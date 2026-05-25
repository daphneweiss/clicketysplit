"""HTTP route handlers — one Python function per route.

Routes are HTTP-translation layers: they validate request shapes, call into
the engine modules (``detection.pipeline``, ``export``, ``config``), and
serialize results. No detection / export / IO logic lives here.

Per CONTRACT_NOTES C1, the active experiment's path lives in
``app.config["experiment_path"]``; route handlers reach it via
:func:`get_active_config` rather than module-level state.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from flask import Flask, jsonify, request, send_file
from pydantic import ValidationError

from .. import __version__
from ..audio_io import SUPPORTED_EXTENSIONS, concatenate, load_audio
from ..config import ExperimentConfig, load_config, save_config
from ..denoise import HAS_NOISEREDUCE
from ..detection import available_detectors
from ..detection.base import LabeledSegment
from ..detection.pipeline import detect_for_condition
from ..discovery import (
    DiscoveryResult,
    ScannedCondition,
    ScannedSpeaker,
    scan_recordings,
)
from ..export import (
    HAS_PARSELMOUTH,
    build_manifest,
    export_tokens,
    write_manifest,
    write_textgrid,
    write_tokens_csv,
)
from ..session import load_session, save_session
from .app import ApiError, _invalidate_config_cache, get_active_config, safe_resolve


__all__ = [
    "register_audio_routes",
    "register_config_routes",
    "register_detect_routes",
    "register_export_routes",
    "register_meta_routes",
    "register_overview_route",
    "register_segments_routes",
    "register_setup_routes",
    "register_stimulus_routes",
]


# ---------------------------------------------------------------------------
# Helpers used across multiple handlers
# ---------------------------------------------------------------------------


def _json_body() -> dict[str, Any]:
    """Return the request's JSON body as a dict, or raise validation_error."""
    data = request.get_json(silent=True)
    if data is None:
        raise ApiError("validation_error", "Request body must be JSON")
    if not isinstance(data, dict):
        raise ApiError("validation_error", "Request body must be a JSON object")
    return data


def _require_str(data: dict[str, Any], field: str) -> str:
    val = data.get(field)
    if not isinstance(val, str) or not val:
        raise ApiError(
            "validation_error", f"Missing or invalid string field: {field!r}"
        )
    return val


def _condition_dir(config: ExperimentConfig, speaker: str, condition: str) -> Path:
    """Resolve ``<experiment>/<output_root>/<speaker>/<condition>`` safely."""
    assert config._config_dir is not None  # set by load_config
    return safe_resolve(config._config_dir, config.output_root, speaker, condition)


def _segments_from_payload(payload: dict[str, Any]) -> list[LabeledSegment]:
    """Re-hydrate ``LabeledSegment`` instances from a reviewed-segments JSON."""
    out: list[LabeledSegment] = []
    for s in payload.get("segments", []):
        out.append(
            LabeledSegment(
                start=float(s["start"]),
                end=float(s["end"]),
                duration_ms=float(s["duration_ms"]),
                segment_type=s.get("segment_type", "word"),
                assigned_name=s.get("assigned_name", ""),
                label_source=s.get("label_source", ""),
                status=s.get("status", "pending"),
                token_index=int(s.get("token_index", 0)),
                cluster_size=int(s.get("cluster_size", 0)),
            )
        )
    return out


def _filter_segments_by_selection(
    segments: Iterable[LabeledSegment],
    selected_tokens: dict[str, list[int]] | None,
) -> list[LabeledSegment]:
    """Per C4: word + status==accepted + non-empty assigned_name.

    If ``selected_tokens`` is provided, only segments whose
    ``assigned_name`` appears as a key AND whose 1-based per-word index
    is in the corresponding list are kept. Otherwise the full default-rule
    set is returned (export_tokens itself re-applies the filter, but doing
    it here makes the response's ``files``/``tokens_per_word`` accurate).
    """
    # Renumber token_index sequentially per assigned_name in source order so
    # the "1-based per-word index" semantics in selected_tokens are stable
    # against earlier reorderings.
    counter: dict[str, int] = defaultdict(int)
    eligible: list[tuple[int, LabeledSegment]] = []
    for seg in segments:
        if seg.segment_type != "word":
            continue
        if seg.status != "accepted":
            continue
        if not seg.assigned_name:
            continue
        counter[seg.assigned_name] += 1
        eligible.append((counter[seg.assigned_name], seg))

    if selected_tokens is None:
        return [seg for _, seg in eligible]

    selected_sets: dict[str, set[int]] = {
        name: set(int(i) for i in idxs) for name, idxs in selected_tokens.items()
    }
    return [
        seg
        for idx, seg in eligible
        if seg.assigned_name in selected_sets
        and idx in selected_sets[seg.assigned_name]
    ]


