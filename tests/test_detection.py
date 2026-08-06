"""Tests for clicketysplit.detection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from clicketysplit.detection import (
    DetectionResult,
    LabelAnchor,
    LabeledSegment,
    ProposedSegment,
    SileroDetector,
    WebRTCDetector,
    auto_label,
    available_detectors,
    classify_segments,
    compute_energy_envelope,
    get_detector,
    refine_boundary,
)

# ---------------------------------------------------------------------------
# Audio fixtures
# ---------------------------------------------------------------------------


def _two_burst_audio(
    sr: int = 16000,
    duration_sec: float = 2.0,
    burst_a: tuple[float, float] = (0.3, 0.8),
    burst_b: tuple[float, float] = (1.2, 1.7),
    burst_amp: float = 0.3,
    floor_amp: float = 0.001,
    seed: int = 42,
) -> np.ndarray:
    """Build a mono float32 signal with two loud bursts on a quiet floor."""
    rng = np.random.default_rng(seed)
    n = int(sr * duration_sec)
    audio = floor_amp * rng.standard_normal(n).astype(np.float32)
    for start, end in (burst_a, burst_b):
        s, e = int(start * sr), int(end * sr)
        t = np.arange(e - s) / sr
        tone = (
            burst_amp * np.sin(2 * np.pi * 440 * t)
            + 0.05 * rng.standard_normal(e - s)
        ).astype(np.float32)
        audio[s:e] = tone
    return audio


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_available_detectors_includes_silero() -> None:
    # Silero is a core dependency, so it is always registered.
    assert "silero" in available_detectors()


def test_get_detector_unknown_raises_keyerror() -> None:
    with pytest.raises(KeyError):
        get_detector("nope")


def test_get_detector_silero_runtime_when_unavailable() -> None:
    if SileroDetector.is_available():
        det = get_detector("silero")
        assert isinstance(det, SileroDetector)
    else:
        with pytest.raises(RuntimeError, match="silero"):
            get_detector("silero")


def test_get_detector_webrtc_runtime_when_unavailable() -> None:
    if WebRTCDetector.is_available():
        det = get_detector("webrtc")
        assert isinstance(det, WebRTCDetector)
    else:
        with pytest.raises(RuntimeError, match="webrtc"):
            get_detector("webrtc")


# ---------------------------------------------------------------------------
# WebRTC detector (gated)
# ---------------------------------------------------------------------------


@pytest.mark.needs_webrtc
@pytest.mark.skipif(
    not WebRTCDetector.is_available(),
    reason="webrtcvad not installed (pip install 'clicketysplit[webrtc]')",
)
def test_webrtc_detect_runs_on_two_bursts() -> None:
    sr = 16000
    audio = _two_burst_audio(sr=sr)
    det = WebRTCDetector()
    result = det.detect(
        audio, sr, min_segment_ms=150, min_silence_ms=150, silence_margin_ms=25
    )
    assert isinstance(result, DetectionResult)
    # WebRTC may or may not flag the 440 Hz tone as speech depending on
    # aggressiveness; we only require the call to run cleanly and produce
    # the analysis envelope at original-rate resolution.
    assert "energy" in result.analysis
    assert "times" in result.analysis
    assert result.analysis["energy"].ndim == 1
    # The energy envelope must be at the ORIGINAL rate, so its length
    # should match what compute_energy_envelope produces directly.
    times_ref, _ = compute_energy_envelope(audio, sr)
    assert len(result.analysis["times"]) == len(times_ref)


@pytest.mark.needs_webrtc
@pytest.mark.skipif(
    not WebRTCDetector.is_available(),
    reason="webrtcvad not installed (pip install 'clicketysplit[webrtc]')",
)
def test_webrtc_rejects_bad_frame_ms() -> None:
    sr = 16000
    audio = _two_burst_audio(sr=sr)
    det = WebRTCDetector()
    with pytest.raises(ValueError, match="frame_ms"):
        det.detect(
            audio,
            sr,
            min_segment_ms=150,
            min_silence_ms=150,
            silence_margin_ms=25,
            frame_ms=25,
        )


# ---------------------------------------------------------------------------
# Silero detector (gated)
# ---------------------------------------------------------------------------


@pytest.mark.needs_silero
@pytest.mark.skipif(
    not SileroDetector.is_available(),
    reason="silero-vad not installed (pip install 'clicketysplit[silero]')",
)
def test_silero_detect_runs_returns_result() -> None:
    sr = 16000
    audio = _two_burst_audio(sr=sr)
    det = SileroDetector()
    result = det.detect(
        audio, sr, min_segment_ms=150, min_silence_ms=150, silence_margin_ms=25
    )
    assert isinstance(result, DetectionResult)
    # Silero is trained on real speech; synthetic sine bursts may or may not
    # trip it. We exercise the call, the analysis envelope, and confirm
    # boundary refinement is done at the ORIGINAL rate (per 03 doc).
    assert "energy" in result.analysis
    times_ref, _ = compute_energy_envelope(audio, sr)
    assert len(result.analysis["times"]) == len(times_ref)


@pytest.mark.needs_silero
@pytest.mark.skipif(
    not SileroDetector.is_available(),
    reason="silero-vad not installed (pip install 'clicketysplit[silero]')",
)
def test_silero_handles_silent_input() -> None:
    sr = 16000
    audio = np.zeros(sr * 2, dtype=np.float32)
    det = SileroDetector()
    result = det.detect(
        audio, sr, min_segment_ms=150, min_silence_ms=150, silence_margin_ms=25
    )
    assert isinstance(result, DetectionResult)
    assert result.segments == []


# ---------------------------------------------------------------------------
# Refinement
# ---------------------------------------------------------------------------


def test_compute_energy_envelope_shapes_and_smoothing() -> None:
    sr = 16000
    audio = _two_burst_audio(sr=sr)
    times, energy = compute_energy_envelope(audio, sr)
    assert times.ndim == 1
    assert energy.ndim == 1
    assert len(times) == len(energy)
    assert times[0] >= 0.0
    assert times[-1] <= len(audio) / sr


def test_refine_boundary_extends_outward_to_threshold_crossing() -> None:
    sr = 16000
    duration = 1.0
    n = int(sr * duration)
    audio = np.zeros(n, dtype=np.float32)
    # A clean burst between 0.40s and 0.60s.
    s, e = int(0.40 * sr), int(0.60 * sr)
    t = np.arange(e - s) / sr
    audio[s:e] = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    times, energy = compute_energy_envelope(audio, sr)

    # Coarse boundary INSIDE the burst; refinement should walk outward to
    # the silence threshold.
    refined_start = refine_boundary(times, energy, 0.45, direction="start")
    refined_end = refine_boundary(times, energy, 0.55, direction="end")
    assert refined_start <= 0.45
    assert refined_end >= 0.55
    # Should not exit the burst by more than the search window (~40 ms).
    assert refined_start >= 0.40 - 0.05
    assert refined_end <= 0.60 + 0.05


def test_refine_boundary_no_crossing_returns_coarse() -> None:
    # Flat envelope: no threshold crossing → return coarse_t unchanged.
    times = np.linspace(0, 1, 100)
    energy = np.ones(100, dtype=np.float64) * 0.5
    out = refine_boundary(times, energy, 0.5, direction="start")
    assert out == pytest.approx(0.5)


def test_refine_boundary_empty_times_returns_coarse() -> None:
    out = refine_boundary(np.array([]), np.array([]), 1.23, direction="start")
    assert out == 1.23


# ---------------------------------------------------------------------------
# classify_segments
# ---------------------------------------------------------------------------


def _seg(start: float, end: float) -> ProposedSegment:
    return ProposedSegment(start=start, end=end, duration_ms=(end - start) * 1000)


def test_classify_short_noise_word_crosstalk() -> None:
    segs = [
        _seg(0.0, 0.1),   # 100 ms → short_noise
        _seg(0.5, 1.0),   # 500 ms → word
        _seg(2.0, 4.0),   # 2000 ms → crosstalk
    ]
    out = classify_segments(
        segs,
        min_word_duration_ms=250,
        max_word_duration_ms=1400,
        drop_intro_block=False,
    )
    assert [s.segment_type for s in out] == ["short_noise", "word", "crosstalk"]
    assert all(s.assigned_name == "" for s in out)
    assert all(s.label_source == "" for s in out)
    assert all(s.status == "pending" for s in out)


def test_classify_drop_intro_block() -> None:
    # Intro: a single utterance, then a big gap (~1.5s), then a dense run.
    segs = [
        _seg(0.0, 0.5),   # intro candidate
        _seg(2.0, 2.5),   # first real word (big gap before)
        _seg(2.7, 3.2),
        _seg(3.4, 3.9),
        _seg(4.1, 4.6),
        _seg(4.8, 5.3),
        _seg(5.5, 6.0),
        _seg(6.2, 6.7),
        _seg(6.9, 7.4),
        _seg(7.6, 8.1),
        _seg(8.3, 8.8),
    ]
    out = classify_segments(segs, drop_intro_block=True)
    assert out[0].segment_type == "intro"
    assert out[0].status == "intro"
    # Subsequent segments should not all be intro.
    assert any(s.segment_type == "word" for s in out[1:])


def test_classify_intro_off_by_default() -> None:
    segs = [
        _seg(0.0, 0.5),
        _seg(2.0, 2.5),
    ]
    out = classify_segments(segs)
    assert all(s.segment_type != "intro" for s in out)


# ---------------------------------------------------------------------------
# auto_label test-vector parametrization
# ---------------------------------------------------------------------------


_VECTORS_PATH = Path(__file__).parent / "labeling_test_vectors.json"


def _load_vectors() -> list[dict[str, Any]]:
    data = json.loads(_VECTORS_PATH.read_text())
    return data["vectors"]


def _word_segments(n: int) -> list[LabeledSegment]:
    """``n`` word-typed segments at 1 s intervals, 500 ms long each."""
    return [
        LabeledSegment(
            start=float(i),
            end=float(i) + 0.5,
            duration_ms=500.0,
            segment_type="word",
        )
        for i in range(n)
    ]


def _vector_segments(vec: dict[str, Any]) -> list[LabeledSegment]:
    """Build the vector's word segments.

    Blocked vectors carry explicit real timings under ``segments`` (gap
    clustering needs them); cycled/random vectors just give a count and get
    a uniform synthetic stride.
    """
    if "segments" in vec:
        return [
            LabeledSegment(
                start=s["start"],
                end=s["end"],
                duration_ms=round((s["end"] - s["start"]) * 1000, 1),
                segment_type="word",
            )
            for s in vec["segments"]
        ]
    return _word_segments(vec["word_segment_count"])


@pytest.mark.parametrize("vec", _load_vectors(), ids=lambda v: v["name"])
def test_auto_label_vectors(vec: dict[str, Any]) -> None:
    segs = _vector_segments(vec)
    anchors = [
        LabelAnchor(
            word_index=a["word_index"], label=a["label"], source=a["source"]
        )
        for a in vec["anchors"]
    ]
    out = auto_label(
        segs,
        vec["stimulus_list"],
        presentation_order=vec["presentation_order"],
        expected_reps_per_stimulus=vec["expected_reps_per_stimulus"],
        anchors=anchors,
    )
    actual = [s.assigned_name for s in out if s.segment_type == "word"]
    assert actual == vec["expected_labels"], (
        f"Vector {vec['name']!r}: expected {vec['expected_labels']}, got {actual}"
    )


def test_auto_label_random_does_not_fill_unanchored() -> None:
    segs = _word_segments(5)
    out = auto_label(
        segs,
        ["apple", "banana", "cherry"],
        presentation_order="random",
        anchors=[],
    )
    assert [s.assigned_name for s in out] == ["", "", "", "", ""]
    assert [s.label_source for s in out] == ["", "", "", "", ""]


def test_auto_label_random_only_anchored_indices_get_label() -> None:
    segs = _word_segments(5)
    out = auto_label(
        segs,
        ["apple", "banana", "cherry"],
        presentation_order="random",
        anchors=[LabelAnchor(2, "cherry", "user")],
    )
    assert [s.assigned_name for s in out] == ["", "", "cherry", "", ""]
    assert out[2].label_source == "anchor"
    assert out[0].label_source == ""


def _blocked_word_segments(cluster_sizes: list[int]) -> list[LabeledSegment]:
    """Word segments with blocked gap structure: 0.6 s pauses within a
    cluster, 2.5 s pauses between clusters — the pacing gap clustering
    was built for."""
    out: list[LabeledSegment] = []
    t = 0.0
    for size in cluster_sizes:
        for i in range(size):
            out.append(
                LabeledSegment(
                    start=t, end=t + 0.5, duration_ms=500.0, segment_type="word"
                )
            )
            t += 0.5 + (0.6 if i < size - 1 else 2.5)
    return out


def test_auto_label_empty_anchor_halts_forward_labeling() -> None:
    segs = _blocked_word_segments([3, 3, 3])
    out = auto_label(
        segs,
        ["apple", "banana", "cherry"],
        presentation_order="blocked",
        expected_reps_per_stimulus=3,
        anchors=[
            LabelAnchor(0, "apple", "initial"),
            LabelAnchor(4, "", "user"),
        ],
    )
    labels = [s.assigned_name for s in out]
    # The empty anchor itself is "" and everything after it stays "".
    assert labels[4] == ""
    assert labels[5] == ""
    assert labels[6] == ""
    assert labels[7] == ""
    assert labels[8] == ""
    # Before the empty anchor, normal forward walk fills.
    assert labels[0] == "apple"
    assert labels[1] == "apple"


def test_auto_label_label_source_distinguishes_anchor_and_auto() -> None:
    segs = _word_segments(4)
    out = auto_label(
        segs,
        ["apple", "banana", "cherry"],
        presentation_order="cycled",
        anchors=[LabelAnchor(0, "apple", "initial")],
    )
    assert out[0].label_source == "anchor"
    assert out[1].label_source == "auto"
    assert out[2].label_source == "auto"
    assert out[3].label_source == "auto"


def test_auto_label_non_word_segments_untouched() -> None:
    segs = _word_segments(3)
    segs[1].segment_type = "short_noise"
    out = auto_label(
        segs,
        ["apple", "banana", "cherry"],
        presentation_order="cycled",
        anchors=[LabelAnchor(0, "apple", "initial")],
    )
    # word indices are now [0, 2] (the short_noise at index 1 doesn't count).
    assert out[0].assigned_name == "apple"
    assert out[1].assigned_name == ""           # not a word, untouched
    assert out[1].segment_type == "short_noise"
    assert out[2].assigned_name == "banana"     # word_index 1, stride 1
