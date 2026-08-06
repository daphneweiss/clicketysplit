"""Standalone Python-side parity check against ``labeling_test_vectors.json``.

The vectors file is the contract shared between this implementation and the
TypeScript port in the review UI. Both implementations are expected to walk
the same set of cases and produce the same labels. Keeping this check in its
own file (separate from the broader detection tests) makes the
cross-language pointer obvious — a future TS test points at the same
``labeling_test_vectors.json`` and asserts the same shape.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from clicketysplit.detection import (
    LabelAnchor,
    LabeledSegment,
    auto_label,
)

_VECTORS_PATH = Path(__file__).parent / "labeling_test_vectors.json"


def _load_vectors_file() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_VECTORS_PATH.read_text(encoding="utf-8"))
    return data


def _word_segments(n: int) -> list[LabeledSegment]:
    """Build ``n`` word-typed segments at 1 s strides, 500 ms each."""
    return [
        LabeledSegment(
            start=float(i),
            end=float(i) + 0.5,
            duration_ms=500.0,
            segment_type="word",
        )
        for i in range(n)
    ]


def test_vectors_file_schema_version_is_one() -> None:
    data = _load_vectors_file()
    assert data["schema_version"] == 1, (
        f"Unexpected schema_version {data['schema_version']!r}; "
        "the Python and TypeScript implementations agree on schema_version=1."
    )
    assert isinstance(data["vectors"], list)
    assert len(data["vectors"]) > 0


@pytest.mark.parametrize(
    "vec",
    _load_vectors_file()["vectors"],
    ids=lambda v: v["name"],
)
def test_auto_label_matches_vector(vec: dict[str, Any]) -> None:
    """For every vector, ``auto_label`` must produce ``expected_labels``."""
    if "segments" in vec:
        segments = [
            LabeledSegment(
                start=s["start"],
                end=s["end"],
                duration_ms=round((s["end"] - s["start"]) * 1000, 1),
                segment_type="word",
            )
            for s in vec["segments"]
        ]
    else:
        segments = _word_segments(vec["word_segment_count"])
    anchors = [
        LabelAnchor(
            word_index=a["word_index"],
            label=a["label"],
            source=a["source"],
        )
        for a in vec["anchors"]
    ]
    result = auto_label(
        segments,
        vec["stimulus_list"],
        presentation_order=vec["presentation_order"],
        expected_reps_per_stimulus=vec["expected_reps_per_stimulus"],
        anchors=anchors,
    )
    actual = [s.assigned_name for s in result if s.segment_type == "word"]
    assert actual == vec["expected_labels"], (
        f"Vector {vec['name']!r}: expected {vec['expected_labels']}, got {actual}"
    )
