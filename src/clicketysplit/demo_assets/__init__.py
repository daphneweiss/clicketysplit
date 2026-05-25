"""Bundled demo recording and stimulus list.

The wheel ships a tiny (~30 s, ~10 word tokens, 16 kHz mono PCM_16)
recording plus the stimulus list it covers. :func:`setup_demo_experiment`
copies these into a fresh temp directory, writes a minimal
``clicketysplit.json`` next to them, and returns the experiment root so
callers (notably ``clicketysplit demo`` in :mod:`clicketysplit.cli`) can
boot a server pointed at a working experiment without any user setup.

The temp layout matches the canonical experiment layout from
CONTRACT_NOTES C2::

    <tmp>/
    ├── clicketysplit.json
    ├── recordings/demo_speaker/demo_condition/rec.wav
    └── stimulus_lists/demo.txt

The demo uses ``presentation_order="random"`` on purpose
(_design/07_TESTING_AND_DOCS.md §"Demo data"): the first thing a new user
sees should not imply the tool requires blocked repetitions.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from ..config import (
    Condition,
    DetectionConfig,
    ExperimentConfig,
    ExportConfig,
    LabelingConfig,
    Speaker,
    save_config,
)

__all__ = ["setup_demo_experiment"]


def _build_demo_config() -> ExperimentConfig:
    """Construct the in-memory ExperimentConfig for the demo experiment.

    Defaults chosen for a zero-friction first run:

    * ``detection.backend = "energy"`` — works without the silero/webrtc extras.
    * ``detection.denoise = False`` — works without the noisereduce extra.
    * ``labeling.min_word_duration_ms = 150`` — short TTS bursts (~150-600 ms)
      still classify as ``"word"`` instead of being demoted to ``short_noise``.
    * ``conditions[0].presentation_order = "random"`` — see module docstring.
    """
    return ExperimentConfig(
        schema_version=1,
        name="clicketysplit demo",
        recordings_root="recordings",
        stimulus_lists_root="stimulus_lists",
        output_root="output",
        speakers=[Speaker(id="demo_speaker")],
        conditions=[
            Condition(
                name="demo_condition",
                stimulus_list="stimulus_lists/demo.txt",
                presentation_order="random",
            )
        ],
        detection=DetectionConfig(backend="energy", denoise=False),
        labeling=LabelingConfig(min_word_duration_ms=150),
        export=ExportConfig(),
    )


def setup_demo_experiment() -> Path:
    """Copy the bundled demo assets to a temp dir and return its root.

    The returned directory contains:

    * ``clicketysplit.json`` — the config produced by :func:`_build_demo_config`.
    * ``recordings/demo_speaker/demo_condition/rec.wav`` — a copy of the
      bundled ``demo_recording.wav``.
    * ``stimulus_lists/demo.txt`` — a copy of the bundled ``demo_stimuli.txt``.

    The caller owns the returned directory; nothing here cleans it up. The
    OS will reap it from the temp area on reboot, which is fine for the
    demo command's "throwaway sandbox" semantics.
    """
    pkg_dir = Path(__file__).parent
    src_wav = pkg_dir / "demo_recording.wav"
    src_stimuli = pkg_dir / "demo_stimuli.txt"
    if not src_wav.is_file():
        raise FileNotFoundError(
            f"Bundled demo recording missing at {src_wav}. "
            "The wheel may have been built without demo_assets/."
        )
    if not src_stimuli.is_file():
        raise FileNotFoundError(
            f"Bundled demo stimulus list missing at {src_stimuli}. "
            "The wheel may have been built without demo_assets/."
        )

    tmp = Path(tempfile.mkdtemp(prefix="clicketysplit_demo_"))

    rec_dir = tmp / "recordings" / "demo_speaker" / "demo_condition"
    rec_dir.mkdir(parents=True)
    shutil.copy(src_wav, rec_dir / "rec.wav")

    stim_dir = tmp / "stimulus_lists"
    stim_dir.mkdir()
    shutil.copy(src_stimuli, stim_dir / "demo.txt")

    cfg = _build_demo_config()
    save_config(cfg, tmp / "clicketysplit.json")

    return tmp
