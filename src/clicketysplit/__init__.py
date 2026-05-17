"""clicketysplit — segment and export word tokens from speech recordings."""

try:
    from ._version import __version__  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - editable install before hatch-vcs runs
    __version__ = "0.0.0+local"

__all__ = ["__version__"]
