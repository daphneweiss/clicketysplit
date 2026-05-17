"""WebRTC VAD detector. Optional via ``clicketysplit[webrtc]``.

Direct port of ``detect_segments_vad`` from the legacy pipeline. The optional
``webrtcvad`` import is guarded so this module is importable even without
the extra; calling code is expected to check :meth:`is_available` first
(or go through :func:`get_detector`, which does that).
"""

from __future__ import annotations

from math import gcd
from typing import TYPE_CHECKING, Any

import numpy as np
from scipy.signal import resample_poly

from .base import DetectionResult, ProposedSegment
from .refinement import compute_energy_envelope, energy_threshold_for_plot, refine_boundary

if TYPE_CHECKING:
    from numpy.typing import NDArray

try:
    import webrtcvad

    _HAS_WEBRTCVAD = True
except ImportError:
    webrtcvad = None  # type: ignore[assignment]
    _HAS_WEBRTCVAD = False


class WebRTCDetector:
    """Frame-based VAD with energy-envelope boundary refinement."""

    name: str = "webrtc"
    requires_extras: list[str] = ["webrtc"]

    @classmethod
    def is_available(cls) -> bool:
        return _HAS_WEBRTCVAD

    def detect(
        self,
        audio: NDArray[np.float32],
        sr: int,
        *,
        min_segment_ms: int = 150,
        min_silence_ms: int = 150,
        silence_margin_ms: int = 25,
        aggressiveness: int = 3,
        frame_ms: int = 30,
        energy_smoothing_ms: int = 15,
        **_backend_specific: Any,
    ) -> DetectionResult:
        if not _HAS_WEBRTCVAD:
            raise RuntimeError(
                "WebRTCDetector requires webrtcvad: pip install 'clicketysplit[webrtc]'"
            )
        if frame_ms not in (10, 20, 30):
            raise ValueError(f"WebRTC VAD requires frame_ms in (10, 20, 30); got {frame_ms}")

        duration = len(audio) / sr

        target_sr = 16000
        if sr != target_sr:
            g = gcd(int(sr), target_sr)
            audio_16k = resample_poly(audio, target_sr // g, int(sr) // g)
        else:
            audio_16k = audio.copy()

        audio_16k = np.clip(audio_16k, -1.0, 1.0)
        pcm = (audio_16k * 32767).astype(np.int16).tobytes()

        vad = webrtcvad.Vad(aggressiveness)
        samples_per_frame = int(target_sr * frame_ms / 1000)
        bytes_per_frame = samples_per_frame * 2

        vad_flags: list[bool] = []
        for i in range(0, len(pcm) - bytes_per_frame + 1, bytes_per_frame):
            frame = pcm[i : i + bytes_per_frame]
            if len(frame) < bytes_per_frame:
                break
            vad_flags.append(vad.is_speech(frame, target_sr))

        n_vad = len(vad_flags)
        # Refine boundaries against the ORIGINAL-rate energy envelope (per 03
        # doc: don't discard source resolution when refining).
        times_e, energy_e = compute_energy_envelope(
            audio, sr, energy_smoothing_ms=energy_smoothing_ms
        )

        if n_vad == 0:
            return DetectionResult(
                segments=[],
                analysis={
                    "times": times_e,
                    "energy": energy_e,
                    "is_speech": np.zeros(len(times_e), dtype=bool),
                    "energy_threshold": energy_threshold_for_plot(energy_e),
                },
            )

        is_speech_vad = np.array(vad_flags, dtype=bool)
        vad_times = np.arange(n_vad) * (frame_ms / 1000) + (frame_ms / 2000)

        min_silence_frames = max(1, int(min_silence_ms / frame_ms))
        min_segment_frames = max(1, int(min_segment_ms / frame_ms))

        smoothed = is_speech_vad.copy()

        gap_start: int | None = None
        for i in range(len(smoothed)):
            if not smoothed[i]:
                if gap_start is None:
                    gap_start = i
            else:
                if gap_start is not None:
                    if i - gap_start < min_silence_frames:
                        smoothed[gap_start:i] = True
                    gap_start = None

        speech_start: int | None = None
        for i in range(len(smoothed)):
            if smoothed[i]:
                if speech_start is None:
                    speech_start = i
            else:
                if speech_start is not None:
                    if i - speech_start < min_segment_frames:
                        smoothed[speech_start:i] = False
                    speech_start = None

        segments: list[ProposedSegment] = []
        seg_start: int | None = None
        for i in range(len(smoothed)):
            if smoothed[i] and seg_start is None:
                seg_start = i
            elif not smoothed[i] and seg_start is not None:
                coarse_start = float(vad_times[seg_start]) - frame_ms / 2000
                coarse_end = float(vad_times[min(i - 1, n_vad - 1)]) + frame_ms / 2000
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
                seg_start = None

        if seg_start is not None:
            coarse_start = float(vad_times[seg_start]) - frame_ms / 2000
            coarse_end = float(vad_times[-1]) + frame_ms / 2000
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
        for k, t in enumerate(times_e):
            vad_idx = int(t / (frame_ms / 1000))
            if 0 <= vad_idx < n_vad:
                is_speech_display[k] = smoothed[vad_idx]

        return DetectionResult(
            segments=segments,
            analysis={
                "times": times_e,
                "energy": energy_e,
                "is_speech": is_speech_display,
                "energy_threshold": energy_threshold_for_plot(energy_e),
            },
        )
