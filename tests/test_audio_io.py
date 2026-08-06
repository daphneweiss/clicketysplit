"""Tests for clicketysplit.audio_io."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from clicketysplit import audio_io
from clicketysplit.audio_io import (
    SUPPORTED_EXTENSIONS,
    ConcatResult,
    FileBoundary,
    concatenate,
    load_audio,
    save_audio,
)


def _sine(duration_sec: float, freq_hz: float, sr: int) -> np.ndarray:
    t = np.arange(round(duration_sec * sr)) / sr
    return (0.5 * np.sin(2.0 * np.pi * freq_hz * t)).astype(np.float32)


def test_save_then_load_roundtrip_wav(tmp_path: Path) -> None:
    sr = 16000
    audio = _sine(0.5, 440.0, sr)
    path = tmp_path / "sine.wav"

    save_audio(path, audio, sr, format="wav")
    loaded, loaded_sr = load_audio(path)

    assert loaded.dtype == np.float32
    assert loaded.ndim == 1
    assert loaded_sr == sr
    assert loaded.shape == audio.shape
    # WAV float32 round-trip should be near-exact (small libsndfile noise OK).
    assert np.allclose(loaded, audio, atol=1e-4)


def test_stereo_downmix_to_mono(tmp_path: Path) -> None:
    sr = 16000
    left = _sine(0.3, 220.0, sr)
    right = _sine(0.3, 440.0, sr)
    stereo = np.stack([left, right], axis=1).astype(np.float32)
    path = tmp_path / "stereo.wav"
    sf.write(str(path), stereo, sr, format="WAV")

    loaded, loaded_sr = load_audio(path)
    expected = (left + right) / 2.0

    assert loaded.ndim == 1
    assert loaded.dtype == np.float32
    assert loaded_sr == sr
    assert loaded.shape == (left.shape[0],)
    assert np.allclose(loaded, expected, atol=1e-4)


def test_resample_on_load(tmp_path: Path) -> None:
    sr_native = 16000
    sr_target = 8000
    audio = _sine(0.5, 440.0, sr_native)
    path = tmp_path / "sine_16k.wav"
    save_audio(path, audio, sr_native, format="wav")

    loaded, loaded_sr = load_audio(path, target_sr=sr_target)

    assert loaded_sr == sr_target
    assert loaded.dtype == np.float32
    assert loaded.ndim == 1
    expected_len = int(audio.shape[0] * sr_target / sr_native)
    # resample_poly's output length should match N * up/down exactly here.
    assert loaded.shape[0] == expected_len


def test_concatenate_two_files(tmp_path: Path) -> None:
    sr = 16000
    a = _sine(0.3, 330.0, sr)
    b = _sine(0.3, 660.0, sr)
    path_a = tmp_path / "a.wav"
    path_b = tmp_path / "b.wav"
    save_audio(path_a, a, sr)
    save_audio(path_b, b, sr)

    result = concatenate([path_a, path_b], silence_sec=0.1)

    assert isinstance(result, ConcatResult)
    assert result.sample_rate == sr
    assert result.audio.dtype == np.float32
    assert result.audio.ndim == 1

    expected_total_sec = 0.3 + 0.1 + 0.3
    assert result.audio.shape[0] == round(expected_total_sec * sr)

    assert len(result.boundaries) == 2
    b0, b1 = result.boundaries
    assert isinstance(b0, FileBoundary)
    assert b0.path == path_a
    assert b0.offset_sec == pytest.approx(0.0)
    assert b0.duration_sec == pytest.approx(0.3, abs=1e-6)
    assert b1.path == path_b
    assert b1.offset_sec == pytest.approx(0.4, abs=1e-6)
    assert b1.duration_sec == pytest.approx(0.3, abs=1e-6)


def test_concatenate_mismatched_sample_rates(tmp_path: Path) -> None:
    a = _sine(0.2, 440.0, 16000)
    b = _sine(0.2, 440.0, 22050)
    path_a = tmp_path / "a16k.wav"
    path_b = tmp_path / "b22k.wav"
    save_audio(path_a, a, 16000)
    save_audio(path_b, b, 22050)

    with pytest.raises(ValueError) as exc_info:
        concatenate([path_a, path_b])
    msg = str(exc_info.value)
    assert "16000" in msg
    assert "22050" in msg
    assert str(path_a) in msg
    assert str(path_b) in msg


def test_mp3_without_ffmpeg_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(audio_io, "HAS_FFMPEG", False)
    # Ensure .mp3 is treated as a pydub-path extension regardless of the
    # set populated at import time.
    fake_path = tmp_path / "nope.mp3"

    with pytest.raises(RuntimeError) as exc_info:
        load_audio(fake_path)
    expected = (
        f"Cannot load {fake_path.suffix} files: ffmpeg is not on PATH. "
        f"Install ffmpeg from https://ffmpeg.org or "
        f"`pip install clicketysplit[mp3]` and ensure ffmpeg is on PATH."
    )
    assert str(exc_info.value) == expected


def test_save_audio_rejects_mp3(tmp_path: Path) -> None:
    audio = _sine(0.1, 440.0, 16000)
    with pytest.raises(ValueError) as exc_info:
        save_audio(tmp_path / "x.mp3", audio, 16000, format="mp3")
    assert "mp3" in str(exc_info.value).lower()


def test_save_audio_rejects_unknown_format(tmp_path: Path) -> None:
    audio = _sine(0.1, 440.0, 16000)
    with pytest.raises(ValueError):
        save_audio(tmp_path / "x.ogg", audio, 16000, format="ogg")


def test_supported_extensions_contains_wav_and_flac() -> None:
    assert ".wav" in SUPPORTED_EXTENSIONS
    assert ".flac" in SUPPORTED_EXTENSIONS


def test_supported_extensions_mp3_matches_capabilities() -> None:
    # .mp3 must be present iff both pydub and ffmpeg are available.
    expected_mp3_present = audio_io.HAS_PYDUB and audio_io.HAS_FFMPEG
    assert (".mp3" in SUPPORTED_EXTENSIONS) == expected_mp3_present


def test_load_unsupported_extension_raises(tmp_path: Path) -> None:
    fake = tmp_path / "weird.xyz"
    fake.write_bytes(b"not really audio")
    with pytest.raises(ValueError) as exc_info:
        load_audio(fake)
    assert ".xyz" in str(exc_info.value) or "xyz" in str(exc_info.value)


def test_load_no_resample_when_target_matches_native(tmp_path: Path) -> None:
    sr = 22050
    audio = _sine(0.2, 440.0, sr)
    path = tmp_path / "s.wav"
    save_audio(path, audio, sr)
    loaded, loaded_sr = load_audio(path, target_sr=sr)
    assert loaded_sr == sr
    assert loaded.shape == audio.shape


def test_concatenate_single_file(tmp_path: Path) -> None:
    sr = 16000
    a = _sine(0.25, 440.0, sr)
    path = tmp_path / "only.wav"
    save_audio(path, a, sr)
    result = concatenate([path], silence_sec=0.5)
    # No silence should be inserted with a single file.
    assert result.audio.shape[0] == a.shape[0]
    assert len(result.boundaries) == 1
    assert result.boundaries[0].offset_sec == pytest.approx(0.0)


def test_concatenate_empty_list_raises() -> None:
    with pytest.raises(ValueError):
        concatenate([])
