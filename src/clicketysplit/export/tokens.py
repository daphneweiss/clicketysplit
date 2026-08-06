"""WAV-token slicing, fade, naming, and atomic-write export.

The selection rule is
deliberately strict: a segment is exported iff it is a word, has
``status == "accepted"``, and carries a non-empty ``assigned_name``. Every
other segment is counted into a typed ``skipped_*`` bucket so the frontend can
surface why specific tokens were left out.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import numpy as np

from ..audio_io import save_audio

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from ..detection.base import LabeledSegment


_SLUG_BAD = re.compile(r"[^A-Za-z0-9_\-]")


def slugify_label(label: str) -> str:
    """Replace any char outside ``[A-Za-z0-9_-]`` with ``_``.

    No Unicode normalization (NFC/NFD) — accented characters are not folded to
    ASCII, they are replaced with ``_`` like any other non-portable character.
    Empty input returns empty string.
    """
    if not label:
        return ""
    return _SLUG_BAD.sub("_", label)


@dataclass(frozen=True)
class TokenInfo:
    """One exported token's on-disk and timing metadata.

    Times are in seconds in the source audio. ``token_index`` is 1-indexed
    within the (speaker, condition, assigned_name) group, sequential by source
    order — NOT the per-segment ``token_index`` field, which can be stale after
    manual reordering.
    """

    filename: str
    assigned_name: str
    token_index: int
    start_sec: float
    end_sec: float
    duration_ms: float
    padded_start_sec: float
    padded_end_sec: float
    padded_duration_ms: float


@dataclass
class ExportResult:
    """Outcome of an ``export_tokens`` call.

    ``tokens_written`` is in source order. The four ``skipped_*`` counters are
    disjoint: each rejected segment lands in exactly one bucket.
    """

    tokens_written: list[TokenInfo] = field(default_factory=list)
    skipped_not_accepted: int = 0
    skipped_not_word: int = 0
    skipped_unnamed: int = 0
    skipped_empty_slice: int = 0


def _atomic_write_audio(
    final_path: Path,
    audio: NDArray[np.float32],
    sr: int,
    audio_format: str,
) -> None:
    # tmp + fsync + rename prevents half-written files if the browser closes
    # mid-export. POSIX rename is atomic on the same filesystem.
    tmp_path = final_path.with_suffix(final_path.suffix + ".tmp")
    save_audio(tmp_path, audio, sr, format=audio_format)
    with open(tmp_path, "rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp_path, final_path)


def export_tokens(
    audio: NDArray[np.float32],
    sr: int,
    segments: Iterable[LabeledSegment],
    output_dir: Path | str,
    speaker_id: str,
    *,
    pad_ms: int = 20,
    fade_ms: int = 3,
    audio_format: Literal["wav", "flac"] = "wav",
) -> ExportResult:
    """Slice every accepted word segment out of ``audio`` and write to disk.

    Output layout is ``<output_dir>/tokens/<speaker_id>_<slug>-<N>.<ext>``
    where N is 1-indexed within the (speaker, condition, word) group in
    source order. ``output_dir`` is the speaker x condition root — the
    ``tokens/`` subdirectory is created here.

    ``pad_ms`` of context is added to each side (clipped to audio bounds),
    then a linear ``fade_ms`` fade-in/out is applied to suppress edge clicks.
    """
    output_dir = Path(output_dir)
    tokens_dir = output_dir / "tokens"
    tokens_dir.mkdir(parents=True, exist_ok=True)

    pad_samples = round(pad_ms * sr / 1000)
    fade_samples = round(fade_ms * sr / 1000)
    n_samples = audio.shape[0]
    ext = audio_format.lower()

    result = ExportResult()
    per_word_count: dict[str, int] = defaultdict(int)

    for seg in segments:
        if seg.status != "accepted":
            result.skipped_not_accepted += 1
            continue
        if seg.segment_type != "word":
            result.skipped_not_word += 1
            continue
        if not seg.assigned_name:
            result.skipped_unnamed += 1
            continue

        start_sample_raw = round(seg.start * sr)
        end_sample_raw = round(seg.end * sr)
        padded_start = max(0, start_sample_raw - pad_samples)
        padded_end = min(n_samples, end_sample_raw + pad_samples)
        if padded_end <= padded_start:
            result.skipped_empty_slice += 1
            continue

        chunk = np.ascontiguousarray(audio[padded_start:padded_end], dtype=np.float32).copy()
        if fade_samples > 0 and len(chunk) > 2 * fade_samples:
            chunk[:fade_samples] *= np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
            chunk[-fade_samples:] *= np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)

        name = seg.assigned_name
        per_word_count[name] += 1
        token_index = per_word_count[name]

        slug = slugify_label(name)
        filename = f"{speaker_id}_{slug}-{token_index}.{ext}"
        final_path = tokens_dir / filename
        _atomic_write_audio(final_path, chunk, sr, audio_format)

        padded_start_sec = padded_start / sr
        padded_end_sec = padded_end / sr
        result.tokens_written.append(
            TokenInfo(
                filename=filename,
                assigned_name=name,
                token_index=token_index,
                start_sec=float(seg.start),
                end_sec=float(seg.end),
                duration_ms=float((seg.end - seg.start) * 1000.0),
                padded_start_sec=padded_start_sec,
                padded_end_sec=padded_end_sec,
                padded_duration_ms=(padded_end_sec - padded_start_sec) * 1000.0,
            )
        )

    return result