def _condition_audio_for_export(
    config: ExperimentConfig, speaker_id: str, condition_name: str
) -> tuple[Any, int]:
    """Return (mono float32 audio, sr) for the working audio of a condition.

    Prefers ``denoised.wav`` (written at detection time when
    ``config.detection.denoise=True`` and noisereduce is available);
    otherwise concatenates the source files the same way detection did.
    """
    cond_dir = _condition_dir(config, speaker_id, condition_name)
    denoised = cond_dir / "denoised.wav"
    if denoised.is_file():
        return load_audio(denoised)
    proposed = cond_dir / "proposed_segments.json"
    if not proposed.is_file():
        raise ApiError(
            "not_found",
            f"No detection output for {speaker_id}/{condition_name}; run /api/detect first.",
            status=404,
        )
    payload = json.loads(proposed.read_text(encoding="utf-8"))
    sources = payload.get("source_files", [])
    if not sources:
        raise ApiError(
            "bad_audio",
            "proposed_segments.json has no source_files entries",
            status=400,
        )
    assert config._config_dir is not None
    paths = [config._config_dir / s["path"] for s in sources]
    concat = concatenate(paths, silence_sec=0.5)
    return concat.audio, concat.sample_rate


# ---------------------------------------------------------------------------
# Meta / capabilities
# ---------------------------------------------------------------------------


def register_meta_routes(app: Flask) -> None:
    @app.route("/api/health")
    def health() -> Any:
        # Kept for CI smoke; intentionally cheap and stateless.
        return jsonify({"ok": True})

    @app.route("/api/version")
    def version() -> Any:
        return jsonify(
            {
                "version": __version__,
                "supported_audio_formats": sorted(SUPPORTED_EXTENSIONS),
                "available_detectors": available_detectors(),
            }
        )

    @app.route("/api/capabilities")
    def capabilities() -> Any:
        return jsonify(
            {
                "detectors": available_detectors(),
                "denoise_available": HAS_NOISEREDUCE,
                "textgrid_available": HAS_PARSELMOUTH,
                "audio_formats": sorted(SUPPORTED_EXTENSIONS),
            }
        )


# ---------------------------------------------------------------------------
# Config (read + load)
# ---------------------------------------------------------------------------


def register_config_routes(app: Flask) -> None:
    @app.route("/api/config", methods=["GET"])
    def get_config() -> Any:
        config = get_active_config(app)
        payload = json.loads(
            config.model_dump_json(exclude={"_config_dir"})
        )
        assert config._config_dir is not None
        payload["_experiment_path"] = app.config["experiment_path"]
        payload["_config_dir"] = str(config._config_dir)
        return jsonify(payload)

    @app.route("/api/config/load", methods=["POST"])
    def load_config_route() -> Any:
        data = _json_body()
        path_str = _require_str(data, "path")
        config_path = Path(path_str)
        if not config_path.is_file():
            # Per task brief we picked: non-existent path → 400 validation_error.
            raise ApiError(
                "validation_error",
                f"Config file not found at path: {config_path}",
                status=400,
            )
        try:
            cfg = load_config(config_path)
        except Exception as exc:
            raise ApiError(
                "validation_error",
                f"Failed to load config: {exc}",
                status=400,
            ) from exc
        # Mutate active experiment (C1) and invalidate the lru_cache so the
        # next get_active_config call sees this exact instance.
        from .app import _invalidate_config_cache

        app.config["experiment_path"] = str(config_path.resolve())
        _invalidate_config_cache()
        payload = json.loads(cfg.model_dump_json(exclude={"_config_dir"}))
        payload["_experiment_path"] = app.config["experiment_path"]
        assert cfg._config_dir is not None
        payload["_config_dir"] = str(cfg._config_dir)
        return jsonify({"status": "ok", "config": payload})


# ---------------------------------------------------------------------------
# Stimulus lists
# ---------------------------------------------------------------------------


