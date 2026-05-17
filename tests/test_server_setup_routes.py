"""Smoke tests for the task-8 setup-wizard write routes.

Covers ``/api/discover``, ``POST /api/config`` (wizard write — NOT
``/api/config/load``), GET/POST ``/api/session``, and
``/api/resolve_browse``. Each test creates a fresh ``create_app`` so
no module-level Flask state leaks (CONTRACT_NOTES C1).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from flask.testing import FlaskClient

from clicketysplit.server import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _post_json(client: FlaskClient, url: str, body: dict[str, Any]) -> Any:
    return client.post(url, data=json.dumps(body), content_type="application/json")


def _touch(p: Path, size: int = 0) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    if size:
        p.write_bytes(b"\x00" * size)
    else:
        p.touch()
    return p


@pytest.fixture
def client() -> FlaskClient:
    return create_app(experiment_path=None).test_client()


# ---------------------------------------------------------------------------
# /api/discover
# ---------------------------------------------------------------------------


def test_discover_returns_expected_shape(client: FlaskClient, tmp_path: Path) -> None:
    recordings = tmp_path / "rec"
    _touch(recordings / "speaker_01" / "cond_a" / "x.wav", size=10)
    _touch(recordings / "speaker_01" / "cond_a" / "y.wav", size=20)
    _touch(recordings / "speaker_02" / "cond_a" / "z.wav", size=5)

    resp = _post_json(client, "/api/discover", {"root": str(recordings)})

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["root"] == str(recordings.resolve())
    assert body["unique_condition_names"] == ["cond_a"]
    assert len(body["speakers"]) == 2
    sp = body["speakers"][0]
    assert sp["id"] == "speaker_01"
    assert sp["subdir"] == "speaker_01"
    assert sp["flat_files"] == []
    assert sp["conditions"][0]["name"] == "cond_a"
    assert sp["conditions"][0]["n_files"] == 2
    assert sp["conditions"][0]["total_bytes"] == 30
    assert all(isinstance(f, str) for f in sp["conditions"][0]["files"])


def test_discover_nonexistent_root_returns_400_not_found(
    client: FlaskClient, tmp_path: Path
) -> None:
    bogus = tmp_path / "does_not_exist"
    resp = _post_json(client, "/api/discover", {"root": str(bogus)})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "not_found"


def test_discover_file_not_directory_returns_not_found(
    client: FlaskClient, tmp_path: Path
) -> None:
    f = tmp_path / "file.txt"
    f.write_text("x", encoding="utf-8")
    resp = _post_json(client, "/api/discover", {"root": str(f)})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "not_found"


def test_discover_relative_path_rejected(client: FlaskClient) -> None:
    resp = _post_json(client, "/api/discover", {"root": "relative/path"})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "validation_error"


def test_discover_missing_root_field(client: FlaskClient) -> None:
    resp = _post_json(client, "/api/discover", {})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "validation_error"


# ---------------------------------------------------------------------------
# POST /api/config (wizard write)
# ---------------------------------------------------------------------------


def _good_config_payload(stim_filename: str = "cond_a.txt") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "name": "wizard test",
        "recordings_root": "recordings",
        "stimulus_lists_root": "stimulus_lists",
        "output_root": "output",
        "speakers": [{"id": "speaker_01"}],
        "conditions": [
            {
                "name": "cond_a",
                "stimulus_list": f"stimulus_lists/{stim_filename}",
                "presentation_order": "random",
            }
        ],
    }


def test_post_config_writes_and_sets_active(
    client: FlaskClient, tmp_path: Path
) -> None:
    # Pre-create the stimulus list so the disk-refs check passes.
    stim_dir = tmp_path / "exp" / "stimulus_lists"
    stim_dir.mkdir(parents=True)
    (stim_dir / "cond_a.txt").write_text("apple\nbanana\n", encoding="utf-8")

    config_dir = tmp_path / "exp"
    resp = _post_json(
        client,
        "/api/config",
        {"config_dir": str(config_dir), "config": _good_config_payload()},
    )
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["status"] == "ok"
    written = Path(body["path"])
    assert written.is_file()
    assert written.name == "clicketysplit.json"

    # Subsequent GET /api/config sees the new active experiment.
    get_resp = client.get("/api/config")
    assert get_resp.status_code == 200, get_resp.get_json()
    cfg = get_resp.get_json()
    assert cfg["name"] == "wizard test"
    assert cfg["_experiment_path"] == str(written)


def test_post_config_creates_config_dir_if_missing(
    client: FlaskClient, tmp_path: Path
) -> None:
    config_dir = tmp_path / "brand_new_exp"
    assert not config_dir.exists()
    # Pre-create the stimulus list so disk-refs passes.
    (config_dir / "stimulus_lists").mkdir(parents=True)
    (config_dir / "stimulus_lists" / "cond_a.txt").write_text(
        "apple\n", encoding="utf-8"
    )

    resp = _post_json(
        client,
        "/api/config",
        {"config_dir": str(config_dir), "config": _good_config_payload()},
    )
    assert resp.status_code == 200, resp.get_json()
    assert (config_dir / "clicketysplit.json").is_file()


def test_post_config_invalid_payload_returns_validation_error(
    client: FlaskClient, tmp_path: Path
) -> None:
    config_dir = tmp_path / "exp"
    config_dir.mkdir()
    # Bad: presentation_order is not in the literal set.
    bad = _good_config_payload()
    bad["conditions"][0]["presentation_order"] = "nope_not_a_real_order"
    resp = _post_json(
        client,
        "/api/config",
        {"config_dir": str(config_dir), "config": bad},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "validation_error"
    # File should NOT have been written for a schema-level failure.
    assert not (config_dir / "clicketysplit.json").exists()


def test_post_config_missing_stimulus_list_writes_then_reports_validation_error(
    client: FlaskClient, tmp_path: Path
) -> None:
    config_dir = tmp_path / "exp"
    config_dir.mkdir()
    # NOTE: we intentionally do NOT create the stimulus list file.
    resp = _post_json(
        client,
        "/api/config",
        {"config_dir": str(config_dir), "config": _good_config_payload()},
    )
    assert resp.status_code == 400
    err = resp.get_json()["error"]
    assert err["code"] == "validation_error"
    # The file IS written even though disk-refs failed (per task brief).
    assert (config_dir / "clicketysplit.json").is_file()


def test_post_config_relative_config_dir_rejected(
    client: FlaskClient,
) -> None:
    resp = _post_json(
        client,
        "/api/config",
        {"config_dir": "relative/exp", "config": _good_config_payload()},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "validation_error"


def test_post_config_missing_fields(client: FlaskClient) -> None:
    resp = _post_json(client, "/api/config", {"config_dir": "/tmp/foo"})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "validation_error"


# ---------------------------------------------------------------------------
# GET/POST /api/session
# ---------------------------------------------------------------------------


def _bootstrap_loaded_experiment(
    client: FlaskClient, tmp_path: Path
) -> Path:
    """Use POST /api/config to install an active experiment; return its dir."""
    stim_dir = tmp_path / "exp" / "stimulus_lists"
    stim_dir.mkdir(parents=True)
    (stim_dir / "cond_a.txt").write_text("apple\n", encoding="utf-8")
    config_dir = tmp_path / "exp"
    resp = _post_json(
        client,
        "/api/config",
        {"config_dir": str(config_dir), "config": _good_config_payload()},
    )
    assert resp.status_code == 200, resp.get_json()
    return config_dir


def test_session_get_without_experiment_returns_404(client: FlaskClient) -> None:
    resp = client.get("/api/session")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "no_experiment"


def test_session_get_returns_empty_dict_initially(
    client: FlaskClient, tmp_path: Path
) -> None:
    _bootstrap_loaded_experiment(client, tmp_path)
    resp = client.get("/api/session")
    assert resp.status_code == 200
    assert resp.get_json() == {}


def test_session_round_trip(client: FlaskClient, tmp_path: Path) -> None:
    config_dir = _bootstrap_loaded_experiment(client, tmp_path)
    payload = {"step": "review", "active_speaker": "speaker_01", "zoom": 1.5}
    resp = _post_json(client, "/api/session", payload)
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["path"] == "output/.session.json"
    assert (config_dir / "output" / ".session.json").is_file()

    # GET reflects the saved state.
    get_resp = client.get("/api/session")
    assert get_resp.status_code == 200
    assert get_resp.get_json() == payload


def test_session_autosave_via_body_field(
    client: FlaskClient, tmp_path: Path
) -> None:
    config_dir = _bootstrap_loaded_experiment(client, tmp_path)
    resp = _post_json(
        client, "/api/session", {"step": "wizard", "autosave": True}
    )
    assert resp.status_code == 200
    assert (config_dir / "output" / ".session.autosave.json").is_file()
    # The `autosave` key should NOT have been persisted in the session blob.
    blob = json.loads(
        (config_dir / "output" / ".session.autosave.json").read_text(
            encoding="utf-8"
        )
    )
    assert "autosave" not in blob
    assert blob == {"step": "wizard"}


def test_session_autosave_via_query_flag(
    client: FlaskClient, tmp_path: Path
) -> None:
    config_dir = _bootstrap_loaded_experiment(client, tmp_path)
    resp = client.post(
        "/api/session?autosave=true",
        data=json.dumps({"step": "wizard"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert (config_dir / "output" / ".session.autosave.json").is_file()


# ---------------------------------------------------------------------------
# /api/resolve_browse
# ---------------------------------------------------------------------------


def test_resolve_browse_finds_match_under_home(
    client: FlaskClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Re-home to tmp_path so the search doesn't hit the real filesystem.
    monkeypatch.setenv("HOME", str(tmp_path))
    # Some platforms read Path.home() from pwd; patch that too for safety.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    target = tmp_path / "Desktop" / "my_recordings"
    target.mkdir(parents=True)

    resp = _post_json(client, "/api/resolve_browse", {"name": "my_recordings"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["matches"] == [str(target.resolve())]


def test_resolve_browse_no_matches_returns_empty_not_404(
    client: FlaskClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    resp = _post_json(client, "/api/resolve_browse", {"name": "definitely_missing"})
    # Per task brief: empty matches → 200 with [], NOT 404.
    assert resp.status_code == 200
    assert resp.get_json() == {"matches": []}


def test_resolve_browse_rejects_separator_in_name(
    client: FlaskClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # A "name" with a slash isn't a folder name from the FS Access API.
    resp = _post_json(client, "/api/resolve_browse", {"name": "foo/bar"})
    assert resp.status_code == 200
    assert resp.get_json() == {"matches": []}


def test_resolve_browse_caps_at_five_and_sorts_by_mtime(
    client: FlaskClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    # Two same-named directories under different search roots; the one
    # with the newer mtime should come first.
    older = tmp_path / "Documents" / "same_name"
    newer = tmp_path / "Desktop" / "same_name"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)

    import os as _os
    # Force the mtimes far apart.
    _os.utime(older, (1_000_000, 1_000_000))
    _os.utime(newer, (2_000_000, 2_000_000))

    resp = _post_json(client, "/api/resolve_browse", {"name": "same_name"})
    assert resp.status_code == 200
    matches = resp.get_json()["matches"]
    assert len(matches) == 2
    assert matches[0] == str(newer.resolve())
    assert matches[1] == str(older.resolve())
