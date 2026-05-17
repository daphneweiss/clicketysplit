"""Flask app factory for clicketysplit.

The real routes land in task 7. For now, ``create_app`` returns a minimal
Flask app with a single ``/api/health`` route so the CLI can import this
package and CI smoke tests have something to hit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Flask


def create_app(experiment_path: Path | None = None) -> Flask:
    """Return a minimal Flask app.

    Parameters
    ----------
    experiment_path:
        Optional absolute path to a ``clicketysplit.json``. Stored on
        ``app.config["experiment_path"]`` for routes to consult later.
        Real loading/validation happens in task 7.
    """
    app = Flask(__name__)
    app.config["experiment_path"] = (
        str(Path(experiment_path).resolve()) if experiment_path is not None else None
    )

    @app.route("/api/health")
    def _health() -> Any:
        return {"ok": True}

    return app


__all__ = ["create_app"]
