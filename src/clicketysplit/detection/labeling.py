"""Segment typing and forward-walk auto-labeling.

See 03_AUDIO_AND_DETECTION.md "Labeling" for the algorithm and worked
examples. The Python implementation and the TS port in the review UI
share ``tests/labeling_test_vectors.json`` so they stay in sync.

``LabelAnchor`` lives in :mod:`.base` (re-exported here for callers that
prefer to import everything labeling-related from this module).
"""

from __future__ import annotations

from typing import Literal

from .base import LabelAnchor, LabeledSegment, ProposedSegment

__all__ = [
    "LabelAnchor",
    "auto_label",
    "classify_segments",
]


# ---------------------------------------------------------------------------
# Typing
# ---------------------------------------------------------------------------


def _detect_intro_block(segments: list[ProposedSegment]) -> int:
    """Return the number of leading segments that look like an intro block.

    The legacy pipeline's heuristic: scan the first few inter-segment gaps,
    find the largest, and treat it as an intro/recording-start boundary if
    it is markedly larger than the median later gap. Only used when the
    caller passes ``drop_intro_block=True``.
    """
    n_seg = len(segments)
    if n_seg < 3:
        return 0

    gaps: list[float] = []
    for i in range(1, n_seg):
        gaps.append(segments[i].start - segments[i - 1].end)

    if len(gaps) < 2:
        return 0

    # Limit scan to the first ~8 segments (intro is at most a few utterances).
    scan_range = max(3, min(8, n_seg // 10))
    early_gaps = gaps[:scan_range]
    if not early_gaps:
        return 0
    max_early_idx = max(range(len(early_gaps)), key=lambda i: early_gaps[i])
    max_early_gap = early_gaps[max_early_idx]
    later_gaps = gaps[scan_range:] if len(gaps) > scan_range else gaps
    if not later_gaps:
        return 0
    sorted_later = sorted(later_gaps)
    median_later = sorted_later[len(sorted_later) // 2]

    if max_early_gap > median_later * 1.5 and max_early_gap > 0.6:
        return max_early_idx + 1
    return 0


def classify_segments(
    segments: list[ProposedSegment],
    *,
    min_word_duration_ms: int = 250,
    max_word_duration_ms: int = 1400,
    drop_intro_block: bool = False,
) -> list[LabeledSegment]:
    """Type-classify segments by duration, optionally flagging an intro block.

    Per 03 doc: ``short_noise`` < ``min_word_duration_ms``, ``word`` in
    ``[min, max]``, ``crosstalk`` > ``max_word_duration_ms``. The intro
    heuristic stays in the codebase per 03 doc "What gets dropped" but
    defaults off. ``assigned_name`` and ``label_source`` are left blank
    here — :func:`auto_label` fills them in for word-typed segments.
    """
    intro_count = _detect_intro_block(segments) if drop_intro_block else 0

    out: list[LabeledSegment] = []
    for i, seg in enumerate(segments):
        if i < intro_count:
            out.append(
                LabeledSegment(
                    start=seg.start,
                    end=seg.end,
                    duration_ms=seg.duration_ms,
                    segment_type="intro",
                    assigned_name="",
                    label_source="",
                    status="intro",
                )
            )
            continue

        if seg.duration_ms < min_word_duration_ms:
            stype: Literal["word", "short_noise", "crosstalk", "intro"] = "short_noise"
        elif seg.duration_ms > max_word_duration_ms:
            stype = "crosstalk"
        else:
            stype = "word"

        out.append(
            LabeledSegment(
                start=seg.start,
                end=seg.end,
                duration_ms=seg.duration_ms,
                segment_type=stype,
                assigned_name="",
                label_source="",
                status="pending",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Auto-labeling (forward walk from anchors)
# ---------------------------------------------------------------------------


def _word_indices(segments: list[LabeledSegment]) -> list[int]:
    """Absolute segment indices of word-typed segments, in order."""
    return [i for i, s in enumerate(segments) if s.segment_type == "word"]


def _validate_anchors(
    anchors: list[LabelAnchor], n_words: int, stimulus_list: list[str]
) -> list[LabelAnchor]:
    """Sort anchors by word_index; drop out-of-range or unknown-label anchors.

    A label of ``""`` is allowed (it halts forward labeling per spec). Any
    other label must be a member of ``stimulus_list`` — otherwise we silently
    drop the anchor, matching the spec's "anchors override at their own
    index" without letting a typo poison the walk.
    """
    valid: list[LabelAnchor] = []
    stim_set = set(stimulus_list)
    for a in anchors:
        if a.word_index < 0 or a.word_index >= n_words:
            continue
        if a.label != "" and a.label not in stim_set:
            continue
        valid.append(a)
    # Stable sort by word_index; if duplicates exist later anchors win.
    valid.sort(key=lambda a: a.word_index)
    deduped: dict[int, LabelAnchor] = {}
    for a in valid:
        deduped[a.word_index] = a
    return sorted(deduped.values(), key=lambda a: a.word_index)


def auto_label(
    segments: list[LabeledSegment],
    stimulus_list: list[str],
    *,
    presentation_order: Literal["random", "cycled", "blocked"],
    expected_reps_per_stimulus: int = 3,
    anchors: list[LabelAnchor] | None = None,
) -> list[LabeledSegment]:
    """Apply tentative auto-labels to word-typed segments.

    Non-word segments are returned unchanged. The algorithm matches the
    worked examples in 03_AUDIO_AND_DETECTION.md:

    - ``random``: only anchored word indices get a label
      (``label_source="anchor"``); everything else stays blank.
    - ``cycled``: walk forward stride-1 through ``stimulus_list`` from the
      most recent anchor. Between two anchors, hold the earlier anchor's
      stimulus position until the later anchor's index, then jump.
    - ``blocked``: walk forward through ``stimulus_list``, advancing to the
      next stimulus once ``expected_reps_per_stimulus`` consecutive tokens
      have been assigned the current stimulus. **The anchored token is
      rep 1**, not rep 0 (per task brief). Walks wrap back to
      ``stimulus_list[0]`` after the last stimulus, so an extra rep past
      the end of the list does not break labeling. Between two anchors,
      the earlier anchor's stimulus is held until the later anchor's index.

    Anchors with ``label == ""`` halt forward labeling — every word-typed
    segment from that index onward stays blank until the next anchor.

    For ``blocked`` and ``cycled`` with no anchor at ``word_index == 0``,
    an implicit ``LabelAnchor(0, stimulus_list[0], "initial")`` is added.
    """
    segments = [
        LabeledSegment(
            start=s.start,
            end=s.end,
            duration_ms=s.duration_ms,
            segment_type=s.segment_type,
            assigned_name=s.assigned_name,
            label_source=s.label_source,
            status=s.status,
            token_index=s.token_index,
            cluster_size=s.cluster_size,
        )
        for s in segments
    ]

    word_idx = _word_indices(segments)
    n_words = len(word_idx)

    # Reset any previous auto-labels on word-typed segments before re-walking.
    for i in word_idx:
        segments[i].assigned_name = ""
        segments[i].label_source = ""

    if n_words == 0:
        return segments

    anchors = list(anchors) if anchors is not None else []
    anchors = _validate_anchors(anchors, n_words, stimulus_list)

    # Project anchors onto their word-typed segments first; they always win
    # at their own index regardless of presentation_order.
    anchor_by_wi: dict[int, LabelAnchor] = {a.word_index: a for a in anchors}
    for a in anchors:
        seg_idx = word_idx[a.word_index]
        segments[seg_idx].assigned_name = a.label
        segments[seg_idx].label_source = "anchor"

    if presentation_order == "random":
        # No forward walk; anchored labels above are the entire output.
        return segments

    if not stimulus_list:
        return segments

    # Add an implicit anchor at index 0 if missing — see 03 doc and brief.
    if 0 not in anchor_by_wi:
        implicit = LabelAnchor(word_index=0, label=stimulus_list[0], source="initial")
        anchors = sorted([implicit, *anchors], key=lambda a: a.word_index)
        anchor_by_wi[0] = implicit
        segments[word_idx[0]].assigned_name = implicit.label
        segments[word_idx[0]].label_source = "anchor"

    if presentation_order == "cycled":
        _walk_cycled(segments, word_idx, anchors, stimulus_list)
    elif presentation_order == "blocked":
        _walk_blocked(
            segments, word_idx, anchors, stimulus_list, expected_reps_per_stimulus
        )
    else:
        raise ValueError(f"Unknown presentation_order: {presentation_order!r}")

    return segments


def _next_anchor_index(anchors: list[LabelAnchor], after_wi: int) -> int | None:
    """Return the word_index of the first anchor strictly after ``after_wi``."""
    for a in anchors:
        if a.word_index > after_wi:
            return a.word_index
    return None


def _walk_cycled(
    segments: list[LabeledSegment],
    word_idx: list[int],
    anchors: list[LabelAnchor],
    stimulus_list: list[str],
) -> None:
    n_words = len(word_idx)
    n_stim = len(stimulus_list)

    for a_pos, a in enumerate(anchors):
        if a.label == "":
            # Empty anchor halts; everything up to the next anchor stays "".
            continue
        try:
            base_stim_idx = stimulus_list.index(a.label)
        except ValueError:
            continue

        next_a_wi = anchors[a_pos + 1].word_index if a_pos + 1 < len(anchors) else None

        for wi in range(a.word_index + 1, n_words):
            if next_a_wi is not None and wi >= next_a_wi:
                break
            if next_a_wi is not None:
                # Between two anchors: hold the earlier anchor's stimulus
                # rather than stride forward. The next anchor will set the
                # label at its own index. Matches the cycled worked example.
                segments[word_idx[wi]].assigned_name = a.label
                segments[word_idx[wi]].label_source = "auto"
            else:
                stride = wi - a.word_index
                segments[word_idx[wi]].assigned_name = stimulus_list[
                    (base_stim_idx + stride) % n_stim
                ]
                segments[word_idx[wi]].label_source = "auto"


def _walk_blocked(
    segments: list[LabeledSegment],
    word_idx: list[int],
    anchors: list[LabelAnchor],
    stimulus_list: list[str],
    k: int,
) -> None:
    n_words = len(word_idx)
    n_stim = len(stimulus_list)
    k = max(1, int(k))

    for a_pos, a in enumerate(anchors):
        if a.label == "":
            continue
        try:
            base_stim_idx = stimulus_list.index(a.label)
        except ValueError:
            continue

        next_a_wi = anchors[a_pos + 1].word_index if a_pos + 1 < len(anchors) else None

        # The anchored token is rep 1 of base_stim (per task brief).
        current_stim_idx = base_stim_idx
        count = 1

        for wi in range(a.word_index + 1, n_words):
            if next_a_wi is not None and wi >= next_a_wi:
                break
            if next_a_wi is not None:
                # Between anchors: hold the anchor's stimulus the whole way
                # rather than K-advancing, so the next anchor can take over
                # cleanly. K only governs the trailing region past the last
                # anchor (the "speaker did an extra rep" wrap case).
                segments[word_idx[wi]].assigned_name = a.label
                segments[word_idx[wi]].label_source = "auto"
                continue

            if count >= k:
                current_stim_idx = (current_stim_idx + 1) % n_stim
                count = 0
            segments[word_idx[wi]].assigned_name = stimulus_list[current_stim_idx]
            segments[word_idx[wi]].label_source = "auto"
            count += 1
