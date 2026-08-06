"""Optional ``noisereduce`` wrapper.

Spectral-gating noise reduction lifted 1:1 from the legacy pipeline's
``reduce_background_noise`` from the original lab pipeline.
The noise profile is estimated from the quietest ``noise_floor_fraction`` of
short frames (legacy hardcoded 0.2), and ``noisereduce.reduce_noise`` is then
called in stationary mode against that profile.

The dependency on ``noisereduce`` is **optional**. The module imports cleanly
without it — :data:`HAS_NOISEREDUCE` reports availability, and
:func:`denoise_audio` raises ``RuntimeError`` with an install hint (matching
the phrasing of ``audio_io``'s ffmpeg error) if called when the library is
missing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from numpy.typing import NDArray


try:
    import noisereduce as _nr  # noqa: F401

    HAS_NOISEREDUCE: bool = True
except ImportError:
    HAS_NOISEREDUCE = False


def denoise_audio(
    audio: NDArray[np.float32],
    sr: int,
    *,
    noise_floor_fraction: float = 0.2,
) -> NDArray[np.float32]:
    """Apply spectral-gating noise reduction to a mono float32 recording.

    The noise profile is estimated from the quietest
    ``noise_floor_fraction`` of 50 ms frames (silence between words), then
    ``noisereduce.reduce_noise`` is run in stationary mode against it.

    Parameters
    ----------
    audio:
        Mono float32 1D array. ``audio_io.load_audio`` always returns this
        shape; passing anything else is an error.
    sr:
        Sample rate of ``audio`` in Hz.
    noise_floor_fraction:
        Fraction of frames (by RMS) treated as the silence/noise profile.
        Legacy default: 0.2.

    Returns
    -------
    A mono float32 1D array with the same length and sample rate. If the
    audio is too short or the noise profile cannot be isolated, the input
    is returned unchanged (matching the legacy behavior of falling back
    when there isn't enough material to estimate from).

    Raises
    ------
    RuntimeError
        If ``noisereduce`` is not installed.
    ValueError
        If ``audio`` is not a 1D array (``audio_io.load_audio`` returns 1D,
        so callers should never hit this in practice).
    """
    # Lazy-import so importing this module does not fail without the extra.
    if not HAS_NOISEREDUCE:
        raise RuntimeError(
            "Denoising requires the 'noisereduce' library. "
            "Install with: pip install 'clicketysplit[denoise]'"
        )
    import noisereduce as nr

    if audio.ndim != 1:
        raise ValueError(
            f"denoise_audio expects a 1D mono array, got shape {audio.shape}. "
            f"Use audio_io.load_audio (which always returns mono) or downmix first."
        )

    if not 0.0 < noise_floor_fraction < 1.0:
        raise ValueError(
            f"noise_floor_fraction must be in (0, 1), got {noise_floor_fraction!r}"
        )

    audio_f32 = np.ascontiguousarray(audio, dtype=np.float32)

    # Frame the audio into 50 ms windows and pick the quietest fraction as
    # the noise profile (legacy logic — works well for speech with clear
    # gaps between utterances).
    frame_len = int(0.05 * sr)
    if frame_len <= 0:
        return audio_f32
    n_frames = len(audio_f32) // frame_len
    if n_frames < 10:
        # Too short to reliably estimate — leave unchanged (legacy fallback).
        return audio_f32

    frame_rms = np.array(
        [
            np.sqrt(
                np.mean(audio_f32[i * frame_len : (i + 1) * frame_len] ** 2)
            )
            for i in range(n_frames)
        ],
        dtype=np.float64,
    )

    threshold = float(np.percentile(frame_rms, noise_floor_fraction * 100.0))
    noise_frames = [
        audio_f32[i * frame_len : (i + 1) * frame_len]
        for i in range(n_frames)
        if frame_rms[i] <= threshold
    ]
    if len(noise_frames) < 3:
        # Couldn't isolate a noise profile — leave unchanged (legacy fallback).
        return audio_f32

    noise_clip = np.concatenate(noise_frames).astype(np.float32, copy=False)

    reduced = nr.reduce_noise(
        y=audio_f32,
        sr=sr,
        y_noise=noise_clip,
        stationary=True,
        prop_decrease=0.85,
        n_fft=2048,
        freq_mask_smooth_hz=200,
    )
    return np.ascontiguousarray(reduced, dtype=np.float32)
