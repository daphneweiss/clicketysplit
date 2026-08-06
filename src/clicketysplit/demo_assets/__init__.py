"""Bundled demo experiment: two short recordings and their stimulus lists.

The wheel ships two ~30-40 s clips of real lab-style speech — one of real
words, one of pseudowords — so ``clicketysplit demo`` boots into a working
two-condition experiment with zero user setup. Both clips are the package
author's own voice, cut from recordings made for a phonetics experiment
(speaker f3; see README.md here for provenance). Each word is repeated
about twice in massed order, which is exactly the recording style the
detector and auto-labeler were built for.

:func:`setup_demo_experiment` copies the assets into a fresh temp
directory, writes a ``clicketysplit.json`` next to them, and returns the
experiment root::

    <tmp>/
    ├── clicketysplit.json
    ├── recordings/demo_speaker/real_words/rec.wav
    ├── recordings/demo_speaker/pseudowords/rec.wav
    └── stimulus_lists/{real_words,pseudowords}.txt
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


# (condition name, bundled wav, bundled stimulus list)
_DEMO_CONDITIONS: tuple[tuple[str, str, str], ...] = (
    ("real_words", "demo_real_words.wav", "real_words.txt"),
    ("pseudowords", "demo_pseudowords.wav", "pseudowords.txt"),
)


def _build_demo_config() -> ExperimentConfig:
    """Construct the in-memory ExperimentConfig for the demo experiment.

    * ``detection.backend = "silero"`` — a core dependency, so the demo
      always runs the same detector a real experiment would.
    * ``detection.denoise = False`` — the clips are already denoised, and
      this keeps the demo working without the noisereduce extra.
    * Both conditions are ``blocked`` (massed) with 2 expected repetitions,
      matching how the source recordings were actually made.
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
                name=cond_name,
                stimulus_list=f"stimulus_lists/{stim_file}",
                presentation_order="blocked",
                expected_reps_per_stimulus=2,
            )
            for cond_name, _wav, stim_file in _DEMO_CONDITIONS
        ],
        detection=DetectionConfig(backend="silero", denoise=False),
        labeling=LabelingConfig(),
        export=ExportConfig(),
    )


def setup_demo_experiment() -> Path:
    """Copy the bundled demo assets to a temp dir and return its root.

    The caller owns the returned directory; nothing here cleans it up. The
    OS will reap it from the temp area on reboot, which is fine for the
    demo command's "throwaway sandbox" semantics.
    """
    pkg_dir = Path(__file__).parent
    for _cond, wav, stim in _DEMO_CONDITIONS:
        for fname in (wav, stim):
            if not (pkg_dir / fname).is_file():
                raise FileNotFoundError(
                    f"Bundled demo asset missing at {pkg_dir / fname}. "
                    "The wheel may have been built without demo_assets/."
                )

    tmp = Path(tempfile.mkdtemp(prefix="clicketysplit_demo_"))

    stim_dir = tmp / "stimulus_lists"
    stim_dir.mkdir()
    for cond_name, wav, stim in _DEMO_CONDITIONS:
        rec_dir = tmp / "recordings" / "demo_speaker" / cond_name
        rec_dir.mkdir(parents=True)
        shutil.copy(pkg_dir / wav, rec_dir / "rec.wav")
        shutil.copy(pkg_dir / stim, stim_dir / stim)

    cfg = _build_demo_config()
    save_config(cfg, tmp / "clicketysplit.json")

    return tmp
