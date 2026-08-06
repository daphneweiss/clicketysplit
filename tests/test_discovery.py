"""Unit tests for :mod:`clicketysplit.discovery`.

The rules under test are implemented in clicketysplit.discovery. The
fixtures here build tiny on-disk trees and assert the dataclass output —
``scan_recordings`` never reads audio so the WAV files we drop are empty
stubs created with ``touch``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from clicketysplit.discovery import (
    DiscoveryResult,
    ScannedCondition,
    ScannedSpeaker,
    scan_recordings,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _touch(path: Path, size: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if size:
        path.write_bytes(b"\x00" * size)
    else:
        path.touch()
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_empty_directory_returns_no_speakers(tmp_path: Path) -> None:
    result = scan_recordings(tmp_path)
    assert isinstance(result, DiscoveryResult)
    assert result.speakers == []
    assert result.unique_condition_names == []
    assert result.root == tmp_path.resolve()


def test_speaker_with_one_condition_two_files(tmp_path: Path) -> None:
    _touch(tmp_path / "speaker_01" / "condition_a" / "take1.wav", size=10)
    _touch(tmp_path / "speaker_01" / "condition_a" / "take2.wav", size=20)

    result = scan_recordings(tmp_path)

    assert len(result.speakers) == 1
    sp = result.speakers[0]
    assert isinstance(sp, ScannedSpeaker)
    assert sp.id == "speaker_01"
    assert sp.subdir == "speaker_01"
    assert sp.flat_files == []
    assert len(sp.conditions) == 1
    cond = sp.conditions[0]
    assert isinstance(cond, ScannedCondition)
    assert cond.name == "condition_a"
    assert cond.n_files == 2
    assert cond.total_bytes == 30
    # Files are absolute paths.
    assert all(p.is_absolute() for p in cond.files)
    # Sorted by lowercase name.
    assert [p.name for p in cond.files] == ["take1.wav", "take2.wav"]
    assert result.unique_condition_names == ["condition_a"]


def test_speaker_flat_layout(tmp_path: Path) -> None:
    _touch(tmp_path / "speaker_02" / "recording_a.wav")
    _touch(tmp_path / "speaker_02" / "recording_b.wav")

    result = scan_recordings(tmp_path)

    assert len(result.speakers) == 1
    sp = result.speakers[0]
    assert sp.id == "speaker_02"
    assert sp.conditions == []
    assert len(sp.flat_files) == 2
    assert all(p.is_absolute() for p in sp.flat_files)
    assert [p.name for p in sp.flat_files] == ["recording_a.wav", "recording_b.wav"]
    # Flat layout contributes nothing to unique_condition_names.
    assert result.unique_condition_names == []


def test_dotfile_and_underscore_dirs_skipped(tmp_path: Path) -> None:
    # Real speaker so the directory isn't empty after the skips.
    _touch(tmp_path / "speaker_01" / "condition_a" / "take.wav")
    # These should all be skipped:
    _touch(tmp_path / ".hidden_speaker" / "condition_x" / "take.wav")
    _touch(tmp_path / "_staging" / "condition_x" / "take.wav")
    _touch(tmp_path / "speaker_01" / ".scratch" / "ignored.wav")
    _touch(tmp_path / "speaker_01" / "_tmp" / "ignored.wav")

    result = scan_recordings(tmp_path)

    speaker_ids = [s.id for s in result.speakers]
    assert speaker_ids == ["speaker_01"]
    cond_names = [c.name for c in result.speakers[0].conditions]
    assert cond_names == ["condition_a"]


def test_reserved_names_skipped(tmp_path: Path) -> None:
    # All four reserved names sitting under root and under a speaker.
    for reserved in ("output", "tokens", "final", "alternates"):
        _touch(tmp_path / reserved / "audio.wav")
        _touch(tmp_path / "speaker_01" / reserved / "audio.wav")
    # One real condition so speaker_01 survives.
    _touch(tmp_path / "speaker_01" / "real_condition" / "take.wav")

    result = scan_recordings(tmp_path)

    # Reserved-named directories at root are not treated as speakers.
    assert [s.id for s in result.speakers] == ["speaker_01"]
    # Reserved-named subdirs are not treated as conditions.
    assert [c.name for c in result.speakers[0].conditions] == ["real_condition"]


def test_unique_condition_names_is_sorted_union(tmp_path: Path) -> None:
    _touch(tmp_path / "speaker_01" / "cond_b" / "x.wav")
    _touch(tmp_path / "speaker_01" / "cond_a" / "x.wav")
    _touch(tmp_path / "speaker_02" / "cond_c" / "x.wav")
    _touch(tmp_path / "speaker_02" / "cond_a" / "x.wav")  # duplicate of speaker_01's

    result = scan_recordings(tmp_path)

    assert result.unique_condition_names == ["cond_a", "cond_b", "cond_c"]


def test_mixed_speakers_conditions_and_flat(tmp_path: Path) -> None:
    # One speaker with conditions
    _touch(tmp_path / "speaker_01" / "cond_a" / "x.wav")
    _touch(tmp_path / "speaker_01" / "cond_b" / "y.wav")
    # Another speaker with flat layout
    _touch(tmp_path / "speaker_02" / "single.wav")

    result = scan_recordings(tmp_path)

    ids = [s.id for s in result.speakers]
    assert ids == ["speaker_01", "speaker_02"]
    sp1, sp2 = result.speakers
    assert [c.name for c in sp1.conditions] == ["cond_a", "cond_b"]
    assert sp1.flat_files == []
    assert sp2.conditions == []
    assert len(sp2.flat_files) == 1
    # unique_condition_names ignores flat-layout speakers entirely.
    assert result.unique_condition_names == ["cond_a", "cond_b"]


def test_only_reserved_subdir_no_audio_is_not_a_speaker(tmp_path: Path) -> None:
    # speaker_only_final has ONLY a `final/` subdir with audio, and no
    # audio directly under the speaker dir. Because `final` is reserved
    # we never recurse into it, and the speaker has no other audio →
    # it must not appear as a speaker.
    _touch(tmp_path / "speaker_only_final" / "final" / "weird.wav")

    result = scan_recordings(tmp_path)

    assert result.speakers == []
    assert result.unique_condition_names == []


def test_non_audio_extension_does_not_create_a_speaker(tmp_path: Path) -> None:
    _touch(tmp_path / "speaker_01" / "notes.txt")
    _touch(tmp_path / "speaker_01" / "metadata.json")

    result = scan_recordings(tmp_path)

    assert result.speakers == []


def test_sort_is_case_insensitive_and_deterministic(tmp_path: Path) -> None:
    _touch(tmp_path / "Bravo" / "cond" / "x.wav")
    _touch(tmp_path / "alpha" / "cond" / "x.wav")
    _touch(tmp_path / "charlie" / "cond" / "x.wav")

    result = scan_recordings(tmp_path)

    assert [s.id for s in result.speakers] == ["alpha", "Bravo", "charlie"]


def test_dataclasses_are_frozen() -> None:
    cond = ScannedCondition(name="x", files=[], n_files=0, total_bytes=0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        cond.name = "y"  # type: ignore[misc]
