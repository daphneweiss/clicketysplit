"""Detection pipeline orchestrator.

Glues together audio loading, optional denoising, the configured VAD
backend, segment typing, and forward-walk auto-labeling into a single
function that produces ``proposed_segments.json`` plus a sanity-check
``overview.png``. The Flask ``/api/detect`` route is a thin wrapper around
:func:`detect_for_condition`; the route MUST NOT mutate module globals
(see _design/00_OVERVIEW.md "What's wrong with the current code" #1).

Lives in ``detection/pipeline.py`` per 05_EXPORT.md §"Overview plot" — the
overview is a *side product of detection*, not an export artifact, so it
ships with the pipeline rather than the export module.
"""

from __future__ import annotations

import json
import os
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Matplotlib backend must be selected BEFORE pyplot is imported, so headless
# CI (no DISPLAY, no Tk) works. Doing this at module level matches the
# legacy pipeline and is the conventional pattern.
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from ..audio_io import SUPPORTED_EXTENSIONS, concatenate, save_audio  # noqa: E402
from ..denoise import HAS_NOISEREDUCE, denoise_audio  # noqa: E402
from . import get_detector  # noqa: E402
from .base import LabelAnchor, LabeledSegment  # noqa: E402
from .labeling import auto_label, classify_segments  # noqa: E402

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from ..config import Condition, ExperimentConfig, Speaker


PROPOSED_SEGMENTS_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProposalResult:
    """What :func:`detect_for_condition` returns to its caller.

    Paths are absolute (the caller turns them into URLs / relative paths
    as needed). All relativization for storage inside the JSON happens
    inside :func:`detect_for_condition` itself.
    """

    speaker_id: str
    condition: str
    proposed_segments_path: Path
    denoised_audio_path: Path | None
    overview_plot_path: Path
    audio_duration_sec: float
    sample_rate: int
    segment_count: int
    word_segment_count: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_speaker(config: ExperimentConfig, speaker_id: str) -> Speaker:
    for s in config.speakers:
        if s.id == speaker_id:
            return s
    known = sorted(s.id for s in config.speakers)
    raise ValueError(
        f"Unknown speaker_id {speaker_id!r}. Known speakers: {known}"
    )


def _find_condition(config: ExperimentConfig, condition_name: str) -> Condition:
    for c in config.conditions:
        if c.name == condition_name:
            return c
    known = sorted(c.name for c in config.conditions)
    raise ValueError(
        f"Unknown condition {condition_name!r}. Known conditions: {known}"
    )


def _audio_files_in(directory: Path) -> list[Path]:
    """Return supported-extension audio files at the top of ``directory``, sorted."""
    if not directory.is_dir():
        return []
    files: list[Path] = []
    seen: set[Path] = set()
    for entry in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_file():
            continue
        if entry.suffix.lower() in SUPPORTED_EXTENSIONS:
            resolved = entry.resolve()
            if resolved not in seen:
                seen.add(resolved)
                files.append(entry)
    return files


def _resolve_audio_paths(
    config: ExperimentConfig, speaker: Speaker, condition: Condition
) -> list[Path]:
    """Find audio files for this speaker×condition, honoring flat layout.

    Per 02_CONFIG_AND_DISCOVERY.md (the "flat layout" rule referenced by
    the task brief): if the speaker subdir has no condition subdirectory
    but DOES contain audio files at its top level AND only one condition
    is configured, those files belong to that single condition. Discovery
    proper happens in task 8; the pipeline only honors the rule when the
    user has configured a flat speaker.
    """
    recordings_root = config.resolve(config.recordings_root)
    # speaker.subdir is populated by Speaker's post-init validator from speaker.id
    # if not explicitly set, so it is always non-None here. The cast keeps mypy
    # happy without a runtime branch.
    subdir = speaker.subdir if speaker.subdir is not None else speaker.id
    speaker_dir = recordings_root / subdir
    condition_dir = speaker_dir / condition.name

    files = _audio_files_in(condition_dir)
    if files:
        return files

    # Flat layout: only valid when exactly one condition is configured.
    if len(config.conditions) == 1:
        flat_files = _audio_files_in(speaker_dir)
        if flat_files:
            return flat_files

    raise ValueError(
        f"No audio files found for speaker {speaker.id!r}, "
        f"condition {condition.name!r} at {condition_dir} "
        f"(supported extensions: {sorted(SUPPORTED_EXTENSIONS)})"
    )


def _read_stimulus_list(path: Path) -> list[str]:
    """One non-blank line per stimulus; ``#``-prefixed lines ignored.

    Comment-skipping matches the legacy ``load_stimulus_list`` behavior so
    user stimulus files round-trip cleanly into this pipeline. Empty files
    are rejected at config-load time, so this never returns ``[]`` for a
    validated config.
    """
    stimuli: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            stimuli.append(line)
    return stimuli


