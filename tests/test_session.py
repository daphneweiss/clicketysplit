"""Unit tests for :mod:`clicketysplit.session`.

The session files live at
``<experiment_dir>/<output_subdir>/.session{,.autosave}.json``. The output
dir is created on demand. Writes are atomic (tmpfile + ``os.replace``);
on a happy-path write no ``*.tmp`` is left behind.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from clicketysplit.session import latest_session_path, load_session, save_session


@pytest.fixture
def exp(tmp_path: Path) -> Path:
    """Bare experiment dir (no output subdir yet — session.py creates it)."""
    d = tmp_path / "exp"
    d.mkdir()
    return d


def test_load_returns_empty_when_neither_file_exists(exp: Path) -> None:
    assert load_session(exp) == {}
    # And the output subdir is NOT created by load.
    assert not (exp / "output").exists()


def test_save_round_trip_writes_session_json(exp: Path) -> None:
    data = {"step": "review", "active_speaker": "speaker_01", "token_index": 3}
    written = save_session(exp, data)

    assert written == exp / "output" / ".session.json"
    assert written.is_file()
    # The bytes on disk are JSON.
    assert json.loads(written.read_text(encoding="utf-8")) == data
    # Round-trip via load_session.
    assert load_session(exp) == data


def test_save_autosave_writes_autosave_file(exp: Path) -> None:
    data = {"step": "wizard"}
    written = save_session(exp, data, autosave=True)
    assert written == exp / "output" / ".session.autosave.json"
    assert written.is_file()
    assert not (exp / "output" / ".session.json").exists()
    # load_session returns the autosave content when only the autosave exists.
    assert load_session(exp) == data


def test_latest_session_path_prefers_newer_autosave(exp: Path) -> None:
    save_session(exp, {"v": 1})
    # Force a measurably newer mtime on the autosave file. Using
    # st_mtime_ns avoids the "same-second" tie that some filesystems hit
    # when two writes land in quick succession.
    time.sleep(0.01)
    save_session(exp, {"v": 2}, autosave=True)

    latest = latest_session_path(exp)
    assert latest is not None
    assert latest.name == ".session.autosave.json"
    # load_session uses latest_session_path so the autosave content wins.
    assert load_session(exp) == {"v": 2}


def test_latest_session_path_prefers_newer_session_when_autosave_is_older(
    exp: Path,
) -> None:
    save_session(exp, {"v": "old"}, autosave=True)
    time.sleep(0.01)
    save_session(exp, {"v": "new"})

    latest = latest_session_path(exp)
    assert latest is not None
    assert latest.name == ".session.json"
    assert load_session(exp) == {"v": "new"}


def test_latest_session_path_none_when_nothing_exists(exp: Path) -> None:
    assert latest_session_path(exp) is None


def test_atomic_write_leaves_no_tmp_file(exp: Path) -> None:
    save_session(exp, {"hello": "world"})
    out_dir = exp / "output"
    tmp_files = [p for p in out_dir.iterdir() if p.name.endswith(".tmp")]
    assert tmp_files == []
    # The only files in output/ should be the session file.
    contents = {p.name for p in out_dir.iterdir()}
    assert contents == {".session.json"}


def test_output_dir_created_if_missing(exp: Path) -> None:
    assert not (exp / "output").exists()
    save_session(exp, {"a": 1})
    assert (exp / "output").is_dir()


def test_custom_output_subdir(exp: Path) -> None:
    save_session(exp, {"x": 1}, output_subdir="weird_out")
    assert (exp / "weird_out" / ".session.json").is_file()
    assert load_session(exp, output_subdir="weird_out") == {"x": 1}
    # The default output_subdir lookup must NOT find this.
    assert load_session(exp) == {}


def test_save_overwrites_existing_file(exp: Path) -> None:
    save_session(exp, {"v": 1})
    save_session(exp, {"v": 2})
    assert load_session(exp) == {"v": 2}


def test_load_rejects_non_object_json(exp: Path) -> None:
    out_dir = exp / "output"
    out_dir.mkdir()
    (out_dir / ".session.json").write_text("[1, 2, 3]", encoding="utf-8")
    # A session file that parses but isn't an object is a type problem, not
    # a value problem — load_session raises TypeError.
    with pytest.raises(TypeError):
        load_session(exp)


def test_load_raises_on_malformed_json(exp: Path) -> None:
    out_dir = exp / "output"
    out_dir.mkdir()
    (out_dir / ".session.json").write_text("not json at all", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_session(exp)


def test_save_returns_absolute_path(exp: Path) -> None:
    written = save_session(exp, {"a": 1})
    assert written.is_absolute()
    assert written.parent == exp / "output"


def test_tmpfile_cleanup_on_replace_failure(
    exp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Verify the exception path doesn't leak a *.tmp file even when
    # os.replace fails partway through.
    real_replace = os.replace

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("simulated rename failure")

    monkeypatch.setattr("clicketysplit.session.os.replace", boom)
    with pytest.raises(OSError):
        save_session(exp, {"v": 1})
    # Restore for any later test execution in this module.
    monkeypatch.setattr("clicketysplit.session.os.replace", real_replace)

    out_dir = exp / "output"
    tmp_files = [p for p in out_dir.iterdir() if p.name.endswith(".tmp")]
    assert tmp_files == []