def register_stimulus_routes(app: Flask) -> None:
    @app.route("/api/stimulus_lists", methods=["GET"])
    def list_stimulus_lists() -> Any:
        config = get_active_config(app)
        assert config._config_dir is not None
        stim_root = safe_resolve(config._config_dir, config.stimulus_lists_root)
        if not stim_root.is_dir():
            return jsonify({"stimulus_lists_root": config.stimulus_lists_root, "files": []})
        files = sorted(
            entry.name
            for entry in stim_root.iterdir()
            if entry.is_file() and not entry.name.startswith(".")
        )
        return jsonify(
            {
                "stimulus_lists_root": config.stimulus_lists_root,
                "files": files,
            }
        )

    @app.route("/api/stimulus_lists/<name>", methods=["GET"])
    def get_stimulus_list(name: str) -> Any:
        config = get_active_config(app)
        assert config._config_dir is not None
        # safe_resolve rejects ".." traversal even if a client crafts the URL
        # outside of Flask's default routing converters.
        path = safe_resolve(config._config_dir, config.stimulus_lists_root, name)
        if not path.is_file():
            raise ApiError(
                "not_found", f"Stimulus list not found: {name}", status=404
            )
        lines: list[str] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                lines.append(line)
        return jsonify({"name": name, "lines": lines})


# ---------------------------------------------------------------------------
# Audio streaming
# ---------------------------------------------------------------------------


def register_audio_routes(app: Flask) -> None:
    @app.route(
        "/api/audio/source/<speaker>/<condition>/<int:file_idx>",
        methods=["GET"],
    )
    def audio_source(speaker: str, condition: str, file_idx: int) -> Any:
        config = get_active_config(app)
        cond_dir = _condition_dir(config, speaker, condition)
        proposed = cond_dir / "proposed_segments.json"
        if not proposed.is_file():
            raise ApiError(
                "not_found",
                f"No detection output for {speaker}/{condition}; "
                "run /api/detect first.",
                status=404,
            )
        payload = json.loads(proposed.read_text(encoding="utf-8"))
        sources = payload.get("source_files", [])
        if not (0 <= file_idx < len(sources)):
            raise ApiError(
                "not_found",
                f"source_file index {file_idx} out of range (have {len(sources)})",
                status=404,
            )
        assert config._config_dir is not None
        rel = sources[file_idx]["path"]
        # Resolve under experiment dir for safety even though `rel` came from
        # a file we wrote ourselves — defensive against later renames.
        audio_path = safe_resolve(config._config_dir, rel)
        if not audio_path.is_file():
            raise ApiError(
                "not_found", f"Source audio file missing on disk: {rel}", status=404
            )
        response = send_file(
            audio_path,
            conditional=True,
            etag=True,
            mimetype=_audio_mimetype(audio_path),
        )
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.route("/api/audio/working/<speaker>/<condition>", methods=["GET"])
    def audio_working(speaker: str, condition: str) -> Any:
        config = get_active_config(app)
        cond_dir = _condition_dir(config, speaker, condition)
        denoised = cond_dir / "denoised.wav"
        if denoised.is_file():
            response = send_file(
                denoised, conditional=True, etag=True, mimetype="audio/wav"
            )
            response.headers["Cache-Control"] = "no-cache"
            return response

        proposed = cond_dir / "proposed_segments.json"
        if not proposed.is_file():
            raise ApiError(
                "not_found",
                f"No working audio for {speaker}/{condition}; run /api/detect first.",
                status=404,
            )
        payload = json.loads(proposed.read_text(encoding="utf-8"))
        sources = payload.get("source_files", [])
        if not sources:
            raise ApiError(
                "bad_audio",
                "proposed_segments.json has no source_files",
                status=400,
            )
        assert config._config_dir is not None
        if len(sources) == 1:
            audio_path = safe_resolve(config._config_dir, sources[0]["path"])
            if not audio_path.is_file():
                raise ApiError(
                    "not_found",
                    f"Source audio missing on disk: {sources[0]['path']}",
                    status=404,
                )
            response = send_file(
                audio_path,
                conditional=True,
                etag=True,
                mimetype=_audio_mimetype(audio_path),
            )
            response.headers["Cache-Control"] = "no-cache"
            return response

        # Multiple sources, no denoised.wav: concatenate-on-the-fly into a
        # NamedTemporaryFile and stream it. Per C1 the server is single-user,
        # so a tmpfile-per-request is fine for v0.1.0.
        paths = [config._config_dir / s["path"] for s in sources]
        concat = concatenate(paths, silence_sec=0.5)
        tmp = tempfile.NamedTemporaryFile(
            suffix=".wav", delete=False, prefix="cs_working_"
        )
        tmp_path = Path(tmp.name)
        tmp.close()
        # Save via soundfile through audio_io's writer to keep formats consistent.
        from ..audio_io import save_audio

        save_audio(tmp_path, concat.audio, concat.sample_rate, format="wav")
        response = send_file(
            tmp_path, conditional=True, etag=True, mimetype="audio/wav"
        )
        response.headers["Cache-Control"] = "no-cache"
        return response


