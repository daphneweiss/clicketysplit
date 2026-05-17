"""Audio loading, mono downmix, resampling, concatenation, and format support.

The rest of the codebase (detection, export) consumes audio as
``(np.ndarray[float32, 1D], sr: int)`` and never inspects file extensions or
channel layouts. All format and channel concerns are contained here.

Module-level feature detection runs at import time. The module imports cleanly
even when ``ffmpeg`` and ``pydub`` are absent — capabilities just narrow.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

if TYPE_CHECKING:
    from numpy.typing import NDArray

HAS_FFMPEG: bool = shutil.which("ffmpeg") is not None

try:
    import pydub  # noqa: F401

    HAS_PYDUB: bool = True
except ImportError:
    HAS_PYDUB = False


_PYDUB_EXTENSIONS: frozenset[str] = frozenset({".mp3", ".m4a", ".aac"})


def _build_supported_extensions() -> set[str]:
    # Start with whatever libsndfile exposes (always includes WAV/FLAC/OGG;
    # may include MP3 on some builds — but lossy formats are gated below).
    exts = {f".{fmt.lower()}" for fmt in sf.available_formats()}
    # Lossy formats are *only* supported when we have BOTH pydub and ffmpeg.
    # Strip any lossy extension that snuck in via soundfile, then re-add
    # exactly when both backends are available. This keeps the contract
    # simple: presence of a lossy extension implies the pydub path works.
    exts -= _PYDUB_EXTENSIONS
    if HAS_PYDUB and HAS_FFMPEG:
        exts |= set(_PYDUB_EXTENSIONS)
    return exts


SUPPORTED_EXTENSIONS: set[str] = _build_supported_extensions()


@dataclass(frozen=True)
class FileBoundary:
    """Where a single source file sits inside a concatenated audio stream."""

    path: Path
    offset_sec: float
    duration_sec: float


@dataclass(frozen=True)
class ConcatResult:
    """Result of :func:`concatenate` — joined audio plus per-file offsets."""

    audio: NDArray[np.float32]
    sample_rate: int
    boundaries: list[FileBoundary]


def _ensure_mono_float32(audio: NDArray[np.floating]) -> NDArray[np.float32]:
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    elif audio.ndim != 1:
        raise ValueError(f"Expected 1D or 2D audio array, got shape {audio.shape}")
    return np.ascontiguousarray(audio, dtype=np.float32)


def _resample(audio: NDArray[np.float32], native_sr: int, target_sr: int) -> NDArray[np.float32]:
    if target_sr == native_sr:
        return audio
    resampled = resample_poly(audio, target_sr, native_sr)
    return np.ascontiguousarray(resampled, dtype=np.float32)


def _load_via_pydub(path: Path) -> tuple[NDArray[np.float32], int]:
    # Imported lazily so the module imports cleanly without pydub installed.
    from pydub import AudioSegment

    seg = AudioSegment.from_file(str(path))
    sr = seg.frame_rate
    samples = np.array(seg.get_array_of_samples())
    if seg.channels > 1:
        samples = samples.reshape(-1, seg.channels).mean(axis=1)
    # pydub returns integer PCM scaled to the sample width; normalize to
    # [-1, 1] float32 to match the soundfile path.
    max_amp = float(1 << (8 * seg.sample_width - 1))
    audio = samples.astype(np.float32) / max_amp
    return np.ascontiguousarray(audio, dtype=np.float32), sr


def load_audio(
    path: Path | str, target_sr: int | None = None
) -> tuple[NDArray[np.float32], int]:
    """Load an audio file as mono float32.

    WAV/FLAC/OGG/OPUS are read via soundfile. MP3/M4A/AAC are read via pydub
    (which requires ffmpeg on PATH). Multi-channel input is downmixed to mono
    by averaging across channels. If ``target_sr`` is given and differs from
    the file's native rate, the audio is resampled with ``scipy.signal.resample_poly``.
    """
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix in _PYDUB_EXTENSIONS:
        if not HAS_FFMPEG:
            raise RuntimeError(
                f"Cannot load {path.suffix} files: ffmpeg is not on PATH. "
                f"Install ffmpeg from https://ffmpeg.org or "
                f"`pip install clicketysplit[mp3]` and ensure ffmpeg is on PATH."
            )
        if not HAS_PYDUB:
            raise RuntimeError(
                f"Cannot load {path.suffix} files: pydub is not installed. "
                f"Install with `pip install clicketysplit[mp3]`."
            )
        audio, native_sr = _load_via_pydub(path)
    elif suffix in SUPPORTED_EXTENSIONS:
        raw, native_sr = sf.read(str(path), dtype="float32", always_2d=False)
        audio = _ensure_mono_float32(raw)
    else:
        raise ValueError(
            f"Unsupported audio extension {path.suffix!r}. "
            f"Supported extensions in this environment: "
            f"{sorted(SUPPORTED_EXTENSIONS)}"
        )

    if target_sr is not None and target_sr != native_sr:
        audio = _resample(audio, native_sr, target_sr)
        return audio, target_sr
    return audio, native_sr


def save_audio(
    path: Path | str, audio: NDArray[np.float32], sr: int, format: str = "wav"
) -> None:
    """Write mono float32 audio. Only ``"wav"`` and ``"flac"`` are accepted.

    The caller owns the file extension; ``format`` is passed through to
    ``soundfile.write`` after upper-casing.
    """
    fmt_lower = format.lower()
    if fmt_lower not in {"wav", "flac"}:
        # MP3 encoding is finicky, ffmpeg-dependent, and lossy export is a bad
        # default for a token corpus — see _design/03_AUDIO_AND_DETECTION.md.
        raise ValueError(
            f"save_audio only supports 'wav' or 'flac' (got {format!r}). "
            f"Lossy export is not supported."
        )
    sf.write(str(path), audio, sr, format=fmt_lower.upper())


def concatenate(audio_paths: list[Path] | list[str], silence_sec: float = 0.5) -> ConcatResult:
    """Load and concatenate audio files with ``silence_sec`` of silence between each.

    The first file's sample rate is canonical; any later file with a
    different SR is rejected with a clear error naming both rates and both
    paths. Silence is inserted *between* files only — not before the first
    or after the last.
    """
    if not audio_paths:
        raise ValueError("concatenate() requires at least one audio path")

    paths: list[Path] = [Path(p) for p in audio_paths]

    first_audio, sr_ref = load_audio(paths[0])
    silence = np.zeros(int(round(silence_sec * sr_ref)), dtype=np.float32)

    parts: list[NDArray[np.float32]] = [first_audio]
    boundaries: list[FileBoundary] = [
        FileBoundary(
            path=paths[0],
            offset_sec=0.0,
            duration_sec=len(first_audio) / sr_ref,
        )
    ]
    cursor_samples = len(first_audio)

    for path in paths[1:]:
        audio, sr = load_audio(path)
        if sr != sr_ref:
            raise ValueError(
                f"Sample rate mismatch in concatenate(): "
                f"{paths[0]} is {sr_ref} Hz but {path} is {sr} Hz. "
                f"Resample inputs to a common rate before concatenating."
            )
        parts.append(silence)
        cursor_samples += len(silence)
        offset_sec = cursor_samples / sr_ref
        parts.append(audio)
        boundaries.append(
            FileBoundary(
                path=path,
                offset_sec=offset_sec,
                duration_sec=len(audio) / sr_ref,
            )
        )
        cursor_samples += len(audio)

    combined = np.ascontiguousarray(np.concatenate(parts), dtype=np.float32)
    return ConcatResult(audio=combined, sample_rate=sr_ref, boundaries=boundaries)
