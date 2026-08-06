"""Opt-in Praat TextGrid export, gated by the ``[praat]`` extra.

Three tiers:

1. ``tokens`` — interval tier over exported word tokens; label is ``assigned_name``.
2. ``all_segments`` — interval tier over every segment; label is ``segment_type``.
3. ``file_boundaries`` — point tier marking offsets between source recordings.

The TextGrid sits at the condition root (alongside ``proposed_segments.json``),
NOT inside ``tokens/``.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

try:
    import parselmouth  # type: ignore[import-not-found]
    from parselmouth.praat import call  # type: ignore[import-not-found]

    HAS_PARSELMOUTH: bool = True
except ImportError:
    HAS_PARSELMOUTH = False

if TYPE_CHECKING:
    from ..audio_io import FileBoundary
    from ..detection.base import LabeledSegment
    from .tokens import TokenInfo


def write_textgrid(
    audio_duration_sec: float,
    tokens: Iterable[TokenInfo],
    all_segments: Iterable[LabeledSegment],
    file_boundaries: Iterable[FileBoundary],
    path: Path | str,
) -> None:
    """Write a 3-tier TextGrid describing the source audio.

    Raises ``RuntimeError`` if ``praat-parselmouth`` is not installed.
    """
    if not HAS_PARSELMOUTH:
        raise RuntimeError(
            "write_textgrid requires praat-parselmouth. "
            "Install with `pip install clicketysplit[praat]`."
        )

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    tier_names = "tokens all_segments file_boundaries"
    point_tiers = "file_boundaries"
    tg = call(
        "Create TextGrid",
        0.0,
        float(audio_duration_sec),
        tier_names,
        point_tiers,
    )

    # Tier 1: tokens (intervals over exported word tokens). Sort by start so
    # 'Insert boundary' calls land in time order — parselmouth's Insert
    # boundary is happy with any order, but the resulting interval indices
    # are easier to reason about this way.
    sorted_tokens = sorted(tokens, key=lambda t: t.start_sec)
    for t in sorted_tokens:
        start = _clip(t.start_sec, 0.0, audio_duration_sec)
        end = _clip(t.end_sec, 0.0, audio_duration_sec)
        if end <= start:
            continue
        _set_interval(tg, tier_index=1, start=start, end=end, label=t.assigned_name)

    # Tier 2: all_segments (every segment, labeled by segment_type).
    sorted_segments = sorted(all_segments, key=lambda s: s.start)
    for seg in sorted_segments:
        start = _clip(float(seg.start), 0.0, audio_duration_sec)
        end = _clip(float(seg.end), 0.0, audio_duration_sec)
        if end <= start:
            continue
        _set_interval(tg, tier_index=2, start=start, end=end, label=seg.segment_type)

    # Tier 3: file_boundaries (point tier). The first boundary at offset 0 is
    # skipped — the TextGrid already starts there. Insert subsequent file
    # offsets so users can see seam locations in Praat.
    for fb in file_boundaries:
        offset = float(fb.offset_sec)
        if offset <= 0.0 or offset >= audio_duration_sec:
            continue
        call(tg, "Insert point", 3, offset, fb.path.name)

    tg.save(str(out_path))


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _set_interval(tg: object, *, tier_index: int, start: float, end: float, label: str) -> None:
    # Praat refuses to insert a boundary that coincides with an existing one,
    # so we guard each call. The 'Insert boundary' verb splits the interval
    # the boundary falls inside; we then set the text on the right slot.
    eps = 1e-9
    duration = float(call(tg, "Get end time"))
    if start > eps:
        try:
            call(tg, "Insert boundary", tier_index, start)
        except parselmouth.PraatError:
            pass
    if end < duration - eps:
        try:
            call(tg, "Insert boundary", tier_index, end)
        except parselmouth.PraatError:
            pass
    # The interval that starts at `start` is the one we want to label.
    interval_idx = int(call(tg, "Get interval at time", tier_index, (start + end) / 2.0))
    call(tg, "Set interval text", tier_index, interval_idx, label)