def _audio_mimetype(path: Path) -> str:
    suf = path.suffix.lower()
    return {
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
    }.get(suf, "application/octet-stream")


# ---------------------------------------------------------------------------
# Overview PNG (CONTRACT_NOTES C10 — its own route, NOT under /api/audio)
# ---------------------------------------------------------------------------


def register_overview_route(app: Flask) -> None:
    @app.route("/api/overview/<speaker>/<condition>", methods=["GET"])
    def overview(speaker: str, condition: str) -> Any:
        config = get_active_config(app)
        cond_dir = _condition_dir(config, speaker, condition)
        png = cond_dir / "overview.png"
        if not png.is_file():
            raise ApiError(
                "not_found",
                f"overview.png not found for {speaker}/{condition}; "
                "run /api/detect first.",
                status=404,
            )
        return send_file(png, mimetype="image/png", conditional=True, etag=True)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _run_detection_one(
    config: ExperimentConfig,
    speaker_id: str,
    condition_name: str,
    *,
    force: bool,
) -> dict[str, Any]:
    """Shared logic for /api/detect and /api/detect_all (per 00 doc #4)."""
    cond_dir = _condition_dir(config, speaker_id, condition_name)
    reviewed = cond_dir / "reviewed_segments.json"
    if reviewed.is_file() and not force:
        raise ApiError(
            "already_reviewed",
            "Reviewed segments already exist for this condition. "
            "Pass force=true to overwrite.",
            status=409,
            path=str(reviewed),
        )
    if reviewed.is_file() and force:
        reviewed.unlink()

    try:
        result = detect_for_condition(config, speaker_id, condition_name)
    except ValueError as exc:
        # Engine signals unknown speaker / condition / missing audio with
        # ValueError; surface a structured error rather than 500.
        raise ApiError("not_found", str(exc), status=404) from exc

    proposed = json.loads(result.proposed_segments_path.read_text(encoding="utf-8"))
    return {
        "status": "ok",
        "speaker_id": result.speaker_id,
        "condition": result.condition,
        "n_segments": result.segment_count,
        "n_words": result.word_segment_count,
        "audio_duration_sec": result.audio_duration_sec,
        "detector": config.detection.backend,
        "stimulus_list": proposed.get("stimulus_list", []),
        "overview_png": f"/api/overview/{result.speaker_id}/{result.condition}",
    }


def register_detect_routes(app: Flask) -> None:
    @app.route("/api/detect", methods=["POST"])
    def detect_one() -> Any:
        config = get_active_config(app)
        data = _json_body()
        speaker_id = _require_str(data, "speaker_id")
        condition = _require_str(data, "condition")
        force = bool(data.get("force", False))
        return jsonify(_run_detection_one(config, speaker_id, condition, force=force))

    @app.route("/api/detect_all", methods=["POST"])
    def detect_all() -> Any:
        config = get_active_config(app)
        data = _json_body() if request.data else {}
        speakers = data.get("speakers")
        conditions = data.get("conditions")
        force = bool(data.get("force", False))

        speaker_ids = (
            [s.id for s in config.speakers]
            if speakers is None
            else [str(s) for s in speakers]
        )
        condition_names = (
            [c.name for c in config.conditions]
            if conditions is None
            else [str(c) for c in conditions]
        )

        results: list[dict[str, Any]] = []
        for sid in speaker_ids:
            for cname in condition_names:
                try:
                    results.append(
                        _run_detection_one(config, sid, cname, force=force)
                    )
                except ApiError as exc:
                    results.append(
                        {
                            "status": "error",
                            "speaker_id": sid,
                            "condition": cname,
                            "error": {
                                "code": exc.code,
                                "message": exc.message,
                                **exc.extras,
                            },
                        }
                    )
        return jsonify({"status": "ok", "results": results})


