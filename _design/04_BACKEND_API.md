# 04 — Backend HTTP API

## Design principles

1. **One active experiment per server process — be honest about it.**
   `app.config["experiment_path"]` holds the path of the currently active
   experiment. `/api/config/load` mutates it; every other route reads it.
   All durable state lives on disk inside the experiment directory; nothing
   important is kept only in memory. This is **not** REST-statelessness, and
   pretending otherwise creates surprising bugs.
   - **Concurrency constraint**: opening two browser tabs against the same
     server, each pointing at a different experiment, is unsupported. The
     tab that loaded later wins. The frontend writes the active
     `experiment_path` into the page title so the user can spot collisions.
     Future: support multi-experiment by namespacing on a query param
     `?exp=<path>`; out of scope for v0.1.0.
2. **One Python function per route, calling one engine function.** Routes are
   HTTP-translation layers; all real logic lives in `detection.pipeline`,
   `export.tokens`, `discovery`, etc.
3. **JSON in, JSON out** (audio streaming is the only exception).
4. **Errors are structured.** `{ "error": { "code": "...", "message": "..." } }`
   with appropriate HTTP status. Never leak raw tracebacks to the client
   except in debug mode.
5. **Safe path handling.** All filesystem reads are validated to stay inside
   the experiment directory.

## App factory

```python
# src/clicketysplit/server/app.py
def create_app(*, experiment_path: Path | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["experiment_path"] = experiment_path
    register_routes(app)
    register_static(app)        # serve the pre-built Svelte assets
    register_error_handlers(app)
    return app
```

