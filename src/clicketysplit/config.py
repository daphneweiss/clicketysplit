"""Experiment configuration schema and load/save helpers.

Pydantic v2 models for ``clicketysplit.json`` plus a three-phase
``load_config`` and a matching ``save_config``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr, model_validator


class DetectionConfig(BaseModel):
    backend: Literal["silero", "webrtc"] = "silero"
    vad_threshold: float = 0.5
    min_segment_ms: int = 150
    # Matches the legacy stim_pipeline defaults that produced good results
    # on real recordings (MIN_SILENCE_MS=150, SILENCE_MARGIN_MS=25).
    min_silence_ms: int = 150
    silence_margin_ms: int = 25
    denoise: bool = True


class LabelingConfig(BaseModel):
    # Legacy stim_pipeline defaults (WORD_DUR_MIN_MS=500, WORD_DUR_MAX_MS=1400),
    # tuned on real lab recordings. Intro-block detection was always on there.
    min_word_duration_ms: int = 500
    max_word_duration_ms: int = 1400
    drop_intro_block: bool = True


class ExportConfig(BaseModel):
    pad_ms: int = 20
    fade_ms: int = 3
    format: Literal["wav", "flac"] = "wav"
    produce_csv: bool = True
    produce_textgrid: bool = False


class Speaker(BaseModel):
    id: str
    subdir: str | None = None

    @model_validator(mode="after")
    def fill_subdir(self) -> Speaker:
        if self.subdir is None:
            self.subdir = self.id
        return self


class Condition(BaseModel):
    name: str
    stimulus_list: str
    # "blocked" = massed (aaa bbb ccc); "cycled" = spaced (abc abc abc);
    # "random" = no predictable order, nothing prefilled. Massed is the
    # default — it's how these recordings are actually made.
    presentation_order: Literal["random", "cycled", "blocked"] = "blocked"
    # Legacy default was 2 repetitions per word.
    expected_reps_per_stimulus: int = 2


class ExperimentConfig(BaseModel):
    schema_version: int = 1
    name: str = ""
    recordings_root: str = "recordings"
    stimulus_lists_root: str = "stimulus_lists"
    output_root: str = "output"
    speakers: list[Speaker] = Field(default_factory=list)
    conditions: list[Condition] = Field(default_factory=list)
    detection: DetectionConfig = Field(default_factory=DetectionConfig)
    labeling: LabelingConfig = Field(default_factory=LabelingConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)

    # Set by load_config() after pydantic validation; never serialized.
    # Pydantic validators cannot use this because
    # they run before _config_dir exists.
    _config_dir: Path | None = PrivateAttr(default=None)

    def resolve(self, *parts: str) -> Path:
        """Resolve a config-relative path against the loaded config's directory."""
        if self._config_dir is None:
            raise ValueError(
                "ExperimentConfig._config_dir is not set; "
                "call load_config() instead of constructing directly, "
                "or assign cfg._config_dir before calling resolve()."
            )
        return (self._config_dir / Path(*parts)).resolve()

    def validate_disk_refs(self) -> None:
        """Filesystem-touching validation: every condition's stimulus_list must
        resolve to an existing, non-empty file.

        Not a pydantic ``@model_validator`` on purpose:
        pydantic validators run during ``model_validate_json``, before
        ``_config_dir`` is set, so they would always see ``None`` and no-op.
        Call this explicitly from ``load_config`` after the dir is anchored.
        """
        if self._config_dir is None:
            raise ValueError(
                "ExperimentConfig._config_dir must be set before validate_disk_refs()."
            )
        for c in self.conditions:
            p = self.resolve(c.stimulus_list)
            if not p.is_file():
                raise ValueError(
                    f"Condition '{c.name}': stimulus_list not found at {p}"
                )
            has_content = False
            with p.open("r", encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        has_content = True
                        break
            if not has_content:
                raise ValueError(
                    f"Condition '{c.name}': stimulus_list at {p} is empty "
                    "(no non-blank lines); an empty stimulus list is not allowed."
                )


def load_config(path: Path) -> ExperimentConfig:
    """Three-phase load:

    1. Pydantic schema validation from JSON text.
    2. Anchor ``_config_dir`` to ``path.parent``.
    3. Filesystem-dependent checks via ``validate_disk_refs``.
    """
    cfg = ExperimentConfig.model_validate_json(path.read_text())
    if cfg.schema_version > 1:
        raise ValueError(
            f"This clicketysplit.json was written for schema_version="
            f"{cfg.schema_version}; please upgrade to a newer clicketysplit."
        )
    cfg._config_dir = path.parent.resolve()
    cfg.validate_disk_refs()
    return cfg


def save_config(cfg: ExperimentConfig, path: Path) -> None:
    """Serialize ``cfg`` to ``path`` as indented JSON, excluding ``_config_dir``."""
    path.write_text(cfg.model_dump_json(indent=2, exclude={"_config_dir"}))