# ---------------------------------------------------------------------------
# Setup wizard write routes (task 8)
#
# These four routes cover the wizard's first-time experience: discover a
# recordings root, persist a new clicketysplit.json, read/write the
# per-experiment session JSON, and best-effort resolve a folder name handed
# back by the File System Access API into an absolute path.
#
# Per CONTRACT_NOTES C6 the recordings root is a TEXT FIELD; resolve_browse
# is opportunistic help, never a contract. Empty matches → 200 with [], not
# 404, so the wizard treats it as "user must type the path".
# ---------------------------------------------------------------------------


def _scanned_condition_to_dict(
    c: ScannedCondition, root: Path
) -> dict[str, Any]:
    """Serialize a ScannedCondition. Paths stay absolute (per dataclass spec).

    The wizard renders absolute paths back to the user; relativizing here
    would lose the disambiguation that the discovery root provides.
    """
    return {
        "name": c.name,
        "n_files": c.n_files,
        "total_bytes": c.total_bytes,
        "files": [str(f) for f in c.files],
    }


def _scanned_speaker_to_dict(s: ScannedSpeaker, root: Path) -> dict[str, Any]:
    return {
        "id": s.id,
        "subdir": s.subdir,
        "conditions": [_scanned_condition_to_dict(c, root) for c in s.conditions],
        "flat_files": [str(f) for f in s.flat_files],
    }


def _probe_stimulus_list_candidates(recordings_root: Path) -> list[str]:
    """List ``*.txt`` files in the conventional sibling ``stimulus_lists/`` dir.

    The wizard runs discovery before the config is saved, so the saved-config
    `/api/stimulus_lists` route can't answer yet. This probe lets the wizard
    pre-fill stimulus-list paths on a fresh setup by looking where the
    canonical layout (CONTRACT_NOTES C2) puts them: a sibling of
    ``recordings/``. Returns ``[]`` if the sibling doesn't exist or isn't a
    directory; the wizard treats that as "user types the path".
    """
    stim_dir = recordings_root.parent / "stimulus_lists"
    if not stim_dir.is_dir():
        return []
    return sorted(
        entry.name
        for entry in stim_dir.iterdir()
        if entry.is_file()
        and not entry.name.startswith(".")
        and entry.suffix.lower() == ".txt"
    )


def _discovery_result_to_dict(d: DiscoveryResult) -> dict[str, Any]:
    return {
        "root": str(d.root),
        "speakers": [_scanned_speaker_to_dict(s, d.root) for s in d.speakers],
        "unique_condition_names": list(d.unique_condition_names),
        "stimulus_list_files": _probe_stimulus_list_candidates(d.root),
    }


def _resolve_abs_browse_candidates(name: str) -> list[Path]:
    """Search common ``$HOME`` locations for a directory named ``name``.

    Best-effort assist for the wizard's "Browse" button (CONTRACT_NOTES C6).
    The text field is the contract; this just makes typical layouts a
    one-click pre-fill instead of a paste. Unreadable directories are
    skipped silently.
    """
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        return []

    home = Path.home()
    search_roots = [
        home,
        home / "Desktop",
        home / "Documents",
        home / "Downloads",
        home / "dev",
        home / "recordings",
    ]

    matches: list[Path] = []
    seen: set[Path] = set()
    for base in search_roots:
        try:
            if not base.is_dir():
                continue
        except OSError:
            continue
        try:
            entries = list(base.iterdir())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            try:
                if not entry.is_dir():
                    continue
            except OSError:
                continue
            if entry.name != name:
                continue
            try:
                resolved = entry.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            matches.append(resolved)

    # Sort by modified-time descending so the most-recently-used candidate
    # is offered first. Stat errors push an entry to the end.
    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return -1.0

    matches.sort(key=_mtime, reverse=True)
    return matches[:5]