Notes:
- `create_app` does NOT load the experiment config eagerly. The current
  active config is re-read from `app.config["experiment_path"]` on each
  request that needs it (or cached behind a `functools.lru_cache(maxsize=1)`
  keyed on the path's mtime). The factory stays testable.
- `/api/config/load` mutates `app.config["experiment_path"]`. This is the
  one place that flips the active experiment.
- The frontend includes the active `experiment_path` in a small
  status-bar UI element so the user knows what they're acting on. The
  `GET /api/config` response includes it explicitly.

## Route inventory

### Capabilities and meta

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/version` | Return server version + supported audio formats + available detectors |
| GET | `/api/capabilities` | `{ detectors: [...], denoise_available: bool, textgrid_available: bool, audio_formats: [...] }` |

### Config and discovery

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/discover` | Body: `{ "root": "/abs/path" }`. Returns a `DiscoveryResult`. |
| GET | `/api/config` | Returns the currently-loaded config, or 404 if none loaded |
| POST | `/api/config` | Body: `{ "config_dir": "/abs/path", "config": {...} }`. Validates with pydantic, writes `clicketysplit.json`, sets it active. |
| POST | `/api/config/load` | Body: `{ "path": "/abs/path/clicketysplit.json" }`. Loads and validates an existing config, sets it active. |

### Stimulus lists

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/stimulus_lists` | List files under `stimulus_lists_root` |
| GET | `/api/stimulus_lists/<name>` | Return the words in a stimulus list |

`<name>` is filename-only — paths are rejected. The handler joins it onto the
configured `stimulus_lists_root` and verifies the resolved path stays inside
the experiment dir.

### Audio streaming

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/audio/source/<speaker>/<condition>/<file_idx>` | Stream an individual source recording. `file_idx` indexes into `source_files` from `proposed_segments.json`. |
| GET | `/api/audio/working/<speaker>/<condition>` | Stream the working audio used by the review UI. This is `denoised.wav` if it exists, else the concatenated source. |
| GET | `/api/overview/<speaker>/<condition>` | Serve the detection-time overview PNG (`output/<speaker>/<condition>/overview.png`). Returns 404 if detection hasn't run yet. Distinct from the audio routes because it serves an image, not audio. |

Both endpoints set `Cache-Control: no-cache` and support `Range` requests
(Flask's `send_file` does this by default — verify in the smoke test).

### Detection

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/detect` | Body: `{ "speaker_id": "...", "condition": "..." }`. Runs the configured detector. Returns `{ "status": "ok", "n_segments": N, ... }`. |
| POST | `/api/detect_all` | Body: `{ "speakers": [...], "conditions": [...] }` (omit for "all configured"). Runs detection for every combination that has recordings. Returns a list of per-pair results. |

Re-running detection on a speaker×condition that already has
`reviewed_segments.json` returns a `409` with `{ "code": "already_reviewed",
"message": "...", "ack_url": "..." }`. The client confirms with the user, then
calls `POST /api/detect` with `force: true` to overwrite.

### Segments

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/segments/<speaker>/<condition>` | Returns the **reviewed** segments JSON if present, else the **proposed** one. Includes `_source` field indicating which file was returned. |
| POST | `/api/segments/<speaker>/<condition>` | Body: the full segments JSON. Persists to `output/<speaker>/<condition>/reviewed_segments.json`. |

We do not support PATCH/partial updates. The review UI re-saves the whole
condition on each "accept" — these files are small (hundreds of segments
max). This avoids merge logic and keeps the API trivial.

### Session (in-progress UI state)

This is **per-experiment**, not per-condition. It stores what step the user
is on, which condition they're reviewing, which token index, zoom level, etc.
Written by the frontend whenever something interesting changes.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/session` | Returns `output/.session.json` for the active experiment, or `{}` |
| POST | `/api/session` | Body: arbitrary JSON. Replaces `.session.json`. |

A second autosave file `.session.autosave.json` is written every N seconds by
the backend's autosave timer so a crash doesn't lose progress. On load, if
the autosave is newer than `session.json`, the frontend offers to restore.

**No pickle anywhere.** Both files are plain JSON. Schema is loose — the
frontend writes whatever it needs.

### Export

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/export` | Body: `{ "speaker_id": "...", "condition": "...", "selected_tokens": { word: [indices] } | null }`. If `selected_tokens` is null, export every word-typed segment with `status == "accepted"` and a non-empty `assigned_name`. Writes tokens, manifest, csv (and TextGrid if enabled). |
| POST | `/api/export_all` | Body: `{ "speakers": [...], "conditions": [...], "selections": { spk: { cond: {...} } } }`. Iterates and reports per-pair results. |

## Concrete request/response shapes

### POST /api/detect

Request:
```json
{
  "speaker_id": "speaker_01",
  "condition": "condition_a",
  "force": false
}
```

Response 200:
```json
{
  "status": "ok",
  "speaker_id": "speaker_01",
  "condition": "condition_a",
  "n_segments": 32,
  "n_words": 30,
  "audio_duration_sec": 80.98,
  "detector": "silero",
  "stimulus_list": ["apple", "banana", "..."],
  "overview_png": "/api/overview/speaker_01/condition_a"
}
```

Response 409 (already reviewed):
```json
{
  "error": {
    "code": "already_reviewed",
    "message": "Reviewed segments already exist for this condition. Pass force=true to overwrite.",
    "path": "output/speaker_01/condition_a/reviewed_segments.json"
  }
}
```

### POST /api/segments/speaker_01/condition_a

Request:
```json
{
  "schema_version": 1,
  "segments": [
    { "start": 1.23, "end": 1.78, "duration_ms": 550, "segment_type": "word",
      "assigned_name": "apple", "status": "accepted",
      "token_index": 1, "cluster_size": 3 }
  ],
  "stimulus_list": [...]
}
```

Response 200: `{ "status": "ok", "path": "output/.../reviewed_segments.json" }`

### POST /api/export

Request:
```json
{
  "speaker_id": "speaker_01",
  "condition": "condition_a",
  "selected_tokens": {
    "apple":  [1, 3],
    "banana": [2]
  }
}
```

Response 200:
```json
{
  "status": "ok",
  "n_exported": 3,
  "tokens_per_word": { "apple": 2, "banana": 1 },
  "output_dir": "output/speaker_01/condition_a/tokens",
  "files": [
    "speaker_01_apple-1.wav",
    "speaker_01_apple-3.wav",
    "speaker_01_banana-2.wav"
  ],
  "manifest": "tokens/token_manifest.json",
  "csv": "tokens/tokens.csv"
}
```

## Error handling

```python
class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, **extras):
        self.code = code; self.message = message
        self.status = status; self.extras = extras

@app.errorhandler(ApiError)
def _handle_api_error(e: ApiError):
    return jsonify({"error": {"code": e.code, "message": e.message, **e.extras}}), e.status

@app.errorhandler(Exception)
def _handle_unexpected(e: Exception):
    if app.debug:
        raise
    app.logger.exception("Unexpected error")
    return jsonify({"error": {"code": "internal", "message": "Internal server error"}}), 500
```

Common error codes:
- `no_experiment` — no config loaded; client must POST `/api/config/load` first
- `not_found` — speaker/condition/file missing
- `validation_error` — pydantic rejected a config or segments payload (includes field path)
- `missing_extra` — detector unavailable; includes `extra` name
- `already_reviewed` — see above
- `bad_audio` — file couldn't be decoded (corrupt, unsupported format)

## Path safety

```python
def safe_resolve(experiment_dir: Path, *parts: str) -> Path:
    """
    Join parts onto experiment_dir and return the resolved path.
    Raises ApiError('path_outside_experiment') if the result escapes.
    """
    p = (experiment_dir / Path(*parts)).resolve()
    if experiment_dir.resolve() not in p.parents and p != experiment_dir.resolve():
        raise ApiError("path_outside_experiment", str(p), status=400)
    return p
```

Every route that takes a path-like parameter uses this. Today's `app.py`
does ad-hoc `Path(filename).name` to prevent traversal; centralize it.

## Static serving (frontend assets)

```python
def register_static(app: Flask):
    static_dir = Path(__file__).parent / "static"
    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def serve_spa(path: str):
        # /api/* paths are handled by other routes; this is for SPA routing.
        candidate = (static_dir / path).resolve()
        if path and static_dir in candidate.parents and candidate.is_file():
            return send_from_directory(static_dir, path)
        # SPA fallback: serve index.html for any unmatched path
        return send_from_directory(static_dir, "index.html")
```

Caches: `Cache-Control: no-cache` for `index.html` (so frontend updates are
picked up after pip-upgrading), `Cache-Control: public, max-age=31536000,
immutable` for fingerprinted asset files (`app-<hash>.js`).

## Threading model

Flask's dev server is single-threaded with `threaded=False`. For local single
user this is fine; one detect-all run at a time. Detection can take 30+
seconds for long recordings, so the frontend uses long-polling status (each
detect call blocks until done, with a spinner) — no need for SSE/WebSocket
streaming in v1.

For LAN deployment (`--host 0.0.0.0`), we explicitly print a warning that
the dev server isn't production-grade and that no auth is in place. v1 does
not bundle a production WSGI server; we document `pip install waitress &&
waitress-serve --port 5000 clicketysplit.server.app:create_app` for users who
want one.
