"""Tests for the experiment config schema and load/save helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from clicketysplit.config import (
    Condition,
    DetectionConfig,
    ExperimentConfig,
    ExportConfig,
    LabelingConfig,
    Speaker,
    load_config,
    save_config,
)


def _write_stimulus_list(path: Path, words: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(words) + "\n", encoding="utf-8")


def _make_valid_experiment(root: Path) -> Path:
    """Build a minimal but valid experiment dir; return the config-file path."""
    (root / "stimulus_lists").mkdir(parents=True, exist_ok=True)
    _write_stimulus_list(root / "stimulus_lists" / "cond_a.txt", ["apple", "banana"])
    _write_stimulus_list(root / "stimulus_lists" / "cond_b.txt", ["cherry", "date"])

    cfg_payload = {
        "schema_version": 1,
        "name": "sample experiment",
        "recordings_root": "recordings",
        "stimulus_lists_root": "stimulus_lists",
        "output_root": "output",
        "speakers": [
            {"id": "speaker_01", "subdir": "speaker_01"},
            {"id": "speaker_02"},
        ],
        "conditions": [
            {
                "name": "cond_a",
                "stimulus_list": "stimulus_lists/cond_a.txt",
                "presentation_order": "random",
            },
            {
                "name": "cond_b",
                "stimulus_list": "stimulus_lists/cond_b.txt",
                "presentation_order": "blocked",
                "expected_reps_per_stimulus": 4,
            },
        ],
    }
    cfg_path = root / "clicketysplit.json"
    cfg_path.write_text(json.dumps(cfg_payload, indent=2), encoding="utf-8")
    return cfg_path


def test_load_valid_config(experiment_dir: Path) -> None:
    cfg_path = _make_valid_experiment(experiment_dir)
    cfg = load_config(cfg_path)

    assert cfg.schema_version == 1
    assert cfg.name == "sample experiment"
    assert cfg.recordings_root == "recordings"
    assert cfg.stimulus_lists_root == "stimulus_lists"
    assert cfg.output_root == "output"
    assert [s.id for s in cfg.speakers] == ["speaker_01", "speaker_02"]
    assert cfg.speakers[1].subdir == "speaker_02"
    assert cfg.conditions[0].presentation_order == "random"
    assert cfg.conditions[1].expected_reps_per_stimulus == 4
    assert cfg._config_dir == cfg_path.parent.resolve()


def test_round_trip_save_load(experiment_dir: Path) -> None:
    _write_stimulus_list(
        experiment_dir / "stimulus_lists" / "only.txt", ["one", "two", "three"]
    )
    original = ExperimentConfig(
        schema_version=1,
        name="round-trip test",
        recordings_root="recordings",
        stimulus_lists_root="stimulus_lists",
        output_root="output",
        speakers=[Speaker(id="alpha"), Speaker(id="beta", subdir="beta_alt")],
        conditions=[
            Condition(
                name="only",
                stimulus_list="stimulus_lists/only.txt",
                presentation_order="cycled",
            )
        ],
        detection=DetectionConfig(backend="silero", denoise=False),
        labeling=LabelingConfig(drop_intro_block=True),
        export=ExportConfig(format="flac", produce_textgrid=True),
    )

    cfg_path = experiment_dir / "clicketysplit.json"
    save_config(original, cfg_path)

    on_disk = json.loads(cfg_path.read_text())
    assert "_config_dir" not in on_disk

    loaded = load_config(cfg_path)

    a = original.model_dump(exclude={"_config_dir"})
    b = loaded.model_dump(exclude={"_config_dir"})
    assert a == b
    assert loaded._config_dir == cfg_path.parent.resolve()


def test_speaker_subdir_defaults_to_id() -> None:
    s = Speaker(id="speaker_42")
    assert s.subdir == "speaker_42"

    s2 = Speaker(id="speaker_42", subdir="custom_dir")
    assert s2.subdir == "custom_dir"


def test_presentation_order_defaults_to_blocked() -> None:
    c = Condition(name="x", stimulus_list="lists/x.txt")
    assert c.presentation_order == "blocked"


def test_expected_reps_per_stimulus_defaults_to_two() -> None:
    # 2 repetitions per word is the legacy stim_pipeline default.
    c = Condition(name="x", stimulus_list="lists/x.txt")
    assert c.expected_reps_per_stimulus == 2


def test_missing_stimulus_list_raises(experiment_dir: Path) -> None:
    cfg_payload = {
        "schema_version": 1,
        "conditions": [
            {
                "name": "missing_cond",
                "stimulus_list": "stimulus_lists/nope.txt",
            }
        ],
    }
    cfg_path = experiment_dir / "clicketysplit.json"
    cfg_path.write_text(json.dumps(cfg_payload), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(cfg_path)
    msg = str(exc_info.value)
    assert "missing_cond" in msg
    assert "stimulus_list" in msg


def test_empty_stimulus_list_file_rejected(experiment_dir: Path) -> None:
    list_path = experiment_dir / "stimulus_lists" / "empty.txt"
    list_path.parent.mkdir(parents=True, exist_ok=True)
    list_path.write_text("\n   \n\t\n", encoding="utf-8")

    cfg_payload = {
        "schema_version": 1,
        "conditions": [
            {
                "name": "empty_cond",
                "stimulus_list": "stimulus_lists/empty.txt",
            }
        ],
    }
    cfg_path = experiment_dir / "clicketysplit.json"
    cfg_path.write_text(json.dumps(cfg_payload), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(cfg_path)
    msg = str(exc_info.value)
    assert "empty_cond" in msg
    assert "empty" in msg.lower()


def test_schema_version_too_new_rejected(experiment_dir: Path) -> None:
    cfg_payload = {"schema_version": 2, "conditions": []}
    cfg_path = experiment_dir / "clicketysplit.json"
    cfg_path.write_text(json.dumps(cfg_payload), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(cfg_path)
    msg = str(exc_info.value)
    assert "newer clicketysplit" in msg
    assert "schema_version" in msg


def test_resolve_without_config_dir_raises() -> None:
    cfg = ExperimentConfig()
    assert cfg._config_dir is None
    with pytest.raises(ValueError) as exc_info:
        cfg.resolve("recordings")
    assert "_config_dir" in str(exc_info.value)


def test_resolve_returns_absolute_under_config_dir(experiment_dir: Path) -> None:
    cfg_path = _make_valid_experiment(experiment_dir)
    cfg = load_config(cfg_path)

    resolved = cfg.resolve("stimulus_lists", "cond_a.txt")
    assert resolved.is_absolute()
    assert resolved == (experiment_dir / "stimulus_lists" / "cond_a.txt").resolve()


def test_invalid_presentation_order_rejected() -> None:
    with pytest.raises(ValidationError):
        Condition(
            name="bad",
            stimulus_list="lists/x.txt",
            presentation_order="foo",  # type: ignore[arg-type]
        )


def test_invalid_detection_backend_rejected() -> None:
    with pytest.raises(ValidationError):
        DetectionConfig(backend="not_a_backend")  # type: ignore[arg-type]


def test_save_excludes_config_dir(experiment_dir: Path) -> None:
    _write_stimulus_list(experiment_dir / "stimulus_lists" / "s.txt", ["w"])
    cfg = ExperimentConfig(
        conditions=[Condition(name="c", stimulus_list="stimulus_lists/s.txt")],
    )
    cfg._config_dir = experiment_dir.resolve()

    out = experiment_dir / "clicketysplit.json"
    save_config(cfg, out)

    data = json.loads(out.read_text())
    assert "_config_dir" not in data
    assert data["conditions"][0]["name"] == "c"


def test_validate_disk_refs_requires_config_dir() -> None:
    cfg = ExperimentConfig(
        conditions=[Condition(name="c", stimulus_list="x.txt")],
    )
    with pytest.raises(ValueError) as exc_info:
        cfg.validate_disk_refs()
    assert "_config_dir" in str(exc_info.value)