def register_setup_routes(app: Flask) -> None:
    """Wire the four setup-wizard write routes onto ``app``."""

    @app.route("/api/discover", methods=["POST"])
    def discover_route() -> Any:
        data = _json_body()
        root_str = _require_str(data, "root")
        # The wizard hands us a path the user typed (or pasted from the
        # opportunistic Browse helper). No experiment is loaded yet, so
        # safe_resolve doesn't apply — but we still need an absolute path.
        candidate = Path(root_str).expanduser()
        if not candidate.is_absolute():
            raise ApiError(
                "validation_error",
                f"root must be an absolute path; got {root_str!r}",
                status=400,
            )
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise ApiError(
                "validation_error",
                f"Could not resolve root: {exc}",
                status=400,
            ) from exc
        if not resolved.exists():
            raise ApiError(
                "not_found",
                f"Recordings root does not exist: {resolved}",
                status=400,
            )
        if not resolved.is_dir():
            raise ApiError(
                "not_found",
                f"Recordings root is not a directory: {resolved}",
                status=400,
            )
        result = scan_recordings(resolved)
        return jsonify(_discovery_result_to_dict(result))

    @app.route("/api/config", methods=["POST"])
    def write_config_route() -> Any:
        # NB: this is the WIZARD WRITE route. The read counterpart and
        # /api/config/load both live in register_config_routes. Keeping
        # the wizard's write here lets task 7's read/load route group stay
        # untouched (per task brief).
        data = _json_body()
        config_dir_str = _require_str(data, "config_dir")
        config_payload = data.get("config")
        if not isinstance(config_payload, dict):
            raise ApiError(
                "validation_error",
                "Missing or invalid 'config' object in request body",
                status=400,
            )

        config_dir = Path(config_dir_str).expanduser()
        if not config_dir.is_absolute():
            raise ApiError(
                "validation_error",
                f"config_dir must be an absolute path; got {config_dir_str!r}",
                status=400,
            )

        # Either exists as a directory, or doesn't exist yet (we create it).
        if config_dir.exists() and not config_dir.is_dir():
            raise ApiError(
                "validation_error",
                f"config_dir exists but is not a directory: {config_dir}",
                status=400,
            )
        config_dir.mkdir(parents=True, exist_ok=True)

        # Phase 1: pydantic schema validation (no FS touch).
        try:
            cfg = ExperimentConfig.model_validate(config_payload)
        except ValidationError as exc:
            raise ApiError(
                "validation_error",
                f"Config payload failed schema validation: {exc}",
                status=400,
            ) from exc

        # Write to disk before phase 3 disk-refs check, per task brief:
        # the file IS written even if stimulus lists are missing, so the
        # wizard can iterate without re-typing every field.
        config_path = (config_dir / "clicketysplit.json").resolve()
        save_config(cfg, config_path)

        # Phase 3: disk-refs check via load_config (mirrors the existing
        # /api/config/load flow). On failure the file is on disk but we
        # report the validation error so the wizard can prompt the user
        # to fix stimulus_list paths.
        try:
            loaded = load_config(config_path)
        except Exception as exc:
            raise ApiError(
                "validation_error",
                (
                    f"Config saved to {config_path}, but disk references "
                    f"are missing: {exc}"
                ),
                status=400,
                path=str(config_path),
            ) from exc

        # Success: flip active experiment + invalidate the lru_cache so
        # the very next /api/config GET sees this exact config.
        app.config["experiment_path"] = str(config_path)
        _invalidate_config_cache()
        # Touch ``loaded`` so the variable isn't flagged unused; the side
        # effect we care about is the disk-refs check above.
        _ = loaded

        return jsonify({"status": "ok", "path": str(config_path)})

    @app.route("/api/session", methods=["GET"])
    def get_session_route() -> Any:
        config = get_active_config(app)
        assert config._config_dir is not None
        data = load_session(config._config_dir, config.output_root)
        return jsonify(data)

    @app.route("/api/session", methods=["POST"])
    def post_session_route() -> Any:
        config = get_active_config(app)
        assert config._config_dir is not None

        payload = _json_body()
        # ``autosave`` may be passed as either a body field or a query
        # flag (per task brief). The body field wins if both are present.
        # We pop it out so it isn't persisted as part of the session blob.
        if "autosave" in payload:
            autosave = bool(payload.pop("autosave"))
        else:
            autosave = request.args.get("autosave", "").lower() in {
                "1",
                "true",
                "yes",
            }

        written = save_session(
            config._config_dir,
            payload,
            autosave=autosave,
            output_subdir=config.output_root,
        )
        try:
            rel = written.relative_to(config._config_dir).as_posix()
        except ValueError:
            rel = str(written)
        return jsonify({"status": "ok", "path": rel})

    @app.route("/api/resolve_browse", methods=["POST"])
    def resolve_browse_route() -> Any:
        data = _json_body()
        name = _require_str(data, "name")
        matches = _resolve_abs_browse_candidates(name)
        # Empty matches → 200 with [], NOT a 404; the wizard treats it as
        # "user must type the path" rather than as an error (C6).
        return jsonify({"matches": [str(p) for p in matches]})


