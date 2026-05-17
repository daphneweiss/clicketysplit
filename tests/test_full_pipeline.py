"""End-to-end integration tests: detection -> review -> export.

The unit suites in ``test_detection.py``, ``test_pipeline.py`` and
``test_export.py`` exhaustively cover each module. This file only asserts
that the modules wire together — that a config-driven detect run plus a
manual ``status="accepted"`` flip plus :func:`export_tokens` produces the
on-disk shape the frontend expects (``proposed_segments.json``,
``tokens/*.wav``, ``token_manifest.json``, ``tokens.csv``).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import soundfile as sf

from clicketysplit.audio_io import load_audio
from clicketysplit.config import load_config
from clicketysplit.detection.base import LabeledSegment
from clicketysplit.detection.pipeline import detect_for_condition
from clicketysplit.export import (
    build_manifest,
    export_tokens,
    write_manifest,
    write_tokens_csv,
)


def _accept_first_word_segment(segments_path: Path) -> dict[str, object]:
    """Flip the first word-typed segment in ``proposed_segments.json`` to accepted.

    Returns the loaded payload (post-edit) so the test can re-use the
    segments list for the export step.
    """
    payload: dict[str, object] = json.loads(segments_path.read_text(encoding="utf-8"))
    segments = payload["segments"]
    assert isinstance(segments, list)
    accepted_any = False
    for seg in segments:
        assert isinstance(seg, dict)
        if seg["segment_type"] == "word" and seg.get("assigned_name"):
            seg["status"] = "accepted"
            accepted_any = True
            break
    assert accepted_any, (
        "fixture_experiment is expected to produce at least one labeled word "
        "segment via the implicit anchor; got none — check the fixture audio."
    )
    segments_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _segments_from_payload(payload: dict[str, object]) -> list[LabeledSegment]:
    raw_segments = payload["segments"]
    assert isinstance(raw_segments, list)
    out: list[LabeledSegment] = []
    for seg in raw_segments:
        assert isinstance(seg, dict)
        out.append(
            LabeledSegment(
                start=float(seg["start"]),
                end=float(seg["end"]),
                duration_ms=float(seg["duration_ms"]),
                segment_type=seg["segment_type"],  # type: ignore[arg-type]
                assigned_name=str(seg["assigned_name"]),
                label_source=seg["label_source"],  # type: ignore[arg-type]
                status=seg["status"],  # type: ignore[arg-type]
                token_index=int(seg["token_index"]),
                cluster_size=int(seg["cluster_size"]),
            )
        )
    return out


def _run_detect_and_export(fixture_experiment: Path) -> tuple[Path, Path]:
    """Run detect -> accept one -> export. Return (tokens_dir, segments_path)."""
    config_path = fixture_experiment / "clicketysplit.json"
    cfg = load_config(config_path)

    result = detect_for_condition(cfg, "spk1", "cond_a")
    segments_path = result.proposed_segments_path
    assert segments_path.is_file()

    payload = _accept_first_word_segment(segments_path)
    segments = _segments_from_payload(payload)

    audio_path = (
        fixture_experiment / "recordings" / "spk1" / "cond_a" / "rec.wav"
    )
    audio, sr = load_audio(audio_path)

    out_dir = result.proposed_segments_path.parent
    export = export_tokens(
        audio,
        sr,
        segments,
        out_dir,
        speaker_id="spk1",
        pad_ms=cfg.export.pad_ms,
        fade_ms=cfg.export.fade_ms,
        audio_format=cfg.export.format,
    )

    manifest = build_manifest(
        speaker_id="spk1",
        condition="cond_a",
        audio_source=str(audio_path.relative_to(fixture_experiment)),
        sample_rate=sr,
        pad_ms=cfg.export.pad_ms,
        fade_ms=cfg.export.fade_ms,
        audio_format=cfg.export.format,
        tokens=export.tokens_written,
    )
    write_manifest(out_dir / "token_manifest.json", manifest)
    write_tokens_csv(
        out_dir / "tokens.csv",
        speaker_id="spk1",
        condition="cond_a",
        sample_rate=sr,
        audio_format=cfg.export.format,
        tokens=export.tokens_written,
    )
    return out_dir / "tokens", segments_path


def test_full_pipeline_produces_expected_artifacts(fixture_experiment: Path) -> None:
    tokens_dir, segments_path = _run_detect_and_export(fixture_experiment)
    out_dir = tokens_dir.parent

    # proposed_segments.json has the right shape.
    payload = json.loads(segments_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["speaker_id"] == "spk1"
    assert payload["condition"] == "cond_a"
    assert isinstance(payload["segments"], list) and payload["segments"]

    # At least one wav was written to tokens/ and the file is non-empty audio.
    wavs = sorted(tokens_dir.glob("*.wav"))
    assert len(wavs) >= 1, f"Expected at least one exported wav in {tokens_dir}"
    audio, sr = sf.read(str(wavs[0]), dtype="float32")
    assert audio.shape[0] > 0, "Exported token audio is empty"
    assert sr > 0

    # Manifest and CSV exist with the expected top-level shape.
    manifest_path = out_dir / "token_manifest.json"
    csv_path = out_dir / "tokens.csv"
    assert manifest_path.is_file()
    assert csv_path.is_file()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["speaker_id"] == "spk1"
    assert manifest["condition"] == "cond_a"
    assert isinstance(manifest["tokens"], list) and len(manifest["tokens"]) >= 1
    assert sum(manifest["tokens_per_word"].values()) == len(manifest["tokens"])


def test_full_pipeline_csv_row_count_matches_tokens(
    fixture_experiment: Path,
) -> None:
    tokens_dir, _ = _run_detect_and_export(fixture_experiment)
    out_dir = tokens_dir.parent

    wavs = sorted(tokens_dir.glob("*.wav"))
    with open(out_dir / "tokens.csv", encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    # Header + one row per exported token.
    assert len(rows) == 1 + len(wavs)
    assert rows[0][0] == "speaker_id"
    for row in rows[1:]:
        assert row[0] == "spk1"
        assert row[1] == "cond_a"
