"""Core dataclasses, anchor type, and the Detector Protocol.

The canonical segment status field is ``status``; there
is no separate ``accepted`` boolean. Export's "is this segment selected by
default" rule is: word-typed AND ``status == "accepted"`` AND non-empty
``assigned_name``. Other modules (export in particular) import these types
from here — do not move them to ``labeling.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray


SegmentType = Literal["word", "short_noise", "crosstalk", "intro"]
LabelSource = Literal["", "auto", "anchor"]
SegmentStatus = Literal["pending", "accepted", "rejected", "intro"]
AnchorSource = Literal["initial", "user"]


@dataclass
class ProposedSegment:
    """A raw boundary proposal in source-audio seconds, no labeling yet."""

    start: float
    end: float
    duration_ms: float


@dataclass
class LabeledSegment(ProposedSegment):
    """A typed and (optionally) auto-labeled segment.

    ``token_index`` and ``cluster_size``
    are placeholders here; the export step fills them in.
    """

    segment_type: SegmentType = "word"
    assigned_name: str = ""
    label_source: LabelSource = ""
    status: SegmentStatus = "pending"
    token_index: int = 0
    cluster_size: int = 0


@dataclass
class LabelAnchor:
    """An anchor: 'word-typed-segment index i gets exactly this label.'

    Anchors are indexed by position among WORD-typed segments only, so
    reclassifying a segment as short_noise / crosstalk during review does
    not shift downstream anchors (per 03 doc "Notes on the algorithm").
    An anchor with ``label == ""`` halts forward labeling from that index.
    """

    word_index: int
    label: str
    source: AnchorSource


@dataclass
class DetectionResult:
    """Detector output: segments plus an ``analysis`` dict for the waveform UI.

    Recognized ``analysis`` keys (all optional):
      - ``"times"``:           np.ndarray[float]  — time axis for the energy plot
      - ``"energy"``:          np.ndarray[float]  — RMS / log-mel envelope
      - ``"is_speech"``:       np.ndarray[bool]   — frame-level VAD mask
      - ``"energy_threshold"``: float             — threshold used for plotting
    """

    segments: list[ProposedSegment]
    analysis: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Detector(Protocol):
    """Common detector interface. All backends accept mono float32 audio."""

    name: str
    requires_extras: list[str]

    @classmethod
    def is_available(cls) -> bool: ...

    def detect(
        self,
        audio: NDArray[np.float32],
        sr: int,
        *,
        min_segment_ms: int,
        min_silence_ms: int,
        silence_margin_ms: int,
        **backend_specific: Any,
    ) -> DetectionResult: ...
