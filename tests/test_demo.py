"""Tests for the bundled demo experiment and ``setup_demo_experiment``.

Covers (per task 14):

1. ``setup_demo_experiment()`` returns an existing directory with the
   three expected files in their canonical positions.
2. The returned ``clicketysplit.json`` round-trips through
   :func:`clicketysplit.config.load_config` without errors.
3. ``detect_for_condition`` runs end-to-end against the demo and produces
   a non-empty list of segments.
4. After detection, ``proposed_segments.json`` on disk contains at least
   five word-typed segments — proves the WAV is real, the detector is
   wired up, and the labeling thresholds are compatible.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clicketysplit.config import ExperimentConfig, load_config
from clicketysplit.demo_assets import setup_demo_experiment
from clicketysplit.detection.pipeline import detect_for_condition


@pytest.fixture
def demo_experiment() -> Path:
    """Build a fresh demo experiment directory for the test.

    ``setup_demo_experiment`` writes to ``tempfile.mkdtemp`` so each test
    gets its own copy of the bundled assets and there is no cross-test
    contamination via shared state.
    """
    return setup_demo_experiment()


def test_setup_demo_experiment_creates_expected_layout(demo_experiment: Path) -> None:
    """The returned dir has clicketysplit.json + recording + stimulus list."""
    assert demo_experiment.is_dir()

    config_path = demo_experiment / "clicketysplit.json"
    rec_path = (
        demo_experiment
        / "recordings"
        / "demo_speaker"
        / "demo_condition"
        / "rec.wav"
    )
    stim_path = demo_experiment / "stimulus_lists" / "demo.txt"

    assert config_path.is_file(), f"missing {config_path}"
    assert rec_path.is_file(), f"missing {rec_path}"
    assert stim_path.is_file(), f"missing {stim_path}"

    # The WAV must be non-empty.
    assert rec_path.stat().st_size > 0
    # The stimulus list must contain non-blank lines (load_config would
    # otherwise reject it via validate_disk_refs).
    assert any(line.strip() for line in stim_path.read_text().splitlines())


def test_demo_config_loads(demo_experiment: Path) -> None:
    """``load_config`` accepts the demo's clicketysplit.json end-to-end.

    This also exercises the three-phase load (CONTRACT_NOTES C5):
    pydantic validation, ``_config_dir`` anchoring, and
    ``validate_disk_refs`` against the on-disk stimulus list.
    """
    cfg = load_config(demo_experiment / "clicketysplit.json")
    assert isinstance(cfg, ExperimentConfig)
    assert cfg.name == "clicketysplit demo"
    assert [s.id for s in cfg.speakers] == ["demo_speaker"]
    assert [c.name for c in cfg.conditions] == ["demo_condition"]
    assert cfg.conditions[0].presentation_order == "random"
    assert cfg.detection.backend == "energy"
    assert cfg.detection.denoise is False


def test_demo_detection_runs_end_to_end(demo_experiment: Path) -> None:
    """``detect_for_condition`` returns a non-empty proposal without errors."""
    cfg = load_config(demo_experiment / "clicketysplit.json")
    result = detect_for_condition(cfg, "demo_speaker", "demo_condition")

    assert result.segment_count > 0, "expected at least one segment from demo audio"
    assert result.proposed_segments_path.is_file()
    assert result.overview_plot_path.is_file()
    # denoise=False in the demo config, so no denoised wav was written.
    assert result.denoised_audio_path is None


def test_demo_detection_yields_word_segments(demo_experiment: Path) -> None:
    """proposed_segments.json has at least 5 word-typed segments.

    The bundled WAV contains ~10 spoken tokens; even with TTS occasionally
    splitting a word into two bursts and a generous safety margin, we
    expect at least 5 segments to clear the word-duration threshold.
    """
    cfg = load_config(demo_experiment / "clicketysplit.json")
    detect_for_condition(cfg, "demo_speaker", "demo_condition")

    payload = json.loads(
        (
            demo_experiment
            / "output"
            / "demo_speaker"
            / "demo_condition"
            / "proposed_segments.json"
        ).read_text()
    )
    word_segments = [s for s in payload["segments"] if s["segment_type"] == "word"]
    assert len(word_segments) >= 5, (
        f"expected >= 5 word-typed segments from demo audio, "
        f"got {len(word_segments)} "
        f"(total segments: {len(payload['segments'])})"
    )
