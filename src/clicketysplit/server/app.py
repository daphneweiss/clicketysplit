"""Flask app factory, error handling, path safety, and config caching.

The app factory wires up routes from :mod:`.routes`, registers SPA static
serving, and installs the structured-error handlers. By design,
the currently active experiment's absolute path lives in
``app.config["experiment_path"]``; route handlers reach for it via
:func:`get_active_config`.

Adding a new route group is a one-liner: write the handler in
``routes.py``, then call its ``register_*`` function from
:func:`register_routes` here. Task 8 (setup-wizard write routes) will
follow that pattern.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from flask import Flask, jsonify, send_from_directory
from werkzeug.wrappers import Response as WerkzeugResponse

if TYPE_CHECKING:
    from ..config import ExperimentConfig


__all__ = [
    "ApiError",
    "create_app",
    "get_active_config",
    "register_error_handlers",
    "register_routes",
    "register_static",
    "safe_resolve",
]


# ---------------------------------------------------------------------------
# Structured errors (04 doc §"Error handling")
# ---------------------------------------------------------------------------


class ApiError(Exception):
    """Structured error raised by route handlers.

    The error handler in :func:`register_error_handlers` turns this into the
    ``{ "error": { "code", "message", ...extras } }`` envelope documented in
    04_BACKEND_API.md.
    """

    def __init__(
        self, code: str, message: str, status: int = 400, **extras: Any
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.extras = extras


# ---------------------------------------------------------------------------
# Path safety (04 doc §"Path safety")
# ---------------------------------------------------------------------------


def safe_resolve(experiment_dir: Path, *parts: str) -> Path:
    """Join ``parts`` onto ``experiment_dir`` and return the resolved path.

    Rejects any ``..`` traversal or absolute components defensively even
    though Flask's URL routing usually screens them. Raises
    :class:`ApiError` ``path_outside_experiment`` if the resolved path
    escapes ``experiment_dir``.
    """
    for part in parts:
        # Reject absolute components and traversal markers up-front.
        p = Path(part)
        if p.is_absolute():
            raise ApiError(
                "path_outside_experiment",
                f"Absolute path component not allowed: {part!r}",
                status=400,
            )
        if ".." in p.parts:
            raise ApiError(
                "path_outside_experiment",
                f"Path traversal not allowed in component: {part!r}",
                status=400,
            )

    base = experiment_dir.resolve()
    if not parts:
        return base
    joined = base / Path(*parts)
    # Containment check on the APPARENT path (no symlink following). The
    # per-part loop above already rejects ".." and absolute components, so
    # the join cannot escape via URL-supplied path traversal. It can only
    # escape if the user put a symlink inside their own experiment dir
    # pointing elsewhere — common (recordings often live on another disk),
    # and that's their call to make. Trust it.
    apparent = Path(os.path.normpath(joined))
    if apparent != base and base not in apparent.parents:
        raise ApiError(
            "path_outside_experiment",
            f"Path {apparent} escapes experiment dir {base}",
            status=400,
        )
    return joined.resolve()


# ---------------------------------------------------------------------------
# Active config (single source of truth: app.config)
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _load_config_cached(path_str: str, mtime_ns: int) -> ExperimentConfig:
    """LRU-cache the loaded ``ExperimentConfig`` keyed on (path, mtime_ns).

    Keying on ``mtime_ns`` means an external edit to ``clicketysplit.json``
    invalidates the cache on the next request automatically. ``maxsize=1``
    matches C1 — one active experiment per process.
    """
    from ..config import load_config  # local import to dodge a cycle

    return load_config(Path(path_str))


def get_active_config(app: Flask) -> ExperimentConfig:
    """Return the active experiment's :class:`ExperimentConfig`.

    Raises :class:`ApiError` ``no_experiment`` (404) if no experiment is
    loaded, or ``not_found`` (404) if ``app.config["experiment_path"]``
    points at a missing file. The cache is keyed on file mtime so a wizard
    edit visible to the user becomes visible to the backend on next read
    without any explicit invalidation.
    """
    exp_path_str = app.config.get("experiment_path")
    if not exp_path_str:
        raise ApiError(
            "no_experiment",
            "No experiment loaded. POST /api/config/load with a config path.",
            status=404,
        )
    config_path = Path(exp_path_str)
    if not config_path.is_file():
        raise ApiError(
            "not_found",
            f"Experiment config file not found: {config_path}",
            status=404,
        )
    return _load_config_cached(str(config_path.resolve()), config_path.stat().st_mtime_ns)


def _invalidate_config_cache() -> None:
    """Drop the cached :class:`ExperimentConfig`. Called from /api/config/load."""
    _load_config_cached.cache_clear()


# ---------------------------------------------------------------------------
# Error handlers (04 doc §"Error handling", verbatim shape)
# ---------------------------------------------------------------------------


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def _handle_api_error(e: ApiError) -> tuple[Any, int]:
        return (
            jsonify(
                {"error": {"code": e.code, "message": e.message, **e.extras}}
            ),
            e.status,
        )

    @app.errorhandler(Exception)
    def _handle_unexpected(e: Exception) -> tuple[Any, int]:
        # Re-raise in debug so Werkzeug's debugger still works.
        if app.debug:
            raise e
        app.logger.exception("Unexpected error")
        return (
            jsonify(
                {"error": {"code": "internal", "message": "Internal server error"}}
            ),
            500,
        )


# ---------------------------------------------------------------------------
# Static SPA serving (04 doc §"Static serving")
# ---------------------------------------------------------------------------


def register_static(app: Flask) -> None:
    """SPA static-asset serving. MUST be registered AFTER ``/api/*`` routes.

    If ``static/index.html`` does not exist (e.g., in tests where the
    frontend hasn't been built), the catch-all returns a small JSON
    placeholder so the smoke test can still boot the app. The CI build
    publishes ``index.html`` before packaging, so production users never
    see the placeholder.
    """
    static_dir = Path(__file__).parent / "static"

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def serve_spa(path: str) -> Any:
        index_html = static_dir / "index.html"
        if path:
            try:
                candidate = (static_dir / path).resolve()
            except OSError:
                candidate = static_dir
            if (
                static_dir.resolve() in candidate.parents
                and candidate.is_file()
            ):
                return send_from_directory(static_dir, path)
        # SPA fallback: serve index.html for any unmatched path.
        if index_html.is_file():
            resp = send_from_directory(static_dir, "index.html")
            # index.html is the version-anchored entrypoint: never cache it,
            # so pip-upgrading clicketysplit immediately flips users to the
            # new bundle. Hashed JS/CSS assets are cached by the browser.
            if isinstance(resp, WerkzeugResponse):
                resp.headers["Cache-Control"] = "no-cache"
            return resp
        # Placeholder for environments without a built frontend (tests).
        return jsonify(
            {
                "status": "ok",
                "message": (
                    "clicketysplit server is running, "
                    "but the frontend bundle is not built. "
                    "Run `npm run build` in frontend/ or use the API directly."
                ),
            }
        )


# ---------------------------------------------------------------------------
# Route registration entry point
# ---------------------------------------------------------------------------


def register_routes(app: Flask) -> None:
    """Wire route handlers from :mod:`.routes` onto ``app``.

    Each ``register_*`` call is independent; adding a new route group
    (e.g. task 8's setup-wizard write routes) is one extra line here
    plus the handler in :mod:`.routes`.
    """
    from . import routes

    routes.register_meta_routes(app)
    routes.register_config_routes(app)
    routes.register_stimulus_routes(app)
    routes.register_audio_routes(app)
    routes.register_overview_route(app)
    routes.register_detect_routes(app)
    routes.register_segments_routes(app)
    routes.register_export_routes(app)
    routes.register_setup_routes(app)


# ---------------------------------------------------------------------------
# App factory (04 doc §"App factory")
# ---------------------------------------------------------------------------


def create_app(experiment_path: Path | str | None = None) -> Flask:
    """Build a Flask app configured for clicketysplit.

    ``experiment_path`` may be ``None`` (no experiment loaded yet — the
    wizard's first screen), an absolute path string, or a :class:`Path`.
    """
    # static_folder=None: we manage static serving via register_static so the
    # SPA catch-all and the asset routes share one resolver.
    app = Flask(__name__, static_folder=None)
    app.config["experiment_path"] = (
        str(Path(experiment_path).resolve()) if experiment_path is not None else None
    )

    # Fresh app ⇒ fresh config cache, so unit tests can't see stale state
    # leak in from a previous test's app.
    _invalidate_config_cache()

    register_routes(app)
    register_error_handlers(app)
    # Static LAST so /api/* never gets swallowed by the SPA catch-all.
    register_static(app)
    return app


# A typing alias for route handlers that return Flask-acceptable values.
RouteHandler = Callable[..., Any]
