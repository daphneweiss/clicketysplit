"""End-to-end smoke tests for the Flask server.

Exercises the app factory, every non-task-8 route, the structured-error
contract, and the SPA static fallback. Uses Flask's test client; spins up
a fresh experiment per test under ``tmp_path``.

Each test creates its own app via :func:`create_app`,
so no module-level Flask state leaks between tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import soundfile as sf
from flask import Flask
from flask.testing import FlaskClient

from clicketysplit import __version__
from clicketysplit.server import create_app

# ---------------------------------------------------------------------------
# Fixture: tiny synthetic experiment + loaded app
# ---------------------------------------------------------------------------


def _synth_two_word_recording(path: Path) -> None:
    """Copy the bundled demo clip (real speech) to ``path``.

    Detection tests need real speech: Silero is trained on it and correctly
    ignores synthetic tones. The bundled demo asset is ~23 s of massed word
    repetitions, so it exercises the whole detect -> classify -> label path.
    """
    from clicketysplit import demo_assets

    src = Path(demo_assets.__file__).parent / "demo_real_words.wav"
    audio, real_sr = sf.read(str(src), dtype="float32")
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), audio, real_sr, subtype="FLOAT")

def _write_experiment(root: Path, *, denoise: bool = False) -> Path:
    """Create a minimal experiment under ``root`` and return the config path."""
    recordings_root = root / "recordings"
    stim_root = root / "stimulus_lists"
    output_root = root / "output"
    stim_root.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)

    (stim_root / "cond_a.txt").write_text("apple\nbanana\n", encoding="utf-8")
    _synth_two_word_recording(recordings_root / "speaker_01" / "cond_a" / "take1.wav")

    config_data = {
        "schema_version": 1,
        "name": "smoke",
        "recordings_root": "recordings",
        "stimulus_lists_root": "stimulus_lists",
        "output_root": "output",
        "speakers": [{"id": "speaker_01", "subdir": "speaker_01"}],
        "conditions": [
            {
                "name": "cond_a",
                "stimulus_list": "stimulus_lists/cond_a.txt",
                "presentation_order": "random",
                "expected_reps_per_stimulus": 3,
            }
        ],
        "detection": {
            "backend": "silero",
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
    return config_path


@pytest.fixture
def experiment(tmp_path: Path) -> Path:
    """Return the path to ``clicketysplit.json`` for a fresh synthetic experiment."""
    return _write_experiment(tmp_path)


@pytest.fixture
def experiment_dir(experiment: Path) -> Path:
    """Return the experiment ROOT directory (parent of the config file)."""
    return experiment.parent


@pytest.fixture
def app_unloaded() -> Flask:
    """An app with no experiment loaded."""
    return create_app(experiment_path=None)


@pytest.fixture
def app_loaded(experiment: Path) -> Flask:
    """An app with the synthetic experiment pre-loaded via the constructor."""
    return create_app(experiment_path=experiment)


@pytest.fixture
def client_unloaded(app_unloaded: Flask) -> FlaskClient:
    return app_unloaded.test_client()


@pytest.fixture
def client_loaded(app_loaded: Flask) -> FlaskClient:
    return app_loaded.test_client()


def _post_json(client: FlaskClient, url: str, body: dict[str, Any]) -> Any:
    return client.post(url, data=json.dumps(body), content_type="application/json")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_create_app_boots_without_experiment(app_unloaded: Flask) -> None:
    assert app_unloaded is not None
    assert app_unloaded.config["experiment_path"] is None


def test_create_app_boots_with_experiment_path(app_loaded: Flask, experiment: Path) -> None:
    assert app_loaded.config["experiment_path"] == str(experiment.resolve())


def test_health(client_unloaded: FlaskClient) -> None:
    resp = client_unloaded.get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}


def test_version(client_unloaded: FlaskClient) -> None:
    resp = client_unloaded.get("/api/version")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["version"] == __version__
    assert isinstance(data["supported_audio_formats"], list)
    assert ".wav" in data["supported_audio_formats"]
    assert "silero" in data["available_detectors"]


def test_capabilities(client_unloaded: FlaskClient) -> None:
    resp = client_unloaded.get("/api/capabilities")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "silero" in data["detectors"]
    assert data["denoise_available"] is True
    assert data["textgrid_available"] is True
    assert ".wav" in data["audio_formats"]


def test_get_config_no_experiment(client_unloaded: FlaskClient) -> None:
    resp = client_unloaded.get("/api/config")
    assert resp.status_code == 404
    err = resp.get_json()["error"]
    assert err["code"] == "no_experiment"


def test_post_config_load_then_get(client_unloaded: FlaskClient, experiment: Path) -> None:
    resp = _post_json(client_unloaded, "/api/config/load", {"path": str(experiment)})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["config"]["name"] == "smoke"

    get_resp = client_unloaded.get("/api/config")
    assert get_resp.status_code == 200
    config_payload = get_resp.get_json()
    assert config_payload["name"] == "smoke"
    assert config_payload["_experiment_path"] == str(experiment.resolve())


def test_post_config_load_nonexistent_path(client_unloaded: FlaskClient) -> None:
    resp = _post_json(client_unloaded, "/api/config/load", {"path": "/nope/missing.json"})
    assert resp.status_code == 400
    err = resp.get_json()["error"]
    assert err["code"] == "validation_error"


def test_post_config_load_missing_field(client_unloaded: FlaskClient) -> None:
    resp = _post_json(client_unloaded, "/api/config/load", {})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "validation_error"


def test_stimulus_lists_index(client_loaded: FlaskClient) -> None:
    resp = client_loaded.get("/api/stimulus_lists")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["files"] == ["cond_a.txt"]


def test_stimulus_list_get_returns_lines(client_loaded: FlaskClient) -> None:
    resp = client_loaded.get("/api/stimulus_lists/cond_a.txt")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["lines"] == ["apple", "banana"]


def test_stimulus_list_traversal_rejected(client_loaded: FlaskClient) -> None:
    # Flask's <name> string converter forbids '/' in the captured slot, so
    # the only traversal a client can land here is a bare '..'. safe_resolve
    # rejects it with path_outside_experiment before any disk access.
    resp = client_loaded.get("/api/stimulus_lists/..")
    assert resp.status_code == 400
    err = resp.get_json()["error"]
    assert err["code"] == "path_outside_experiment"


def test_detect_happy_path(
    client_loaded: FlaskClient, experiment_dir: Path
) -> None:
    resp = _post_json(
        client_loaded,
        "/api/detect",
        {"speaker_id": "speaker_01", "condition": "cond_a"},
    )
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["speaker_id"] == "speaker_01"
    assert data["condition"] == "cond_a"
    assert data["n_segments"] >= 1
    assert data["n_words"] >= 1
    assert data["detector"] == "silero"
    assert data["audio_duration_sec"] == pytest.approx(23.41, abs=0.1)
    assert data["overview_png"] == "/api/overview/speaker_01/cond_a"

    # On-disk side effects.
    cond_dir = experiment_dir / "output" / "speaker_01" / "cond_a"
    assert (cond_dir / "proposed_segments.json").is_file()
    assert (cond_dir / "overview.png").is_file()


def test_detect_already_reviewed_returns_409(
    client_loaded: FlaskClient, experiment_dir: Path
) -> None:
    # Run once to create proposed_segments, then drop a reviewed_segments.json
    # to simulate the user having reviewed.
    first = _post_json(
        client_loaded, "/api/detect",
        {"speaker_id": "speaker_01", "condition": "cond_a"},
    )
    assert first.status_code == 200
    cond_dir = experiment_dir / "output" / "speaker_01" / "cond_a"
    (cond_dir / "reviewed_segments.json").write_text(
        json.dumps({"schema_version": 1, "segments": []}), encoding="utf-8"
    )

    # Second detect without force → 409.
    blocked = _post_json(
        client_loaded, "/api/detect",
        {"speaker_id": "speaker_01", "condition": "cond_a"},
    )
    assert blocked.status_code == 409
    assert blocked.get_json()["error"]["code"] == "already_reviewed"

    # With force=True → proceeds.
    forced = _post_json(
        client_loaded, "/api/detect",
        {"speaker_id": "speaker_01", "condition": "cond_a", "force": True},
    )
    assert forced.status_code == 200, forced.get_json()


def test_get_segments_returns_proposed_then_reviewed(
    client_loaded: FlaskClient, experiment_dir: Path
) -> None:
    _post_json(
        client_loaded, "/api/detect",
        {"speaker_id": "speaker_01", "condition": "cond_a"},
    )

    proposed_resp = client_loaded.get("/api/segments/speaker_01/cond_a")
    assert proposed_resp.status_code == 200
    proposed_body = proposed_resp.get_json()
    assert proposed_body["_source"] == "proposed"
    assert isinstance(proposed_body["segments"], list)

    # Save reviewed (simplest acceptable payload).
    save_body = dict(proposed_body)
    save_body.pop("_source", None)
    # Mark first word-typed segment accepted + name so export has something.
    for seg in save_body["segments"]:
        if seg["segment_type"] == "word":
            seg["status"] = "accepted"
            seg["assigned_name"] = "apple"
            break
    save_resp = _post_json(
        client_loaded, "/api/segments/speaker_01/cond_a", save_body
    )
    assert save_resp.status_code == 200, save_resp.get_json()
    assert save_resp.get_json()["status"] == "ok"
    cond_dir = experiment_dir / "output" / "speaker_01" / "cond_a"
    assert (cond_dir / "reviewed_segments.json").is_file()

    # GET now returns reviewed.
    after_resp = client_loaded.get("/api/segments/speaker_01/cond_a")
    assert after_resp.status_code == 200
    assert after_resp.get_json()["_source"] == "reviewed"


def test_overview_png_404_before_detect(client_loaded: FlaskClient) -> None:
    resp = client_loaded.get("/api/overview/speaker_01/cond_a")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "not_found"


def test_overview_png_served_after_detect(client_loaded: FlaskClient) -> None:
    _post_json(
        client_loaded, "/api/detect",
        {"speaker_id": "speaker_01", "condition": "cond_a"},
    )
    resp = client_loaded.get("/api/overview/speaker_01/cond_a")
    assert resp.status_code == 200
    assert resp.mimetype == "image/png"
    assert len(resp.data) > 1024


def test_audio_source_streams_first_file(client_loaded: FlaskClient) -> None:
    _post_json(
        client_loaded, "/api/detect",
        {"speaker_id": "speaker_01", "condition": "cond_a"},
    )
    resp = client_loaded.get("/api/audio/source/speaker_01/cond_a/0")
    assert resp.status_code == 200
    assert resp.mimetype == "audio/wav"
    assert len(resp.data) > 1024
    assert resp.headers.get("Cache-Control") == "no-cache"


def test_audio_working_streams(client_loaded: FlaskClient) -> None:
    _post_json(
        client_loaded, "/api/detect",
        {"speaker_id": "speaker_01", "condition": "cond_a"},
    )
    resp = client_loaded.get("/api/audio/working/speaker_01/cond_a")
    assert resp.status_code == 200
    assert resp.mimetype in ("audio/wav", "audio/x-wav")
    assert len(resp.data) > 1024


def test_export_default_selection(
    client_loaded: FlaskClient, experiment_dir: Path
) -> None:
    _post_json(
        client_loaded, "/api/detect",
        {"speaker_id": "speaker_01", "condition": "cond_a"},
    )
    # Save reviewed segments with both words accepted & labeled, so the
    # default selection rule (C4) finds something to export.
    seg_path = experiment_dir / "output" / "speaker_01" / "cond_a" / "proposed_segments.json"
    payload = json.loads(seg_path.read_text(encoding="utf-8"))
    word_segs = [s for s in payload["segments"] if s["segment_type"] == "word"]
    assert len(word_segs) >= 1
    for i, seg in enumerate(word_segs):
        seg["status"] = "accepted"
        seg["assigned_name"] = "apple"
        seg["token_index"] = i + 1
    save_resp = _post_json(
        client_loaded, "/api/segments/speaker_01/cond_a", payload
    )
    assert save_resp.status_code == 200

    resp = _post_json(
        client_loaded, "/api/export",
        {"speaker_id": "speaker_01", "condition": "cond_a"},
    )
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["n_exported"] == len(word_segs)
    assert isinstance(data["files"], list)
    assert all(f.endswith(".wav") for f in data["files"])

    tokens_dir = experiment_dir / "output" / "speaker_01" / "cond_a" / "tokens"
    for fname in data["files"]:
        assert (tokens_dir / fname).is_file()
    assert (tokens_dir / "token_manifest.json").is_file()
    assert (tokens_dir / "tokens.csv").is_file()


def test_export_with_selected_tokens(
    client_loaded: FlaskClient, experiment_dir: Path
) -> None:
    _post_json(
        client_loaded, "/api/detect",
        {"speaker_id": "speaker_01", "condition": "cond_a"},
    )
    seg_path = experiment_dir / "output" / "speaker_01" / "cond_a" / "proposed_segments.json"
    payload = json.loads(seg_path.read_text(encoding="utf-8"))
    word_segs = [s for s in payload["segments"] if s["segment_type"] == "word"]
    # Two word-typed segments expected; label both 'apple' and accept.
    for seg in word_segs:
        seg["status"] = "accepted"
        seg["assigned_name"] = "apple"
    _post_json(client_loaded, "/api/segments/speaker_01/cond_a", payload)

    # Ask for only the first apple token.
    resp = _post_json(
        client_loaded, "/api/export",
        {
            "speaker_id": "speaker_01",
            "condition": "cond_a",
            "selected_tokens": {"apple": [1]},
        },
    )
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data["n_exported"] == 1


def test_safe_resolve_rejects_traversal(tmp_path: Path) -> None:
    # Unit-test the path-safety helper directly: Flask's URL converters
    # screen many traversal forms before they reach a handler, but
    # safe_resolve is the load-bearing line of defense and the smoke test
    # asserts its contract.
    from clicketysplit.server import ApiError, safe_resolve

    base = tmp_path
    with pytest.raises(ApiError) as exc:
        safe_resolve(base, "..", "evil")
    assert exc.value.code == "path_outside_experiment"

    with pytest.raises(ApiError) as exc:
        safe_resolve(base, "/etc", "passwd")
    assert exc.value.code == "path_outside_experiment"

    # Sanity: a normal sub-path is accepted and resolves inside ``base``.
    ok = safe_resolve(base, "output", "speaker_01")
    assert base.resolve() in ok.parents


def test_static_spa_placeholder_serves_without_built_frontend(
    client_unloaded: FlaskClient,
) -> None:
    # The repo ships a stub index.html so this returns 200; if the stub is
    # missing for any reason the placeholder JSON is acceptable too.
    resp = client_unloaded.get("/")
    assert resp.status_code == 200
    # Either the placeholder JSON or an HTML response is fine.
    assert resp.data, "expected non-empty SPA response"


def test_static_api_not_swallowed_by_spa_catchall(
    client_loaded: FlaskClient,
) -> None:
    # If register_static were registered first, /api/version would fall
    # through to the SPA catch-all. Verify the correct route still wins.
    resp = client_loaded.get("/api/version")
    assert resp.status_code == 200
    assert resp.get_json()["version"] == __version__
