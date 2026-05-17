"""Energy-envelope computation and boundary refinement.

Shared by all VAD backends — coarse start/end times from any detector are
walked outward against the energy envelope to give sub-frame precision
(per 03 doc "Boundary refinement"). Ported from the legacy pipeline's
``compute_energy_envelope`` and ``_refine_boundary``, with module-global
``ENERGY_SMOOTHING_MS`` replaced by an explicit function argument.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import numpy as np
from scipy.signal import medfilt

if TYPE_CHECKING:
    from numpy.typing import NDArray


DEFAULT_ENERGY_SMOOTHING_MS: int = 15


def compute_energy_envelope(
    audio: NDArray[np.float32],
    sr: int,
    frame_ms: int = 5,
    energy_smoothing_ms: int = DEFAULT_ENERGY_SMOOTHING_MS,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Compute a median-smoothed RMS envelope.

    Returns ``(times, energy)`` arrays on a 50%-overlap frame grid.
    ``frame_ms`` defaults to 5 ms (200 Hz frame rate) which matches the
    legacy pipeline. A degenerate single-frame result is returned when
    the input is shorter than one analysis frame.
    """
    frame_len = int(frame_ms * sr / 1000)
    hop = max(1, frame_len // 2)
    n_frames = (len(audio) - frame_len) // hop + 1
    if n_frames <= 0 or frame_len <= 0:
        return np.array([0.0]), np.array([0.0])

    energy = np.zeros(n_frames, dtype=np.float64)
    times = np.zeros(n_frames, dtype=np.float64)
    for i in range(n_frames):
        start = i * hop
        end = start + frame_len
        if end > len(audio):
            break
        frame = audio[start:end].astype(np.float64)
        energy[i] = np.sqrt(np.mean(frame * frame))
        times[i] = (start + frame_len // 2) / sr

    # medfilt requires an odd kernel size; OR-with-1 forces oddness.
    kernel = max(3, int(energy_smoothing_ms / frame_ms)) | 1
    if len(energy) >= kernel:
        energy = medfilt(energy, kernel_size=kernel)
    return times, energy


def refine_boundary(
    times: NDArray[np.float64],
    energy: NDArray[np.float64],
    coarse_t: float,
    direction: Literal["start", "end"] = "start",
    window_ms: int = 40,
) -> float:
    """Refine a coarse VAD boundary against the energy envelope.

    For ``direction="start"`` walks backward from ``coarse_t`` to where energy
    drops below 15% of the local max (the true onset). For ``"end"``, walks
    forward similarly. If no clear crossing is found within the search
    window, the original ``coarse_t`` is returned — we never expand the
    boundary into silence speculatively.
    """
    if len(times) == 0:
        return coarse_t

    dt = float(times[1] - times[0]) if len(times) > 1 else 0.005
    window_frames = max(1, int(window_ms / 1000 / dt))

    idx = int(np.searchsorted(times, coarse_t))
    idx = min(max(idx, 0), len(times) - 1)

    if direction == "start":
        search_start = max(0, idx - window_frames)
        search_end = min(len(energy), idx + window_frames // 2)
        region = energy[search_start:search_end]
        if len(region) == 0:
            return coarse_t
        local_max = float(np.max(region))
        threshold = local_max * 0.15
        for j in range(idx, search_start - 1, -1):
            if energy[j] < threshold:
                return float(times[min(j + 1, len(times) - 1)])
        return coarse_t

    search_start = max(0, idx - window_frames // 2)
    search_end = min(len(energy), idx + window_frames)
    region = energy[search_start:search_end]
    if len(region) == 0:
        return coarse_t
    local_max = float(np.max(region))
    threshold = local_max * 0.15
    for j in range(idx, search_end):
        if j < len(energy) and energy[j] < threshold:
            return float(times[max(j - 1, 0)])
    return coarse_t


def energy_threshold_for_plot(energy: NDArray[np.float64]) -> float:
    """Compute the dB-domain threshold used to shade the overview plot.

    Matches the legacy formula:
    ``e_silence + 0.45 * (e_speech - e_silence)`` on the 20th/55th
    percentiles of nonzero-energy frames in dB, then converted back to
    linear. Returns 0.0 for all-zero or empty inputs.
    """
    energy_nonzero = energy[energy > 0]
    if len(energy_nonzero) == 0:
        return 0.0
    energy_db = 20 * np.log10(energy_nonzero + 1e-12)
    e_silence = float(np.percentile(energy_db, 20))
    e_speech = float(np.percentile(energy_db, 55))
    e_threshold = e_silence + 0.45 * (e_speech - e_silence)
    return float(10 ** (e_threshold / 20))
