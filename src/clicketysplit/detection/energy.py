"""Energy-threshold detector — the always-available fallback.

Direct port of ``detect_segments_raw`` from the legacy pipeline. RMS-based
silence detection, median filter, fill short gaps, drop short bursts. Has
no optional dependencies, so :meth:`EnergyDetector.is_available` is always
true and this detector is the registry's default fallback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from .base import DetectionResult, ProposedSegment
from .refinement import compute_energy_envelope, energy_threshold_for_plot

if TYPE_CHECKING:
    from numpy.typing import NDArray


class EnergyDetector:
    """RMS-thresholded VAD. No external deps."""

    name: str = "energy"
    requires_extras: list[str] = []

    @classmethod
    def is_available(cls) -> bool:
        return True

    def detect(
        self,
        audio: NDArray[np.float32],
        sr: int,
        *,
        min_segment_ms: int = 150,
        min_silence_ms: int = 150,
        silence_margin_ms: int = 25,
        frame_ms: int = 5,
        energy_smoothing_ms: int = 15,
        **_backend_specific: Any,
    ) -> DetectionResult:
        duration = len(audio) / sr
        times, energy = compute_energy_envelope(
            audio, sr, frame_ms=frame_ms, energy_smoothing_ms=energy_smoothing_ms
        )

        energy_nonzero = energy[energy > 0]
        if len(energy_nonzero) == 0:
            return DetectionResult(
                segments=[],
                analysis={
                    "times": times,
                    "energy": energy,
                    "is_speech": np.zeros_like(energy, dtype=bool),
                    "energy_threshold": 0.0,
                },
            )

        energy_db = 20 * np.log10(energy_nonzero + 1e-12)
        e_silence = float(np.percentile(energy_db, 20))
        e_speech = float(np.percentile(energy_db, 55))
        e_threshold_db = e_silence + 0.45 * (e_speech - e_silence)
        e_threshold_lin = float(10 ** (e_threshold_db / 20))
        is_speech = energy > e_threshold_lin

        min_silence_frames = max(1, int(min_silence_ms / frame_ms))
        min_segment_frames = max(1, int(min_segment_ms / frame_ms))

        cleaned = is_speech.copy()

        gap_start: int | None = None
        for i in range(len(cleaned)):
            if not cleaned[i]:
                if gap_start is None:
                    gap_start = i
            else:
                if gap_start is not None:
                    if i - gap_start < min_silence_frames:
                        cleaned[gap_start:i] = True
                    gap_start = None

        speech_start: int | None = None
        for i in range(len(cleaned)):
            if cleaned[i]:
                if speech_start is None:
                    speech_start = i
            else:
                if speech_start is not None:
                    if i - speech_start < min_segment_frames:
                        cleaned[speech_start:i] = False
                    speech_start = None

        segments: list[ProposedSegment] = []
        seg_start: int | None = None
        for i in range(len(cleaned)):
            if cleaned[i] and seg_start is None:
                seg_start = i
            elif not cleaned[i] and seg_start is not None:
                start_sec = max(0.0, times[seg_start] - silence_margin_ms / 1000)
                end_sec = min(
                    duration, times[min(i, len(times) - 1)] + silence_margin_ms / 1000
                )
                dur_ms = (end_sec - start_sec) * 1000
                if dur_ms >= min_segment_ms:
                    segments.append(
                        ProposedSegment(
                            start=round(start_sec, 4),
                            end=round(end_sec, 4),
                            duration_ms=round(dur_ms, 1),
                        )
                    )
                seg_start = None

        if seg_start is not None:
            end_sec = min(duration, float(times[-1]) + silence_margin_ms / 1000)
            start_sec = max(0.0, float(times[seg_start]) - silence_margin_ms / 1000)
            dur_ms = (end_sec - start_sec) * 1000
            if dur_ms >= min_segment_ms:
                segments.append(
                    ProposedSegment(
                        start=round(start_sec, 4),
                        end=round(end_sec, 4),
                        duration_ms=round(dur_ms, 1),
                    )
                )

        # Recompute the threshold from the actual envelope so the plot
        # uses the same convention regardless of which branch we hit.
        threshold_for_plot = energy_threshold_for_plot(energy)
        return DetectionResult(
            segments=segments,
            analysis={
                "times": times,
                "energy": energy,
                "is_speech": cleaned,
                "energy_threshold": threshold_for_plot
                if threshold_for_plot > 0
                else e_threshold_lin,
            },
        )
