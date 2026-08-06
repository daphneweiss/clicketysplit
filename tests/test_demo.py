"""Tests for the bundled demo experiment and ``setup_demo_experiment``.

Covers:

1. ``setup_demo_experiment()`` returns an existing directory with both
   conditions' recordings and stimulus lists in their canonical positions.
2. The returned ``clicketysplit.json`` round-trips through
   :func:`clicketysplit.config.load_config` without errors.
3. ``detect_for_condition`` runs end-to-end against each demo condition
   and produces word-typed segments — proves the WAVs are real, the
   detector is wired up, and the labeling thresholds are compatible.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clicketysplit.config import ExperimentConfig, load_config
from clicketysplit.demo_assets import setup_demo_experiment
from clicketysplit.detection.pipeline import detect_for_condition

DEMO_CONDITIONS = ("real_words", "pseudowords")


@pytest.fixture
def demo_experiment() -> Path:
    """Build a fresh demo experiment directory for the test.

    ``setup_demo_experiment`` writes to ``tempfile.mkdtemp`` so each test
    gets its own copy of the bundled assets and there is no cross-test
    contamination via shared state.
    """
    return setup_demo_experiment()


def test_setup_demo_experiment_creates_expected_layout(demo_experiment: Path) -> None:
    """The returned dir has clicketysplit.json + both recordings + lists."""
    assert demo_experiment.is_dir()

    config_path = demo_experiment / "clicketysplit.json"
    assert config_path.is_file(), f"missing {config_path}"

    for cond in DEMO_CONDITIONS:
        rec_path = demo_experiment / "recordings" / "demo_speaker" / cond / "rec.wav"
        stim_path = demo_experiment / "stimulus_lists" / f"{cond}.txt"
        assert rec_path.is_file(), f"missing {rec_path}"
        assert stim_path.is_file(), f"missing {stim_path}"
        # The WAV must be non-empty and the stimulus list must contain
        # non-blank lines (load_config would otherwise reject it via
        # validate_disk_refs).
        assert rec_path.stat().st_size > 0
        assert any(line.strip() for line in stim_path.read_text().splitlines())


def test_demo_config_loads(demo_experiment: Path) -> None:
    """``load_config`` accepts the demo's clicketysplit.json end-to-end."""
    cfg = load_config(demo_experiment / "clicketysplit.json")
    assert isinstance(cfg, ExperimentConfig)
    assert cfg.name == "clicketysplit demo"
    assert [s.id for s in cfg.speakers] == ["demo_speaker"]
    assert [c.name for c in cfg.conditions] == list(DEMO_CONDITIONS)
    for cond in cfg.conditions:
        # The demo clips are massed repetitions of each word, ~2 per word —
        # the recording style the auto-labeler was built for.
        assert cond.presentation_order == "blocked"
        assert cond.expected_reps_per_stimulus == 2
    # The demo picks the best installed backend (silero > webrtc > energy)
    # so it always boots on a bare install but uses the real detector when
    # the extras are present.
    from clicketysplit.detection import available_detectors

    assert cfg.detection.backend in available_detectors()
    assert cfg.detection.denoise is False


@pytest.mark.parametrize("condition", DEMO_CONDITIONS)
def test_demo_detection_runs_end_to_end(demo_experiment: Path, condition: str) -> None:
    """``detect_for_condition`` returns a non-empty proposal without errors."""
    cfg = load_config(demo_experiment / "clicketysplit.json")
    result = detect_for_condition(cfg, "demo_speaker", condition)

    assert result.segment_count > 0, "expected at least one segment from demo audio"
    assert result.proposed_segments_path.is_file()
    assert result.overview_plot_path.is_file()
    # denoise=False in the demo config, so no denoised wav was written.
    assert result.denoised_audio_path is None


@pytest.mark.parametrize("condition", DEMO_CONDITIONS)
def test_demo_detection_yields_labeled_words(
    demo_experiment: Path, condition: str
) -> None:
    """Each demo clip yields ≥8 word segments, most carrying auto-labels.

    The clips hold 12-18 spoken tokens of real speech; even with a couple
    of boundary hiccups the detector should comfortably clear 8 words, and
    blocked auto-labeling should prefill names from the stimulus list.
    """
    cfg = load_config(demo_experiment / "clicketysplit.json")
    detect_for_condition(cfg, "demo_speaker", condition)

    payload = json.loads(
        (
            demo_experiment / "output" / "demo_speaker" / condition / "proposed_segments.json"
        ).read_text()
    )
    words = [s for s in payload["segments"] if s["segment_type"] == "word"]
    assert len(words) >= 8, (
        f"expected >= 8 word-typed segments from {condition}, got {len(words)} "
        f"(total segments: {len(payload['segments'])})"
    )
    labeled = [w for w in words if w.get("assigned_name")]
    assert len(labeled) >= len(words) // 2, (
        f"expected most word segments to carry auto-labels, "
        f"got {len(labeled)}/{len(words)}"
    )
