"""JSON load/save of in-progress review state.

The Setup Wizard and the review UI persist their UI state to
``<output_root>/.session.json`` (per-experiment, NOT
per-condition). A second ``<output_root>/.session.autosave.json`` is
written by the autosave path; on load the newer of the two wins.

No pickle. No schema. The frontend writes whatever JSON it needs and the
backend just round-trips it.

The ``output_subdir`` parameter mirrors ``ExperimentConfig.output_root``
so route handlers can pass the active config's value through. We don't
import ``config`` here — that would create an import cycle and force a
specific load path on callers (tests in particular).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

__all__ = [
    "latest_session_path",
    "load_session",
    "save_session",
]


_SESSION_NAME = ".session.json"
_AUTOSAVE_NAME = ".session.autosave.json"


def _session_dir(experiment_dir: Path, output_subdir: str) -> Path:
    """Return ``<experiment_dir>/<output_subdir>`` (no I/O)."""
    return Path(experiment_dir) / output_subdir


def latest_session_path(
    experiment_dir: Path, output_subdir: str = "output"
) -> Path | None:
    """Return whichever of ``.session.json``/``.session.autosave.json`` is newer.

    Returns ``None`` if neither file exists. mtime is compared via
    ``st_mtime_ns`` so two writes within the same second still order
    correctly on filesystems that expose ns timestamps.
    """
    base = _session_dir(experiment_dir, output_subdir)
    session_p = base / _SESSION_NAME
    autosave_p = base / _AUTOSAVE_NAME

    have_session = session_p.is_file()
    have_autosave = autosave_p.is_file()
    if not have_session and not have_autosave:
        return None
    if have_session and not have_autosave:
        return session_p
    if have_autosave and not have_session:
        return autosave_p
    # Both exist — pick whichever has the newer mtime.
    if autosave_p.stat().st_mtime_ns >= session_p.stat().st_mtime_ns:
        return autosave_p
    return session_p


def load_session(
    experiment_dir: Path, output_subdir: str = "output"
) -> dict[str, Any]:
    """Load the most-recent session JSON for an experiment.

    Returns ``{}`` if neither ``.session.json`` nor ``.session.autosave.json``
    exists. Errors on malformed JSON — there's no schema, but the file IS
    expected to be a JSON object the frontend wrote.
    """
    path = latest_session_path(experiment_dir, output_subdir)
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(
            f"Session file {path} did not contain a JSON object "
            f"(got {type(data).__name__})."
        )
    return data


def save_session(
    experiment_dir: Path,
    data: dict[str, Any],
    *,
    autosave: bool = False,
    output_subdir: str = "output",
) -> Path:
    """Write ``data`` to the appropriate session file atomically.

    Atomicity uses a ``NamedTemporaryFile`` in the destination directory
    plus ``os.replace``, so a crash mid-write never leaves a half-written
    ``.session.json``. Returns the path written.

    The output directory is created if it doesn't already exist (the
    wizard may call this before the first detection run that would
    otherwise create ``output/``).
    """
    base = _session_dir(experiment_dir, output_subdir)
    base.mkdir(parents=True, exist_ok=True)

    out_path = base / (_AUTOSAVE_NAME if autosave else _SESSION_NAME)

    # Write the JSON to a sibling tmpfile in the same dir (so os.replace is
    # atomic on POSIX and best-effort atomic on Windows), then rename.
    fd, tmp_name = tempfile.mkstemp(
        prefix=out_path.name + ".", suffix=".tmp", dir=str(base)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp_path, out_path)
    except Exception:
        # Best-effort cleanup: if rename failed, don't leave the tmpfile behind.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return out_path