# ---------------------------------------------------------------------------
# Segments
# ---------------------------------------------------------------------------


def register_segments_routes(app: Flask) -> None:
    @app.route("/api/segments/<speaker>/<condition>", methods=["GET"])
    def get_segments(speaker: str, condition: str) -> Any:
        config = get_active_config(app)
        cond_dir = _condition_dir(config, speaker, condition)
        reviewed = cond_dir / "reviewed_segments.json"
        if reviewed.is_file():
            payload = json.loads(reviewed.read_text(encoding="utf-8"))
            payload["_source"] = "reviewed"
            return jsonify(payload)
        proposed = cond_dir / "proposed_segments.json"
        if proposed.is_file():
            payload = json.loads(proposed.read_text(encoding="utf-8"))
            payload["_source"] = "proposed"
            return jsonify(payload)
        raise ApiError(
            "not_found",
            f"No segments file found for {speaker}/{condition}",
            status=404,
        )

    @app.route("/api/segments/<speaker>/<condition>", methods=["POST"])
    def save_segments(speaker: str, condition: str) -> Any:
        config = get_active_config(app)
        cond_dir = _condition_dir(config, speaker, condition)
        cond_dir.mkdir(parents=True, exist_ok=True)
        payload = _json_body()
        # Atomic write: tmp then rename, so a crash never leaves a half-written file.
        out_path = cond_dir / "reviewed_segments.json"
        tmp_path = out_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp_path, out_path)
        # Path stored relative to the experiment dir keeps the response portable.
        assert config._config_dir is not None
        try:
            rel = out_path.relative_to(config._config_dir).as_posix()
        except ValueError:
            rel = str(out_path)
        return jsonify({"status": "ok", "path": rel})


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def _run_export_one(
    config: ExperimentConfig,
    speaker_id: str,
    condition_name: str,
    *,
    selected_tokens: dict[str, list[int]] | None,
) -> dict[str, Any]:
    cond_dir = _condition_dir(config, speaker_id, condition_name)
    # Prefer reviewed segments; fall back to proposed only as a convenience.
    reviewed = cond_dir / "reviewed_segments.json"
    proposed = cond_dir / "proposed_segments.json"
    if reviewed.is_file():
        seg_payload = json.loads(reviewed.read_text(encoding="utf-8"))
        audio_source_label = "reviewed_segments.json"
    elif proposed.is_file():
        seg_payload = json.loads(proposed.read_text(encoding="utf-8"))
        audio_source_label = "proposed_segments.json"
    else:
        raise ApiError(
            "not_found",
            f"No segments to export for {speaker_id}/{condition_name}",
            status=404,
        )

    segments = _segments_from_payload(seg_payload)
    filtered = _filter_segments_by_selection(segments, selected_tokens)

    audio, sr = _condition_audio_for_export(config, speaker_id, condition_name)

    export_result = export_tokens(
        audio,
        sr,
        filtered,
        cond_dir,
        speaker_id,
        pad_ms=config.export.pad_ms,
        fade_ms=config.export.fade_ms,
        audio_format=config.export.format,
    )

    # Manifest + CSV (CSV gated by export.produce_csv).
    assert config._config_dir is not None
    denoised = cond_dir / "denoised.wav"
    if denoised.is_file():
        try:
            audio_source_rel = denoised.relative_to(config._config_dir).as_posix()
        except ValueError:
            audio_source_rel = str(denoised)
    else:
        audio_source_rel = audio_source_label

    manifest = build_manifest(
        speaker_id=speaker_id,
        condition=condition_name,
        audio_source=audio_source_rel,
        sample_rate=int(sr),
        pad_ms=config.export.pad_ms,
        fade_ms=config.export.fade_ms,
        audio_format=config.export.format,
        tokens=export_result.tokens_written,
    )
    manifest_path = cond_dir / "tokens" / "token_manifest.json"
    write_manifest(manifest_path, manifest)

    csv_rel: str | None = None
    if config.export.produce_csv:
        csv_path = cond_dir / "tokens" / "tokens.csv"
        write_tokens_csv(
            csv_path,
            speaker_id=speaker_id,
            condition=condition_name,
            sample_rate=int(sr),
            audio_format=config.export.format,
            tokens=export_result.tokens_written,
        )
        try:
            csv_rel = csv_path.relative_to(config._config_dir).as_posix()
        except ValueError:
            csv_rel = str(csv_path)

    # Optional TextGrid alongside proposed_segments.json (CONTRACT_NOTES C2).
    if config.export.produce_textgrid and HAS_PARSELMOUTH:
        # Reconstruct the file_boundaries the TextGrid wants from
        # proposed_segments.json (cheap; this is metadata, not audio).
        boundaries: list[Any] = []
        if proposed.is_file():
            from ..audio_io import FileBoundary

            for s in seg_payload.get("source_files", []):
                boundaries.append(
                    FileBoundary(
                        path=config._config_dir / s["path"],
                        offset_sec=float(s.get("offset_sec", 0.0)),
                        duration_sec=float(s.get("duration_sec", 0.0)),
                    )
                )
        textgrid_path = cond_dir / f"{condition_name}.TextGrid"
        write_textgrid(
            audio_duration_sec=float(len(audio) / sr),
            tokens=export_result.tokens_written,
            all_segments=segments,
            file_boundaries=boundaries,
            path=textgrid_path,
        )

    tokens_per_word: dict[str, int] = defaultdict(int)
    for t in export_result.tokens_written:
        tokens_per_word[t.assigned_name] += 1

    try:
        tokens_dir_rel = (cond_dir / "tokens").relative_to(config._config_dir).as_posix()
        manifest_rel = manifest_path.relative_to(config._config_dir).as_posix()
    except ValueError:
        tokens_dir_rel = str(cond_dir / "tokens")
        manifest_rel = str(manifest_path)

    return {
        "status": "ok",
        "speaker_id": speaker_id,
        "condition": condition_name,
        "n_exported": len(export_result.tokens_written),
        "tokens_per_word": dict(tokens_per_word),
        "output_dir": tokens_dir_rel,
        "files": [t.filename for t in export_result.tokens_written],
        "manifest": manifest_rel,
        "csv": csv_rel,
        "skipped": {
            "not_accepted": export_result.skipped_not_accepted,
            "not_word": export_result.skipped_not_word,
            "unnamed": export_result.skipped_unnamed,
            "empty_slice": export_result.skipped_empty_slice,
        },
    }


