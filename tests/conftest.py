"""Shared pytest fixtures for the clicketysplit test suite.

Shared fixtures for the test suite. They
are additive — existing tests bring their own ad-hoc fixtures and continue
to use them; the helpers here exist for future tests that want the
documented shape.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf
from numpy.typing import NDArray


@pytest.fixture
def experiment_dir(tmp_path: Path) -> Path:
    """Return a fresh empty directory suitable for use as an experiment root."""
    exp = tmp_path / "experiment"
    exp.mkdir()
    return exp


@pytest.fixture
def tiny_audio() -> tuple[NDArray[np.float32], int]:
    """3 second mono 16 kHz audio with 3 word bursts at known offsets.

    Bursts are 0.2 s of 440 Hz sine at amplitude 0.5, placed at 0.3 s, 1.2 s,
    and 2.1 s. Synthetic on purpose — used by audio-IO and envelope tests
    that want exactly-known sample positions. Detection tests want
    :func:`speech_audio` instead: Silero is trained on speech and correctly
    ignores sine tones.
    """
    sr = 16000
    audio = np.zeros(sr * 3, dtype=np.float32)
    burst_len = sr // 5
    for offset_sec in (0.3, 1.2, 2.1):
        i = int(offset_sec * sr)
        audio[i:i + burst_len] = (
            0.5 * np.sin(2 * np.pi * 440 * np.arange(burst_len) / sr)
        ).astype(np.float32)
    return audio, sr


@pytest.fixture(scope="session")
def speech_audio_path() -> Path:
    """Path to a real-speech WAV: the bundled demo clip.

    Detection tests need actual speech — the whole point of the Silero
    backend is that it does not fire on synthetic tones. Reusing the demo
    asset keeps the repo free of a second audio fixture.
    """
    from clicketysplit import demo_assets

    return Path(demo_assets.__file__).parent / "demo_real_words.wav"


@pytest.fixture
def tiny_stereo_audio() -> tuple[NDArray[np.float32], int]:
    """1 second stereo 16 kHz audio: 440 Hz sine in L, low noise in R.

    Used by audio-IO downmix tests that want a known multi-channel signal.
    """
    sr = 16000
    stereo = np.zeros((sr, 2), dtype=np.float32)
    t = np.arange(sr) / sr
    stereo[:, 0] = (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    rng = np.random.default_rng(0)
    stereo[:, 1] = (0.05 * rng.standard_normal(sr)).astype(np.float32)
    return stereo, sr


@pytest.fixture
def tiny_speech_path(
    tmp_path: Path, tiny_audio: tuple[NDArray[np.float32], int]
) -> Path:
    """Write :func:`tiny_audio` to ``tmp_path/tiny_speech.wav`` and return the path."""
    audio, sr = tiny_audio
    path = tmp_path / "tiny_speech.wav"
    sf.write(str(path), audio, sr, subtype="PCM_16")
    return path


@pytest.fixture
def tiny_stimuli_path(tmp_path: Path) -> Path:
    """Write a small stimulus list under ``tmp_path`` and return the path."""
    path = tmp_path / "tiny_stimuli.txt"
    path.write_text("apple\nbanana\ncherry\n", encoding="utf-8")
    return path


def make_minimal_config(
    speakers: list[dict[str, Any]],
    conditions: list[dict[str, Any]],
    **overrides: Any,
) -> dict[str, Any]:
    """Build a ``clicketysplit.json``-shaped dict with sensible defaults.

    ``speakers`` and ``conditions`` are lists of dicts matching the
    :mod:`clicketysplit.config` schema. Top-level keys can be overridden via
    ``**overrides`` (e.g. ``recordings_root="rec"``); nested sub-configs
    (``detection``, ``labeling``, ``export``) accept dict overrides that
    are shallow-merged onto the defaults so callers can tweak a single
    field without restating all the others.
    """
    cfg: dict[str, Any] = {
        "schema_version": 1,
        "name": "test experiment",
        "recordings_root": "recordings",
        "stimulus_lists_root": "stimulus_lists",
        "output_root": "output",
        "speakers": speakers,
        "conditions": conditions,
        "detection": {
            "backend": "silero",
            "vad_threshold": 0.5,
            "min_segment_ms": 150,
            "min_silence_ms": 150,
            "silence_margin_ms": 25,
            "denoise": False,
        },
        "labeling": {
            "min_word_duration_ms": 100,
            "max_word_duration_ms": 1400,
            "drop_intro_block": False,
        },
        "export": {
            "pad_ms": 20,
            "fade_ms": 3,
            "format": "wav",
            "produce_csv": True,
            "produce_textgrid": False,
        },
    }
    for key, value in overrides.items():
        if key in ("detection", "labeling", "export") and isinstance(value, dict):
            cfg[key] = {**cfg[key], **value}
        else:
            cfg[key] = value
    return cfg


@pytest.fixture
def fixture_experiment(tmp_path: Path, speech_audio_path: Path) -> Path:
    """A complete fixture experiment dir for integration / server tests.

    Layout::

        <tmp>/exp/
            clicketysplit.json
            recordings/spk1/cond_a/rec.wav
            stimulus_lists/cond_a.txt
            output/

    The recording is the bundled demo clip — real speech, because Silero
    only fires on real speech. ``presentation_order`` is ``cycled`` so the
    implicit anchor labels the first token ``apple``, giving pipeline tests
    at least one accepted token to export.
    """
    audio, sr = sf.read(str(speech_audio_path), dtype="float32")
    exp = tmp_path / "exp"
    (exp / "recordings" / "spk1" / "cond_a").mkdir(parents=True)
    sf.write(
        str(exp / "recordings" / "spk1" / "cond_a" / "rec.wav"),
        audio,
        sr,
        subtype="PCM_16",
    )
    (exp / "stimulus_lists").mkdir()
    (exp / "stimulus_lists" / "cond_a.txt").write_text(
        "apple\nbanana\ncherry\n", encoding="utf-8"
    )
    (exp / "output").mkdir()

    cfg = make_minimal_config(
        speakers=[{"id": "spk1"}],
        conditions=[
            {
                "name": "cond_a",
                "stimulus_list": "stimulus_lists/cond_a.txt",
                "presentation_order": "cycled",
                "expected_reps_per_stimulus": 3,
            }
        ],
    )
    (exp / "clicketysplit.json").write_text(
        json.dumps(cfg, indent=2), encoding="utf-8"
    )
    return exp
