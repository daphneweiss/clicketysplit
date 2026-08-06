"""Segment typing and auto-labeling.

Ports the batch label pass of the original lab tool
(``stim_pipeline/segment_recording.py:classify_and_label``): classify
segments by duration, then assign stimulus names to blocked repetitions by
clustering word candidates on inter-token gaps. The Python implementation
and the TS port in the review UI share
``tests/labeling_test_vectors.json`` so they stay in sync.

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

# Long segments are crosstalk. The original scales the cutoff off the
# recording's own median word length rather than trusting the fixed ceiling,
# so a slow speaker's ordinary words don't get thrown out
# (segment_recording.py:623).
LONG_SEGMENT_FACTOR = 2.2


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolated percentile matching ``numpy.percentile``'s default."""
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * (pct / 100.0)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def _long_segment_threshold(
    durations: list[float], min_word_duration_ms: int, max_word_duration_ms: int
) -> float:
    """Crosstalk cutoff adapted to this recording's typical word length.

    Port of segment_recording.py:614-623: estimate a typical word from the
    interquartile band (so crosstalk can't drag the estimate up), then take
    ``max(max_word_duration_ms, median * LONG_SEGMENT_FACTOR)``.
    """
    if not durations:
        return float(max_word_duration_ms)
    p25 = _percentile(durations, 25)
    p75 = _percentile(durations, 75)
    lo = max(p25, float(min_word_duration_ms))
    word_like = [d for d in durations if lo <= d <= p75]
    median_dur = _median(word_like) if word_like else _median(durations)
    return max(float(max_word_duration_ms), median_dur * LONG_SEGMENT_FACTOR)


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
    min_word_duration_ms: int = 500,
    max_word_duration_ms: int = 1400,
    drop_intro_block: bool = True,
) -> list[LabeledSegment]:
    """Type-classify segments by duration, optionally flagging an intro block.

    ``short_noise`` < ``min_word_duration_ms``; ``crosstalk`` above the
    adaptive long-segment threshold (``max_word_duration_ms`` scaled up when
    the recording's median word runs long — see
    :func:`_long_segment_threshold`); ``word`` in between.
    ``assigned_name`` and ``label_source`` are left blank here —
    :func:`auto_label` fills them in for word-typed segments.
    """
    intro_count = _detect_intro_block(segments) if drop_intro_block else 0

    long_thresh = _long_segment_threshold(
        [s.duration_ms for s in segments[intro_count:]],
        min_word_duration_ms,
        max_word_duration_ms,
    )

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
        elif seg.duration_ms > long_thresh:
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
    expected_reps_per_stimulus: int = 2,
    anchors: list[LabelAnchor] | None = None,
) -> list[LabeledSegment]:
    """Apply tentative auto-labels to word-typed segments.

    Non-word segments are returned unchanged. Per presentation order:

    - ``random``: only anchored word indices get a label
      (``label_source="anchor"``); everything else stays blank.
    - ``cycled``: walk forward stride-1 through ``stimulus_list`` from the
      most recent anchor. Between two anchors, hold the earlier anchor's
      stimulus position until the later anchor's index, then jump.
    - ``blocked``: cluster word tokens on inter-token gaps and assign one
      stimulus per cluster, in list order. ``expected_reps_per_stimulus``
      is a fallback hint, not a hard stride, so a speaker producing an
      extra or missing repetition does not shift every later label. An
      anchor pins its cluster's stimulus; later clusters continue from
      there.

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


def _gap_clusters(
    segments: list[LabeledSegment],
    word_idx: list[int],
    n_stim: int,
    k: int,
) -> list[list[int]]:
    """Split word positions into ``n_stim`` clusters at the largest gaps.

    Speakers pause briefly between repetitions of the same word and longer
    when moving to the next one, so the ``n_stim - 1`` widest gaps are the
    word-switch points. Falls back to fixed ``k``-sized chunks when there
    are too few gaps to split on.

    Returns clusters of *word positions* (indices into ``word_idx``).
    """
    n_words = len(word_idx)
    n_splits = n_stim - 1
    if n_words == 0:
        return []
    if n_splits <= 0:
        return [list(range(n_words))]

    gaps: list[tuple[float, int]] = []
    for wi in range(1, n_words):
        gap = segments[word_idx[wi]].start - segments[word_idx[wi - 1]].end
        gaps.append((gap, wi))

    if len(gaps) < n_splits:
        k = max(1, int(k))
        return [list(range(i, min(i + k, n_words))) for i in range(0, n_words, k)]

    widest = sorted(gaps, key=lambda g: g[0], reverse=True)[:n_splits]
    split_positions = sorted(wi for _, wi in widest)

    clusters: list[list[int]] = []
    prev = 0
    for sp in split_positions:
        clusters.append(list(range(prev, sp)))
        prev = sp
    clusters.append(list(range(prev, n_words)))
    return [c for c in clusters if c]


def _cluster_of(clusters: list[list[int]], word_position: int) -> int | None:
    """Index of the cluster containing ``word_position``, or None."""
    for c_idx, cluster in enumerate(clusters):
        if word_position in cluster:
            return c_idx
    return None


def _walk_blocked(
    segments: list[LabeledSegment],
    word_idx: list[int],
    anchors: list[LabelAnchor],
    stimulus_list: list[str],
    k: int,
) -> None:
    """Label blocked repetitions by clustering on inter-token gaps.

    Consuming exactly ``k`` tokens per stimulus drifts irrecoverably as soon
    as a speaker produces more or fewer repetitions than the script called
    for — every downstream label shifts. Instead we infer cluster boundaries
    from the recording's own timing (see :func:`_gap_clusters`), so ``k`` acts
    as the hint it was always meant to be.

    Anchors still win: an anchor pins its whole cluster to that stimulus, and
    later clusters continue forward through ``stimulus_list`` from there.
    """
    n_words = len(word_idx)
    n_stim = len(stimulus_list)
    if n_words == 0 or n_stim == 0:
        return

    clusters = _gap_clusters(segments, word_idx, n_stim, k)
    if not clusters:
        return

    anchored_positions = {a.word_index for a in anchors if a.label != ""}

    # An anchor re-pins the stimulus for its cluster; clusters after it walk
    # forward from that new position.
    anchor_by_cluster: dict[int, int] = {}
    for a in anchors:
        if a.label == "" or a.label not in stimulus_list:
            continue
        c_idx = _cluster_of(clusters, a.word_index)
        if c_idx is not None:
            anchor_by_cluster[c_idx] = stimulus_list.index(a.label)

    stim_idx = anchor_by_cluster.get(0, 0)
    for c_idx, cluster in enumerate(clusters):
        if c_idx in anchor_by_cluster:
            stim_idx = anchor_by_cluster[c_idx]

        name = stimulus_list[stim_idx] if 0 <= stim_idx < n_stim else ""
        for token_pos, wp in enumerate(cluster):
            seg = segments[word_idx[wp]]
            seg.assigned_name = name
            seg.label_source = "anchor" if wp in anchored_positions else "auto"
            seg.token_index = token_pos + 1
            seg.cluster_size = len(cluster)

        stim_idx += 1

    # Empty-label anchors halt forward labeling: every word from the anchor's
    # index up to the next anchor stays blank (see LabelAnchor in base.py and
    # the auto_label docstring). Applied last so the cluster assignment above
    # cannot overwrite the halted range.
    for a_pos, a in enumerate(anchors):
        if a.label != "":
            continue
        halt_end = (
            anchors[a_pos + 1].word_index if a_pos + 1 < len(anchors) else n_words
        )
        for wp in range(a.word_index, halt_end):
            seg = segments[word_idx[wp]]
            seg.assigned_name = ""
            seg.label_source = "anchor" if wp == a.word_index else ""
            seg.token_index = 0
            seg.cluster_size = 0
