"""Silero VAD detector. Optional via ``clicketysplit[silero]``.

Direct port of ``detect_segments_silero`` from the legacy pipeline. The
neural VAD runs at 16 kHz internally but boundaries are refined against
the ORIGINAL-rate energy envelope (per 03 doc: don't discard source
resolution when refining). The ONNX model is held in a module-level
lazy singleton — first detect call pays the load cost, subsequent calls
reuse it (matches the legacy ``_silero_model`` global).
"""

from __future__ import annotations

from math import gcd
from typing import TYPE_CHECKING, Any, ClassVar

import numpy as np
from scipy.signal import resample_poly

from .base import DetectionResult, ProposedSegment
from .refinement import compute_energy_envelope, energy_threshold_for_plot, refine_boundary

if TYPE_CHECKING:
    from numpy.typing import NDArray

try:
    from silero_vad import get_speech_timestamps, load_silero_vad

    _HAS_SILERO = True
except ImportError:
    load_silero_vad = None  # type: ignore[assignment]
    get_speech_timestamps = None  # type: ignore[assignment]
    _HAS_SILERO = False


_silero_model: Any = None


def _get_silero_model() -> Any:
    global _silero_model
    if _silero_model is None:
        if load_silero_vad is None:
            raise RuntimeError(
                "silero-vad is not installed: pip install 'clicketysplit[silero]'"
            )
        _silero_model = load_silero_vad(onnx=True)
    return _silero_model


class SileroDetector:
    """ONNX Silero VAD. 16 kHz inference, original-rate boundary refinement."""

    name: str = "silero"
    requires_extras: ClassVar[list[str]] = ["silero"]

    @classmethod
    def is_available(cls) -> bool:
        return _HAS_SILERO

    def detect(
        self,
        audio: NDArray[np.float32],
        sr: int,
        *,
        min_segment_ms: int = 150,
        min_silence_ms: int = 150,
        silence_margin_ms: int = 25,
        vad_threshold: float = 0.5,
        energy_smoothing_ms: int = 15,
        **_backend_specific: Any,
    ) -> DetectionResult:
        if not _HAS_SILERO:
            raise RuntimeError(
                "SileroDetector requires silero-vad: pip install 'clicketysplit[silero]'"
            )

        duration = len(audio) / sr
        model = _get_silero_model()

        target_sr = 16000
        if sr != target_sr:
            g = gcd(int(sr), target_sr)
            audio_16k = resample_poly(audio, target_sr // g, int(sr) // g).astype(np.float32)
        else:
            audio_16k = audio.astype(np.float32)

        speech_timestamps = get_speech_timestamps(
            audio_16k,
            model,
            sampling_rate=target_sr,
            threshold=vad_threshold,
            min_speech_duration_ms=min_segment_ms,
            min_silence_duration_ms=min_silence_ms,
            speech_pad_ms=0,
            return_seconds=False,
        )

        # Refinement and the plot energy axis run at the ORIGINAL rate so
        # the source-audio resolution is preserved.
        times_e, energy_e = compute_energy_envelope(
            audio, sr, energy_smoothing_ms=energy_smoothing_ms
        )

        if not speech_timestamps:
            return DetectionResult(
                segments=[],
                analysis={
                    "times": times_e,
                    "energy": energy_e,
                    "is_speech": np.zeros(len(times_e), dtype=bool),
                    "energy_threshold": energy_threshold_for_plot(energy_e),
                },
            )

        segments: list[ProposedSegment] = []
        for ts in speech_timestamps:
            coarse_start = ts["start"] / target_sr
            coarse_end = ts["end"] / target_sr

            start_sec = refine_boundary(times_e, energy_e, coarse_start, direction="start")
            end_sec = refine_boundary(times_e, energy_e, coarse_end, direction="end")

            start_sec = max(0.0, start_sec - silence_margin_ms / 1000)
            end_sec = min(duration, end_sec + silence_margin_ms / 1000)

            dur_ms = (end_sec - start_sec) * 1000
            if dur_ms >= min_segment_ms:
                segments.append(
                    ProposedSegment(
                        start=round(start_sec, 4),
                        end=round(end_sec, 4),
                        duration_ms=round(dur_ms, 1),
                    )
                )

        is_speech_display = np.zeros(len(times_e), dtype=bool)
        for ts in speech_timestamps:
            seg_start_sec = ts["start"] / target_sr
            seg_end_sec = ts["end"] / target_sr
            mask = (times_e >= seg_start_sec) & (times_e <= seg_end_sec)
            is_speech_display[mask] = True

        return DetectionResult(
            segments=segments,
            analysis={
                "times": times_e,
                "energy": energy_e,
                "is_speech": is_speech_display,
                "energy_threshold": energy_threshold_for_plot(energy_e),
            },
        )
