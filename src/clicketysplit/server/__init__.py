"""Flask server package — re-exports the app factory from :mod:`.app`.

Per 01_PACKAGING.md and 04_BACKEND_API.md, ``create_app`` is the only
public entry point. Routes live in :mod:`.routes` and are registered by
:func:`.app.create_app` through :func:`.app.register_routes`.
"""

from __future__ import annotations

from .app import (
    ApiError,
    create_app,
    get_active_config,
    register_error_handlers,
    register_routes,
    register_static,
    safe_resolve,
)

__all__ = [
    "ApiError",
    "create_app",
    "get_active_config",
    "register_error_handlers",
    "register_routes",
    "register_static",
    "safe_resolve",
]