def register_export_routes(app: Flask) -> None:
    @app.route("/api/export", methods=["POST"])
    def export_one() -> Any:
        config = get_active_config(app)
        data = _json_body()
        speaker_id = _require_str(data, "speaker_id")
        condition = _require_str(data, "condition")
        selected_raw = data.get("selected_tokens")
        selected: dict[str, list[int]] | None
        if selected_raw is None:
            selected = None
        elif isinstance(selected_raw, dict):
            selected = {
                str(k): [int(i) for i in v] for k, v in selected_raw.items()
            }
        else:
            raise ApiError(
                "validation_error",
                "selected_tokens must be a JSON object mapping word→[indices] "
                "or null",
            )
        return jsonify(_run_export_one(config, speaker_id, condition, selected_tokens=selected))

    @app.route("/api/export_all", methods=["POST"])
    def export_all_route() -> Any:
        config = get_active_config(app)
        data = _json_body() if request.data else {}
        speakers = data.get("speakers")
        conditions = data.get("conditions")
        selections = data.get("selections") or {}

        speaker_ids = (
            [s.id for s in config.speakers]
            if speakers is None
            else [str(s) for s in speakers]
        )
        condition_names = (
            [c.name for c in config.conditions]
            if conditions is None
            else [str(c) for c in conditions]
        )

        results: list[dict[str, Any]] = []
        for sid in speaker_ids:
            spk_sel = selections.get(sid) if isinstance(selections, dict) else None
            for cname in condition_names:
                cond_sel = None
                if isinstance(spk_sel, dict):
                    raw = spk_sel.get(cname)
                    if isinstance(raw, dict):
                        cond_sel = {
                            str(k): [int(i) for i in v] for k, v in raw.items()
                        }
                try:
                    results.append(
                        _run_export_one(
                            config, sid, cname, selected_tokens=cond_sel
                        )
                    )
                except ApiError as exc:
                    results.append(
                        {
                            "status": "error",
                            "speaker_id": sid,
                            "condition": cname,
                            "error": {
                                "code": exc.code,
                                "message": exc.message,
                                **exc.extras,
                            },
                        }
                    )
        return jsonify({"status": "ok", "results": results})
