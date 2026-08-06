"""Detection backends registry.

Importing this module is always safe — the optional backends guard their own
heavy imports internally and surface availability via :meth:`is_available`.
:func:`get_detector` raises ``RuntimeError`` (with a pip-install hint) when
asked for an unavailable backend, and ``KeyError`` for an unknown name.
"""

from __future__ import annotations

from typing import Any

from .base import (
    DetectionResult,
    Detector,
    LabelAnchor,
    LabeledSegment,
    ProposedSegment,
)
from .labeling import auto_label, classify_segments
from .refinement import compute_energy_envelope, refine_boundary
from .silero import SileroDetector
from .webrtc import WebRTCDetector

__all__ = [
    "DetectionResult",
    "Detector",
    "LabelAnchor",
    "LabeledSegment",
    "ProposedSegment",
    "SileroDetector",
    "WebRTCDetector",
    "auto_label",
    "available_detectors",
    "classify_segments",
    "compute_energy_envelope",
    "get_detector",
    "refine_boundary",
]


# Values are detector CLASSES. Not `type[Detector]`: Detector is a Protocol,
# and mypy rejects concrete classes there because a Protocol type isn't
# instantiable. The three uses below (is_available, requires_extras, and
# calling it) are covered by the Detector protocol on the returned instance.
_REGISTRY: dict[str, Any] = {
    "silero": SileroDetector,
    "webrtc": WebRTCDetector,
}


def get_detector(name: str) -> Detector:
    """Instantiate a detector by registered name.

    Raises ``KeyError`` if ``name`` is not registered, and ``RuntimeError``
    (with a pip-install hint) if the backend is registered but unavailable.
    """
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown detector {name!r}. Known: {sorted(_REGISTRY)}"
        )
    cls = _REGISTRY[name]
    if not cls.is_available():
        extras = ",".join(cls.requires_extras)
        raise RuntimeError(
            f"Detector {name!r} requires extras: {cls.requires_extras}. "
            f"Install with: pip install 'clicketysplit[{extras}]'"
        )
    return cls()


def available_detectors() -> list[str]:
    """Names of detectors whose optional deps are present in this environment."""
    return [name for name, cls in _REGISTRY.items() if cls.is_available()]