def _relative_to_config(path: Path, config_dir: Path) -> str:
    """Return ``path`` as a forward-slash string relative to ``config_dir``.

    Falls back to ``os.path.relpath`` when the path is outside ``config_dir``
    (rare — only happens if the user pointed ``recordings_root`` at an
    absolute path outside the experiment dir). Forward slashes keep the
    written JSON portable across Windows / *nix machines (per
    00_OVERVIEW.md #6: "no absolute paths leaking through" — moving the
    experiment dir to another machine must just work).
    """
    try:
        rel = path.resolve().relative_to(config_dir)
    except ValueError:
        rel = Path(os.path.relpath(path.resolve(), config_dir))
    return rel.as_posix()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (tmp + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_proposed_segments(payload: dict[str, Any], path: Path) -> None:
    """Serialize ``payload`` to ``path`` as indented JSON, atomically.

    Schema is the responsibility of the caller; this just writes JSON with
    ``indent=2`` via tmp + rename so a crashed process never leaves a
    half-written file behind.
    """
    data = json.dumps(payload, indent=2).encode("utf-8")
    _atomic_write_bytes(path, data)


# ---------------------------------------------------------------------------
# Overview plot
# ---------------------------------------------------------------------------


_SEGMENT_TYPE_COLORS: dict[str, str] = {
    "word": "#69b7a5",       # green
    "short_noise": "#888888",  # gray
    "crosstalk": "#c0a050",   # red-ish brown
    "intro": "#d4c14e",       # yellow
}


def _rms_envelope(audio: NDArray[np.float32], sr: int, frame_ms: float = 10.0) -> tuple[
    NDArray[np.float64], NDArray[np.float64]
]:
    frame_len = max(1, int(frame_ms * sr / 1000.0))
    n_frames = max(1, len(audio) // frame_len)
    times = np.zeros(n_frames, dtype=np.float64)
    energy = np.zeros(n_frames, dtype=np.float64)
    for i in range(n_frames):
        start = i * frame_len
        end = start + frame_len
        chunk = audio[start:end]
        if len(chunk) == 0:
            break
        energy[i] = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
        times[i] = (start + frame_len / 2) / sr
    return times, energy


def plot_overview(
    audio: NDArray[np.float32],
    sr: int,
    segments: list[LabeledSegment],
    out_path: Path,
) -> None:
    """Save a single-panel RMS envelope + colored type bars to ``out_path``.

    Minimal by design (05_EXPORT.md §"Overview plot"): time axis, RMS
    envelope, colored bars tinted by ``segment_type`` (word=green,
    short_noise=gray, crosstalk=red, intro=yellow). No zoomed inset, no
    spectrogram, no waveform-on-top-of-energy. The file is saved
    atomically (matplotlib's ``savefig`` does not atomic-write).
    """
    duration = len(audio) / sr
    times, energy = _rms_envelope(audio, sr, frame_ms=10.0)

    width = max(8.0, min(40.0, duration * 1.2))
    fig, ax = plt.subplots(1, 1, figsize=(width, 3.5))
    try:
        ax.plot(times, energy, color="navy", linewidth=0.6)
        for seg in segments:
            color = _SEGMENT_TYPE_COLORS.get(seg.segment_type, "#888888")
            ax.axvspan(seg.start, seg.end, alpha=0.25, color=color)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("RMS energy")
        ax.set_xlim(0, max(duration, 0.001))
        word_count = sum(1 for s in segments if s.segment_type == "word")
        ax.set_title(
            f"{len(segments)} segments ({word_count} word-typed) · {duration:.1f}s"
        )
        fig.tight_layout()

        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Matplotlib infers format from the file suffix, and ``.tmp`` is not a
        # known format — pass ``format="png"`` explicitly so the tmp path
        # works even with a non-standard suffix.
        tmp = out_path.with_suffix(out_path.suffix + ".tmp")
        fig.savefig(tmp, dpi=120, bbox_inches="tight", format="png")
        os.replace(tmp, out_path)
    finally:
        plt.close(fig)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def detect_for_condition(
    config: ExperimentConfig,
    speaker_id: str,
    condition_name: str,
) -> ProposalResult:
    """Run the full detection pipeline for one speaker×condition.

    Steps (per 03_AUDIO_AND_DETECTION.md §"Detection pipeline orchestration"):

      1. Look up speaker + condition by id/name.
      2. Resolve audio file paths (condition subdir, falling back to flat
         layout when exactly one condition is configured).
      3. Concatenate via :func:`audio_io.concatenate` with a 0.5 s silence
         gap between files.
      4. Optionally denoise (writes ``denoised.wav`` next to the JSON).
      5. Run the configured detector with min_segment / min_silence /
         silence_margin from ``config.detection``.
      6. Type-classify via :func:`classify_segments`.
      7. For ``presentation_order in {cycled, blocked}``, seed an implicit
         ``LabelAnchor(0, stimulus_list[0], "initial")`` so the forward
         walk has something to start from. For ``random`` no implicit
         anchor exists (per 03 doc).
      8. Write ``proposed_segments.json``, ``overview.png``, return paths.
    """
    if config._config_dir is None:
        raise ValueError(
            "ExperimentConfig._config_dir is not set. Load the config via "
            "config.load_config() so paths can be resolved."
        )
    config_dir = config._config_dir

    speaker = _find_speaker(config, speaker_id)
    condition = _find_condition(config, condition_name)

    audio_paths = _resolve_audio_paths(config, speaker, condition)

    concat = concatenate(audio_paths, silence_sec=0.5)
    audio = concat.audio
    sr = concat.sample_rate

    # Output directory: <output_root>/<speaker_id>/<condition_name>/
    out_dir = config.resolve(config.output_root, speaker.id, condition.name)
    out_dir.mkdir(parents=True, exist_ok=True)

    denoised_audio_path: Path | None = None
    if config.detection.denoise:
        if HAS_NOISEREDUCE:
            audio = denoise_audio(audio, sr)
            denoised_audio_path = out_dir / "denoised.wav"
            # Atomic save: save to tmp, then rename.
            tmp_audio = denoised_audio_path.with_suffix(".wav.tmp")
            save_audio(tmp_audio, audio, sr, format="wav")
            os.replace(tmp_audio, denoised_audio_path)
        else:
            # Per task brief: warn rather than fail when the lib isn't there.
            warnings.warn(
                "config.detection.denoise=True but the 'noisereduce' library "
                "is not installed. Continuing without denoising. "
                "Install with: pip install 'clicketysplit[denoise]'",
                RuntimeWarning,
                stacklevel=2,
            )

    detector = get_detector(config.detection.backend)
    detect_kwargs: dict[str, Any] = {}
    # Silero is the only backend with backend-specific params today; pass it
    # cleanly via **kwargs without poisoning the energy/webrtc signature.
    if config.detection.backend == "silero":
        detect_kwargs["vad_threshold"] = config.detection.vad_threshold
    result = detector.detect(
        audio,
        sr,
        min_segment_ms=config.detection.min_segment_ms,
        min_silence_ms=config.detection.min_silence_ms,
        silence_margin_ms=config.detection.silence_margin_ms,
        **detect_kwargs,
    )

    labeled = classify_segments(
        result.segments,
        min_word_duration_ms=config.labeling.min_word_duration_ms,
        max_word_duration_ms=config.labeling.max_word_duration_ms,
        drop_intro_block=config.labeling.drop_intro_block,
    )

    stimulus_list = _read_stimulus_list(config.resolve(condition.stimulus_list))

    # Implicit initial anchor only for cycled/blocked (per 03 doc + task brief).
    anchors: list[LabelAnchor] = []
    if condition.presentation_order in ("cycled", "blocked") and stimulus_list:
        anchors = [
            LabelAnchor(word_index=0, label=stimulus_list[0], source="initial")
        ]

    labeled = auto_label(
        labeled,
        stimulus_list,
        presentation_order=condition.presentation_order,
        expected_reps_per_stimulus=condition.expected_reps_per_stimulus,
        anchors=anchors,
    )

    # Build proposed_segments.json payload (03 doc §schema).
    source_files_payload = [
        {
            "path": _relative_to_config(b.path, config_dir),
            "duration_sec": float(b.duration_sec),
            "offset_sec": float(b.offset_sec),
        }
        for b in concat.boundaries
    ]

    detector_params: dict[str, Any] = {
        "min_segment_ms": config.detection.min_segment_ms,
        "min_silence_ms": config.detection.min_silence_ms,
        "silence_margin_ms": config.detection.silence_margin_ms,
    }
    if config.detection.backend == "silero":
        detector_params["vad_threshold"] = config.detection.vad_threshold

    payload: dict[str, Any] = {
        "schema_version": PROPOSED_SEGMENTS_SCHEMA_VERSION,
        "speaker_id": speaker.id,
        "condition": condition.name,
        "source_files": source_files_payload,
        "denoised_audio": (
            _relative_to_config(denoised_audio_path, config_dir)
            if denoised_audio_path is not None
            else None
        ),
        "audio_duration_sec": float(len(audio) / sr),
        "sample_rate": int(sr),
        "detector": config.detection.backend,
        "detector_params": detector_params,
        "stimulus_list": stimulus_list,
        "presentation_order": condition.presentation_order,
        "expected_reps_per_stimulus": condition.expected_reps_per_stimulus,
        "label_anchors": [
            {"word_index": a.word_index, "label": a.label, "source": a.source}
            for a in anchors
        ],
        "segments": [
            {
                "start": float(s.start),
                "end": float(s.end),
                "duration_ms": float(s.duration_ms),
                "segment_type": s.segment_type,
                "assigned_name": s.assigned_name,
                "label_source": s.label_source,
                "status": s.status,
                "token_index": int(s.token_index),
                "cluster_size": int(s.cluster_size),
            }
            for s in labeled
        ],
    }

    json_path = out_dir / "proposed_segments.json"
    write_proposed_segments(payload, json_path)

    overview_path = out_dir / "overview.png"
    plot_overview(audio, sr, labeled, overview_path)

    return ProposalResult(
        speaker_id=speaker.id,
        condition=condition.name,
        proposed_segments_path=json_path,
        denoised_audio_path=denoised_audio_path,
        overview_plot_path=overview_path,
        audio_duration_sec=float(len(audio) / sr),
        sample_rate=int(sr),
        segment_count=len(labeled),
        word_segment_count=sum(1 for s in labeled if s.segment_type == "word"),
    )
