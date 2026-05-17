"""Tests for clicketysplit.export.

These tests use a local fake-segment dataclass so they do not depend on
``clicketysplit.detection.base.LabeledSegment`` being importable yet (task 4
implements it in parallel).
"""

from __future__ import annotations

import csv
import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from clicketysplit.export import (
    CSV_HEADER,
    ExportResult,
    SCHEMA_VERSION,
    TokenInfo,
    build_manifest,
    export_tokens,
    slugify_label,
    write_manifest,
    write_tokens_csv,
)

HAS_PARSELMOUTH = importlib.util.find_spec("parselmouth") is not None


@dataclass
class _FakeSeg:
    """Minimal duck-typed segment matching the fields export_tokens reads."""

    start: float
    end: float
    duration_ms: float = 0.0
    segment_type: str = "word"
    assigned_name: str = ""
    label_source: str = ""
    status: str = "accepted"
    token_index: int = 0
    cluster_size: int = 0


@dataclass
class _FakeBoundary:
    """Minimal duck-typed FileBoundary."""

    path: Path
    offset_sec: float
    duration_sec: float


def _silence(duration_sec: float, sr: int) -> np.ndarray:
    return np.zeros(int(round(duration_sec * sr)), dtype=np.float32)


def _ramp(n: int) -> np.ndarray:
    # A non-zero ramp so fade-in/out leaves a visible mark at the edges.
    return np.linspace(0.5, 1.0, n, dtype=np.float32)


def _audio_with_marks(sr: int, total_sec: float, marks: list[tuple[float, float]]) -> np.ndarray:
    """Build an all-1.0 region for each (start_sec, end_sec) over silence."""
    audio = _silence(total_sec, sr)
    for start_s, end_s in marks:
        start_i = int(round(start_s * sr))
        end_i = int(round(end_s * sr))
        audio[start_i:end_i] = 1.0
    return audio


def test_slugify_label_basic() -> None:
    assert slugify_label("apple") == "apple"


def test_slugify_label_spaces_and_punct() -> None:
    assert slugify_label("hello world!") == "hello_world_"


def test_slugify_label_empty() -> None:
    assert slugify_label("") == ""


def test_slugify_label_non_ascii() -> None:
    # No Unicode normalization — non-ASCII is replaced character-by-character.
    assert slugify_label("naïve") == "na_ve"


def test_slugify_label_preserves_hyphen_and_underscore() -> None:
    assert slugify_label("co-pilot_v2") == "co-pilot_v2"


def test_export_tokens_happy_path(tmp_path: Path) -> None:
    sr = 16000
    total = 3.0
    marks = [(0.5, 0.8), (1.2, 1.5), (2.0, 2.3)]
    audio = _audio_with_marks(sr, total, marks)
    segments = [
        _FakeSeg(start=s, end=e, segment_type="word", assigned_name=n, status="accepted")
        for (s, e), n in zip(marks, ["apple", "banana", "apple"])
    ]

    out_dir = tmp_path / "spk_01" / "cond_a"
    result = export_tokens(
        audio, sr, segments, out_dir, speaker_id="spk_01", pad_ms=0, fade_ms=0
    )

    assert isinstance(result, ExportResult)
    assert len(result.tokens_written) == 3
    tokens_dir = out_dir / "tokens"
    assert tokens_dir.is_dir()
    written = sorted(p.name for p in tokens_dir.glob("*.wav"))
    assert written == sorted(
        ["spk_01_apple-1.wav", "spk_01_banana-1.wav", "spk_01_apple-2.wav"]
    )
    # Each file should contain exactly (end-start)*sr samples (no pad).
    for token in result.tokens_written:
        data, file_sr = sf.read(str(tokens_dir / token.filename), dtype="float32")
        assert file_sr == sr
        expected_samples = int(round((token.end_sec - token.start_sec) * sr))
        assert data.shape[0] == expected_samples


def test_export_tokens_pad_ms_extends_slice(tmp_path: Path) -> None:
    sr = 16000
    total = 2.0
    marks = [(0.5, 0.7)]
    audio = _audio_with_marks(sr, total, marks)
    seg = _FakeSeg(start=0.5, end=0.7, assigned_name="x", status="accepted")

    out_dir = tmp_path / "out"
    result = export_tokens(audio, sr, [seg], out_dir, "spk", pad_ms=20, fade_ms=0)
    token = result.tokens_written[0]
    pad_samples = int(round(20 * sr / 1000))
    expected_total = int(round((0.7 - 0.5) * sr)) + 2 * pad_samples
    data, _ = sf.read(str(out_dir / "tokens" / token.filename), dtype="float32")
    assert data.shape[0] == expected_total


