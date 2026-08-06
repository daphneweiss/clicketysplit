# clicketysplit demo assets

This directory holds the tiny two-condition demo experiment that ships
inside the wheel so `clicketysplit demo` works on a fresh install with
zero setup.

## Contents

| File | Purpose |
|------|---------|
| `demo_real_words.wav`  | ~23 s, 16 kHz mono PCM_16. Real English words, ~2 massed repetitions each. |
| `demo_pseudowords.wav` | ~31 s, 16 kHz mono PCM_16. Pseudowords, ~2 massed repetitions each. |
| `real_words.txt`       | Stimulus list for the real-word clip (6 words). |
| `pseudowords.txt`      | Stimulus list for the pseudoword clip (8 items). |
| `__init__.py`          | `setup_demo_experiment()` — copies these into a temp dir and writes a `clicketysplit.json`. |

## Provenance

Both clips are the package author's own voice, excerpted from stimulus
recordings made for a word learning experiment (speaker `f3`, conditions
`filler_word` and `filler_pseudo`). The excerpts were cut at inter-cluster silences
from the denoised session audio and downsampled to 16 kHz mono. No other
speaker's audio ships with this package.

This is real experiment-style speech — massed repetitions with natural
pacing, the occasional extra or missing repetition included — so the demo
exercises the detector and the gap-clustering auto-labeler on exactly the
kind of material they were built for.

## Regenerating

The clips are cut from the source stimulus recordings (not distributed with
this repo). The cut points are the token cluster boundaries in the
original tool's `proposed_segments.json`: take the first N stimuli's
complete clusters, cut at the midpoint of the surrounding silences,
downsample 44.1 kHz → 16 kHz (`scipy.signal.resample_poly(x, 160, 441)`),
and write PCM_16. If you change the clips, update the stimulus lists,
this README, and the assertions in `tests/test_demo.py`.
