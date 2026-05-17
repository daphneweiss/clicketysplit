"""Tests for ``clicketysplit.detection.pipeline``.

These exercise the full orchestrator end-to-end on a tiny synthetic
experiment laid out under ``tmp_path``. The energy backend is used
throughout because it has no optional deps and produces deterministic
boundaries for clear tone bursts.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from clicketysplit import denoise
from clicketysplit.config import load_config
from clicketysplit.detection.pipeline import (
    ProposalResult,
    detect_for_condition,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _synth_two_word_recording(path: Path, sr: int = 22050) -> None:
    """Write a 3-second WAV with two clear ~850 ms tone bursts.

    The bursts are duration-classified as ``word`` by labeling.py's defaults
    (min_word_duration_ms=250, max_word_duration_ms=1400).
    """
    duration = 3.0
    n = int(sr * duration)
    audio = np.zeros(n, dtype=np.float32)

    def burst(start_sec: float, end_sec: float, freq: float) -> None:
        i0 = int(start_sec * sr)
        i1 = int(end_sec * sr)
        t = np.arange(i0, i1) / sr
        audio[i0:i1] += (0.5 * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)

    burst(0.4, 1.2, 220.0)
    burst(1.8, 2.6, 330.0)

    rng = np.random.default_rng(0)
    audio = (audio + 0.001 * rng.standard_normal(n).astype(np.float32)).astype(
        np.float32
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), audio, sr, subtype="FLOAT")


def _build_experiment(
    root: Path,
    *,
    presentation_order: str = "random",
    denoise: bool = False,
    stimuli: tuple[str, ...] = ("apple",),
    speaker_id: str = "speaker_01",
    speaker_subdir: str | None = None,
    condition_name: str = "cond_a",
    with_audio: bool = True,
) -> Path:
    """Create a minimal experiment directory and return the config path."""
    recordings_root = root / "recordings"
    stim_root = root / "stimulus_lists"
    output_root = root / "output"
    stim_root.mkdir(parents=True, exist_ok=True)

    stim_path = stim_root / f"{condition_name}.txt"
    stim_path.write_text("\n".join(stimuli) + "\n", encoding="utf-8")

    subdir = speaker_subdir or speaker_id
    if with_audio:
        audio_dir = recordings_root / subdir / condition_name
        _synth_two_word_recording(audio_dir / "take1.wav")
    else:
        # Ensure the speaker dir exists but is empty so the "no audio files"
        # path can be exercised deterministically.
        (recordings_root / subdir / condition_name).mkdir(parents=True, exist_ok=True)

    config_data = {
        "schema_version": 1,
        "name": "test experiment",
        "recordings_root": "recordings",
        "stimulus_lists_root": "stimulus_lists",
        "output_root": "output",
        "speakers": [{"id": speaker_id, "subdir": subdir}],
        "conditions": [
            {
                "name": condition_name,
                "stimulus_list": f"stimulus_lists/{condition_name}.txt",
                "presentation_order": presentation_order,
                "expected_reps_per_stimulus": 3,
            }
        ],
        "detection": {
            "backend": "energy",
            "vad_threshold": 0.5,
            "min_segment_ms": 150,
            "min_silence_ms": 150,
            "silence_margin_ms": 25,
            "denoise": denoise,
        },
        "labeling": {
            "min_word_duration_ms": 250,
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

    config_path = root / "clicketysplit.json"
    config_path.write_text(json.dumps(config_data, indent=2), encoding="utf-8")
    output_root.mkdir(parents=True, exist_ok=True)
    return config_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_detect_for_condition_happy_path_random(experiment_dir: Path) -> None:
    config_path = _build_experiment(experiment_dir, presentation_order="random")
    cfg = load_config(config_path)

    result = detect_for_condition(cfg, "speaker_01", "cond_a")

    assert isinstance(result, ProposalResult)
    assert result.speaker_id == "speaker_01"
    assert result.condition == "cond_a"

    expected_json = experiment_dir / "output" / "speaker_01" / "cond_a" / "proposed_segments.json"
    expected_png = experiment_dir / "output" / "speaker_01" / "cond_a" / "overview.png"
    assert result.proposed_segments_path == expected_json
    assert result.overview_plot_path == expected_png
    assert result.denoised_audio_path is None  # denoise=False
    assert result.audio_duration_sec == pytest.approx(3.0, abs=0.05)
    assert result.sample_rate == 22050
    assert result.segment_count >= 1
    assert result.word_segment_count >= 1


def test_proposed_segments_json_shape(experiment_dir: Path) -> None:
    config_path = _build_experiment(experiment_dir, presentation_order="random")
    cfg = load_config(config_path)

    result = detect_for_condition(cfg, "speaker_01", "cond_a")
    data = json.loads(result.proposed_segments_path.read_text(encoding="utf-8"))

    # Required top-level keys per 03_AUDIO_AND_DETECTION.md schema.
    assert data["schema_version"] == 1
    assert data["speaker_id"] == "speaker_01"
    assert data["condition"] == "cond_a"
    assert isinstance(data["source_files"], list) and len(data["source_files"]) == 1
    src = data["source_files"][0]
    # Paths inside the JSON are RELATIVE to the experiment root.
    assert src["path"] == "recordings/speaker_01/cond_a/take1.wav"
    assert src["offset_sec"] == pytest.approx(0.0)
    assert src["duration_sec"] == pytest.approx(3.0, abs=0.05)
    assert "audio_duration_sec" in data
    assert data["sample_rate"] == 22050
    assert data["detector"] == "energy"
    assert isinstance(data["segments"], list)
    assert data["presentation_order"] == "random"
    assert data["label_anchors"] == []  # random → no implicit anchor

    # Each segment carries the documented fields.
    for seg in data["segments"]:
        assert {"start", "end", "duration_ms", "segment_type", "assigned_name",
                "label_source", "status", "token_index", "cluster_size"} <= set(seg)


def test_overview_png_is_written_and_non_empty(experiment_dir: Path) -> None:
    config_path = _build_experiment(experiment_dir)
    cfg = load_config(config_path)
    result = detect_for_condition(cfg, "speaker_01", "cond_a")
    assert result.overview_plot_path.is_file()
    assert result.overview_plot_path.stat().st_size > 1024  # > 1 KB PNG


def test_denoise_enabled_writes_denoised_wav(experiment_dir: Path) -> None:
    assert denoise.HAS_NOISEREDUCE, (
        "Test requires noisereduce; install via pip install '.[denoise]'"
    )
    config_path = _build_experiment(experiment_dir, denoise=True)
    cfg = load_config(config_path)
    result = detect_for_condition(cfg, "speaker_01", "cond_a")

    assert result.denoised_audio_path is not None
    assert result.denoised_audio_path.is_file()

    data = json.loads(result.proposed_segments_path.read_text(encoding="utf-8"))
    # JSON path is relative to the experiment root and lives under output/.
    assert data["denoised_audio"] is not None
    assert data["denoised_audio"].startswith("output/")
    assert data["denoised_audio"].endswith("/denoised.wav")


def test_unknown_speaker_raises_value_error(experiment_dir: Path) -> None:
    config_path = _build_experiment(experiment_dir)
    cfg = load_config(config_path)
    with pytest.raises(ValueError) as exc_info:
        detect_for_condition(cfg, "speaker_99", "cond_a")
    assert "speaker_99" in str(exc_info.value)


def test_unknown_condition_raises_value_error(experiment_dir: Path) -> None:
    config_path = _build_experiment(experiment_dir)
    cfg = load_config(config_path)
    with pytest.raises(ValueError) as exc_info:
        detect_for_condition(cfg, "speaker_01", "cond_zzz")
    assert "cond_zzz" in str(exc_info.value)


def test_missing_audio_files_raises_value_error(experiment_dir: Path) -> None:
    config_path = _build_experiment(experiment_dir, with_audio=False)
    cfg = load_config(config_path)
    with pytest.raises(ValueError) as exc_info:
        detect_for_condition(cfg, "speaker_01", "cond_a")
    msg = str(exc_info.value)
    # Error must name the directory the orchestrator looked at so the user
    # can fix the layout without spelunking through the source.
    assert "cond_a" in msg or "speaker_01" in msg


def test_cycled_uses_implicit_anchor_and_labels_first_word(experiment_dir: Path) -> None:
    config_path = _build_experiment(
        experiment_dir,
        presentation_order="cycled",
        stimuli=("apple", "banana"),
    )
    cfg = load_config(config_path)
    result = detect_for_condition(cfg, "speaker_01", "cond_a")

    data = json.loads(result.proposed_segments_path.read_text(encoding="utf-8"))
    # Implicit (0, "apple", "initial") anchor must be in the JSON.
    anchors = data["label_anchors"]
    assert len(anchors) == 1
    assert anchors[0] == {"word_index": 0, "label": "apple", "source": "initial"}

    word_segments = [s for s in data["segments"] if s["segment_type"] == "word"]
    assert len(word_segments) >= 1, "Energy detector should find at least one word burst"
    # First word-typed segment must be labeled with the implicit anchor's label.
    assert word_segments[0]["assigned_name"] == "apple"
    assert word_segments[0]["label_source"] == "anchor"


def test_flat_layout_audio_files_are_used(experiment_dir: Path) -> None:
    # Build an experiment but DELETE the condition subdir and drop audio
    # directly under the speaker subdir, to exercise the flat-layout branch.
    config_path = _build_experiment(experiment_dir, with_audio=False)
    speaker_dir = experiment_dir / "recordings" / "speaker_01"
    condition_dir = speaker_dir / "cond_a"
    if condition_dir.exists():
        for p in condition_dir.iterdir():
            p.unlink()
        condition_dir.rmdir()
    _synth_two_word_recording(speaker_dir / "take1.wav")

    cfg = load_config(config_path)
    result = detect_for_condition(cfg, "speaker_01", "cond_a")
    assert result.audio_duration_sec == pytest.approx(3.0, abs=0.05)
