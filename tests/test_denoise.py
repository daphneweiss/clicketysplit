"""Tests for ``clicketysplit.denoise``."""

from __future__ import annotations

import numpy as np
import pytest

from clicketysplit import denoise
from clicketysplit.denoise import HAS_NOISEREDUCE, denoise_audio


def _noisy_speech(duration_sec: float, sr: int, seed: int = 0) -> np.ndarray:
    """Synthesize a noisy "speech" signal with clear silence regions.

    A short tone burst around 220 Hz with low-amplitude wideband noise
    everywhere — gives denoise_audio enough material to estimate a noise
    profile from quietest frames.
    """
    rng = np.random.default_rng(seed)
    n = int(round(duration_sec * sr))
    noise = (0.01 * rng.standard_normal(n)).astype(np.float32)

    t = np.arange(n) / sr
    burst_start = int(0.4 * sr)
    burst_end = int(0.9 * sr)
    audio = noise.copy()
    audio[burst_start:burst_end] += (
        0.4 * np.sin(2.0 * np.pi * 220.0 * t[burst_start:burst_end])
    ).astype(np.float32)
    return audio.astype(np.float32)


def test_has_noisereduce_true() -> None:
    # Task brief requires HAS_NOISEREDUCE be True in this venv.
    assert HAS_NOISEREDUCE is True


def test_denoise_audio_returns_same_shape_and_dtype() -> None:
    sr = 16000
    audio = _noisy_speech(2.0, sr)
    out = denoise_audio(audio, sr)
    assert out.dtype == np.float32
    assert out.ndim == 1
    assert out.shape == audio.shape
    # SNR is brittle to assert; just confirm the wrapper actually modified
    # the signal rather than handing back the input array unchanged.
    assert not np.array_equal(out, audio)


def test_denoise_audio_raises_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(denoise, "HAS_NOISEREDUCE", False)
    audio = np.zeros(1024, dtype=np.float32)
    with pytest.raises(RuntimeError) as exc_info:
        denoise_audio(audio, 16000)
    assert str(exc_info.value) == (
        "Denoising requires the 'noisereduce' library. "
        "Install with: pip install 'clicketysplit[denoise]'"
    )


def test_denoise_audio_rejects_stereo() -> None:
    sr = 16000
    stereo = np.zeros((sr, 2), dtype=np.float32)
    with pytest.raises(ValueError) as exc_info:
        denoise_audio(stereo, sr)
    # ValueError must reference the offending shape so the user knows what
    # to fix without diving into the source.
    assert "1D" in str(exc_info.value) or "mono" in str(exc_info.value)


def test_denoise_audio_short_input_passthrough() -> None:
    # Fewer than 10 50ms frames at 16 kHz → 8000 samples threshold.
    # Legacy fallback: return input unchanged rather than erroring.
    sr = 16000
    audio = np.zeros(int(0.3 * sr), dtype=np.float32)  # 0.3s = 6 frames at 50ms
    out = denoise_audio(audio, sr)
    assert out.shape == audio.shape
    assert np.array_equal(out, audio)


def test_denoise_audio_rejects_bad_fraction() -> None:
    sr = 16000
    audio = _noisy_speech(1.0, sr)
    with pytest.raises(ValueError):
        denoise_audio(audio, sr, noise_floor_fraction=0.0)
    with pytest.raises(ValueError):
        denoise_audio(audio, sr, noise_floor_fraction=1.0)