def test_export_tokens_pad_clipped_at_audio_bounds(tmp_path: Path) -> None:
    sr = 16000
    # Word starts immediately at t=0 so the left pad must clip.
    total = 1.0
    audio = _audio_with_marks(sr, total, [(0.0, 0.2)])
    seg = _FakeSeg(start=0.0, end=0.2, assigned_name="x", status="accepted")

    out_dir = tmp_path / "out"
    result = export_tokens(audio, sr, [seg], out_dir, "spk", pad_ms=50, fade_ms=0)
    pad_samples = int(round(50 * sr / 1000))
    data, _ = sf.read(str(out_dir / "tokens" / result.tokens_written[0].filename), dtype="float32")
    # Left side cannot be padded past 0; only the right pad gets added.
    expected = int(round(0.2 * sr)) + pad_samples
    assert data.shape[0] == expected


def test_export_tokens_fade_ms_applies_linear_ramp(tmp_path: Path) -> None:
    sr = 16000
    total = 1.0
    audio = _audio_with_marks(sr, total, [(0.2, 0.8)])
    seg = _FakeSeg(start=0.2, end=0.8, assigned_name="x", status="accepted")

    out_dir = tmp_path / "out"
    fade_ms = 5
    result = export_tokens(audio, sr, [seg], out_dir, "spk", pad_ms=0, fade_ms=fade_ms)
    data, _ = sf.read(str(out_dir / "tokens" / result.tokens_written[0].filename), dtype="float32")
    fade_samples = int(round(fade_ms * sr / 1000))
    mid = len(data) // 2
    # First sample is faded to ~0; middle sample sees the full amplitude.
    assert data[0] < data[mid]
    assert data[-1] < data[mid]
    # The fade is linear: a few samples in should be visibly larger than sample 0.
    assert data[fade_samples // 2] > data[0]


def test_export_tokens_skip_rules(tmp_path: Path) -> None:
    sr = 16000
    audio = _silence(2.0, sr)
    segments = [
        _FakeSeg(start=0.1, end=0.3, status="rejected", segment_type="word", assigned_name="a"),
        _FakeSeg(start=0.4, end=0.6, status="pending", segment_type="word", assigned_name="b"),
        _FakeSeg(start=0.7, end=0.9, status="accepted", segment_type="crosstalk", assigned_name="c"),
        _FakeSeg(start=1.0, end=1.2, status="accepted", segment_type="word", assigned_name=""),
        _FakeSeg(start=1.3, end=1.5, status="accepted", segment_type="word", assigned_name="ok"),
    ]
    out_dir = tmp_path / "out"
    result = export_tokens(audio, sr, segments, out_dir, "spk", pad_ms=0, fade_ms=0)

    assert result.skipped_not_accepted == 2  # rejected + pending
    assert result.skipped_not_word == 1  # crosstalk
    assert result.skipped_unnamed == 1
    assert result.skipped_empty_slice == 0
    assert len(result.tokens_written) == 1
    assert result.tokens_written[0].assigned_name == "ok"


def test_export_tokens_sequential_token_indices(tmp_path: Path) -> None:
    sr = 16000
    audio = _silence(5.0, sr)
    segments = [
        _FakeSeg(start=0.1, end=0.2, assigned_name="apple", status="accepted"),
        _FakeSeg(start=0.3, end=0.4, assigned_name="apple", status="accepted"),
        _FakeSeg(start=0.5, end=0.6, assigned_name="banana", status="accepted"),
        _FakeSeg(start=0.7, end=0.8, assigned_name="apple", status="accepted"),
    ]
    out_dir = tmp_path / "out"
    result = export_tokens(audio, sr, segments, out_dir, "s", pad_ms=0, fade_ms=0)
    apple_tokens = [t for t in result.tokens_written if t.assigned_name == "apple"]
    banana_tokens = [t for t in result.tokens_written if t.assigned_name == "banana"]
    assert [t.token_index for t in apple_tokens] == [1, 2, 3]
    assert [t.filename for t in apple_tokens] == ["s_apple-1.wav", "s_apple-2.wav", "s_apple-3.wav"]
    assert [t.token_index for t in banana_tokens] == [1]


def test_export_tokens_atomic_no_tmp_leftover(tmp_path: Path) -> None:
    sr = 16000
    audio = _audio_with_marks(sr, 1.0, [(0.2, 0.4)])
    seg = _FakeSeg(start=0.2, end=0.4, assigned_name="x", status="accepted")
    out_dir = tmp_path / "out"
    export_tokens(audio, sr, [seg], out_dir, "spk", pad_ms=0, fade_ms=0)
    tokens_dir = out_dir / "tokens"
    leftovers = list(tokens_dir.glob("*.tmp"))
    assert leftovers == []


def test_export_tokens_empty_slice_skipped(tmp_path: Path) -> None:
    sr = 16000
    audio = _silence(1.0, sr)
    # A segment past the end of the audio: padded slice is empty.
    seg = _FakeSeg(start=2.0, end=2.1, assigned_name="x", status="accepted")
    out_dir = tmp_path / "out"
    result = export_tokens(audio, sr, [seg], out_dir, "spk", pad_ms=0, fade_ms=0)
    assert result.tokens_written == []
    assert result.skipped_empty_slice == 1


def test_export_tokens_slugifies_filename(tmp_path: Path) -> None:
    sr = 16000
    audio = _audio_with_marks(sr, 1.0, [(0.2, 0.4)])
    seg = _FakeSeg(start=0.2, end=0.4, assigned_name="hello world!", status="accepted")
    out_dir = tmp_path / "out"
    result = export_tokens(audio, sr, [seg], out_dir, "spk", pad_ms=0, fade_ms=0)
    assert result.tokens_written[0].filename == "spk_hello_world_-1.wav"
    assert (out_dir / "tokens" / "spk_hello_world_-1.wav").is_file()


def test_build_manifest_schema_fields() -> None:
    tokens = [
        TokenInfo(
            filename="spk_apple-1.wav",
            assigned_name="apple",
            token_index=1,
            start_sec=1.23,
            end_sec=1.78,
            duration_ms=550.0,
            padded_start_sec=1.21,
            padded_end_sec=1.80,
            padded_duration_ms=590.0,
        ),
        TokenInfo(
            filename="spk_apple-2.wav",
            assigned_name="apple",
            token_index=2,
            start_sec=3.41,
            end_sec=3.97,
            duration_ms=560.0,
            padded_start_sec=3.39,
            padded_end_sec=3.99,
            padded_duration_ms=600.0,
        ),
        TokenInfo(
            filename="spk_banana-1.wav",
            assigned_name="banana",
            token_index=1,
            start_sec=5.0,
            end_sec=5.5,
            duration_ms=500.0,
            padded_start_sec=4.98,
            padded_end_sec=5.52,
            padded_duration_ms=540.0,
        ),
    ]
    manifest = build_manifest(
        speaker_id="spk",
        condition="cond_a",
        audio_source="output/spk/cond_a/denoised.wav",
        sample_rate=44100,
        pad_ms=20,
        fade_ms=3,
        audio_format="wav",
        tokens=tokens,
        exported_at="2026-05-17T14:23:18Z",
    )
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["speaker_id"] == "spk"
    assert manifest["condition"] == "cond_a"
    assert manifest["exported_at"] == "2026-05-17T14:23:18Z"
    assert manifest["audio_source"] == "output/spk/cond_a/denoised.wav"
    assert manifest["sample_rate"] == 44100
    assert manifest["pad_ms"] == 20
    assert manifest["fade_ms"] == 3
    assert manifest["audio_format"] == "wav"
    assert manifest["tokens_per_word"] == {"apple": 2, "banana": 1}
    assert len(manifest["tokens"]) == 3
    t0 = manifest["tokens"][0]
    for k in (
        "filename",
        "assigned_name",
        "token_index",
        "start_sec",
        "end_sec",
        "duration_ms",
        "padded_start_sec",
        "padded_end_sec",
        "padded_duration_ms",
    ):
        assert k in t0


def test_build_manifest_default_exported_at_is_iso8601_utc() -> None:
    manifest = build_manifest(
        speaker_id="s",
        condition="c",
        audio_source="x.wav",
        sample_rate=16000,
        pad_ms=0,
        fade_ms=0,
        audio_format="wav",
        tokens=[],
    )
    ts = manifest["exported_at"]
    assert ts.endswith("Z")
    assert "T" in ts


def test_write_manifest_roundtrip(tmp_path: Path) -> None:
    manifest = build_manifest(
        speaker_id="s",
        condition="c",
        audio_source="x.wav",
        sample_rate=16000,
        pad_ms=10,
        fade_ms=2,
        audio_format="wav",
        tokens=[],
        exported_at="2026-01-02T03:04:05Z",
    )
    path = tmp_path / "token_manifest.json"
    write_manifest(path, manifest)
    assert path.is_file()
    assert not (path.parent / (path.name + ".tmp")).exists()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == manifest


def test_write_tokens_csv_header_and_rows(tmp_path: Path) -> None:
    tokens = [
        TokenInfo(
            filename="spk_apple-1.wav",
            assigned_name="apple",
            token_index=1,
            start_sec=1.23,
            end_sec=1.78,
            duration_ms=550.0,
            padded_start_sec=1.21,
            padded_end_sec=1.80,
            padded_duration_ms=590.0,
        ),
        TokenInfo(
            filename="spk_apple-2.wav",
            assigned_name="apple",
            token_index=2,
            start_sec=3.41,
            end_sec=3.97,
            duration_ms=560.0,
            padded_start_sec=3.39,
            padded_end_sec=3.99,
            padded_duration_ms=600.0,
        ),
    ]
    path = tmp_path / "tokens.csv"
    write_tokens_csv(
        path,
        speaker_id="spk",
        condition="cond_a",
        sample_rate=44100,
        audio_format="wav",
        tokens=tokens,
    )
    assert path.is_file()
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    assert tuple(rows[0]) == CSV_HEADER
    assert rows[0] == [
        "speaker_id",
        "condition",
        "assigned_name",
        "token_index",
        "filename",
        "start_sec",
        "end_sec",
        "duration_ms",
        "padded_duration_ms",
        "sample_rate",
        "audio_format",
    ]
    assert len(rows) == 1 + len(tokens)
    # Row content for the first token (string-compare via csv).
    assert rows[1][0] == "spk"
    assert rows[1][1] == "cond_a"
    assert rows[1][2] == "apple"
    assert rows[1][3] == "1"
    assert rows[1][4] == "spk_apple-1.wav"
    assert rows[1][9] == "44100"
    assert rows[1][10] == "wav"


@pytest.mark.needs_praat
@pytest.mark.skipif(not HAS_PARSELMOUTH, reason="praat-parselmouth not installed")
def test_write_textgrid_three_tiers(tmp_path: Path) -> None:
    import parselmouth
    from parselmouth.praat import call

    from clicketysplit.export import write_textgrid

    duration = 3.0
    tokens = [
        TokenInfo(
            filename="s_apple-1.wav",
            assigned_name="apple",
            token_index=1,
            start_sec=0.5,
            end_sec=0.8,
            duration_ms=300.0,
            padded_start_sec=0.48,
            padded_end_sec=0.82,
            padded_duration_ms=340.0,
        ),
        TokenInfo(
            filename="s_banana-1.wav",
            assigned_name="banana",
            token_index=1,
            start_sec=2.0,
            end_sec=2.3,
            duration_ms=300.0,
            padded_start_sec=1.98,
            padded_end_sec=2.32,
            padded_duration_ms=340.0,
        ),
    ]
    all_segments = [
        _FakeSeg(start=0.5, end=0.8, segment_type="word", assigned_name="apple"),
        _FakeSeg(start=1.0, end=1.1, segment_type="noise", assigned_name="noise"),
        _FakeSeg(start=2.0, end=2.3, segment_type="word", assigned_name="banana"),
    ]
    boundaries = [
        _FakeBoundary(path=Path("a.wav"), offset_sec=0.0, duration_sec=1.5),
        _FakeBoundary(path=Path("b.wav"), offset_sec=1.5, duration_sec=1.5),
    ]
    out = tmp_path / "cond.TextGrid"
    write_textgrid(duration, tokens, all_segments, boundaries, out)
    assert out.is_file()

    tg = parselmouth.read(str(out))
    assert int(call(tg, "Get number of tiers")) == 3
    assert call(tg, "Get tier name", 1) == "tokens"
    assert call(tg, "Get tier name", 2) == "all_segments"
    assert call(tg, "Get tier name", 3) == "file_boundaries"

    # 'tokens' tier should contain intervals labeled 'apple' and 'banana'.
    n_intervals = int(call(tg, "Get number of intervals", 1))
    labels = {call(tg, "Get label of interval", 1, i) for i in range(1, n_intervals + 1)}
    assert "apple" in labels
    assert "banana" in labels

    # file_boundaries point tier holds the non-zero offset only.
    n_points = int(call(tg, "Get number of points", 3))
    assert n_points == 1
