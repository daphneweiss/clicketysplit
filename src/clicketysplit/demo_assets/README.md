# clicketysplit demo assets

This directory holds the tiny demo experiment that ships inside the wheel
so that ``clicketysplit demo`` works on a fresh install with zero user
setup.

## Contents

| File | Purpose |
|------|---------|
| `demo_recording.wav` | ~30 s, 16 kHz mono, PCM_16 WAV with ~10 word tokens in mixed order. |
| `demo_stimuli.txt`   | Stimulus list (5 simple words) covering the spoken tokens. |
| `__init__.py`        | `setup_demo_experiment()` — copies these into a temp dir and writes a minimal `clicketysplit.json`. |

## Stimulus list

```
apple
banana
cherry
dog
egg
```

## How `demo_recording.wav` was generated

The WAV was synthesized using **Google Text-to-Speech (`gTTS`)** to render
each word as a short MP3, then decoded to 16 kHz mono PCM via
**`miniaudio`** (which decodes MP3 without an `ffmpeg` system dependency),
trimmed to remove leading/trailing silence, normalized per-token, and
concatenated with ~2.3 s of digital silence between tokens. The result is
30.00 s at 16 kHz mono PCM_16 — 960 044 bytes, comfortably under the 1 MB
budget called out in `_design/07_TESTING_AND_DOCS.md` §"Demo data".

The token order is mixed (not blocked) on purpose. See the same design
document: the demo is the first thing a new user sees, so it must not
suggest the tool requires a blocked-repetitions paradigm. The actual order
is:

```
dog cherry apple banana egg cherry apple dog egg banana
```

so each of the five stimuli appears twice across ten tokens.

## License / attribution

The audio was synthesized through Google's TTS service via `gTTS`. The
synthesized waveform is a derivative work; redistribution alongside this
open-source project is consistent with `gTTS`'s usage. There is no
copyrighted source recording involved. The stimulus list contains only
common English nouns and carries no third-party rights.

## Regenerating

Neither `gTTS` nor `miniaudio` is a runtime dependency of clicketysplit
— they were used only at asset-build time and are NOT listed in
`pyproject.toml`. If you want to regenerate the bundled WAV, install them
into your dev environment temporarily:

```bash
pip install gtts miniaudio
```

then run a script equivalent to:

```python
from gtts import gTTS
import miniaudio, io, numpy as np, soundfile as sf
from pathlib import Path

WORDS = ["dog", "cherry", "apple", "banana", "egg",
         "cherry", "apple", "dog", "egg", "banana"]
SR = 16000
GAP_SEC = 2.3

def synth(word: str) -> np.ndarray:
    tts = gTTS(text=word, lang="en")
    buf = io.BytesIO(); tts.write_to_fp(buf)
    d = miniaudio.decode(
        buf.getvalue(),
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=1,
        sample_rate=SR,
    )
    pcm = np.frombuffer(bytes(d.samples), dtype=np.int16).astype(np.float32) / 32768.0
    return pcm

parts = [np.zeros(int(0.3 * SR), dtype=np.float32)]
for w in WORDS:
    a = synth(w)
    peak = float(np.max(np.abs(a)))
    if peak > 0:
        a = a * (0.7 / peak)
    parts.append(a)
    parts.append(np.zeros(int(GAP_SEC * SR), dtype=np.float32))
parts[-1] = np.zeros(int(0.4 * SR), dtype=np.float32)

audio = np.concatenate(parts).astype(np.float32)
# Pad to exactly 30 s.
target = int(30.0 * SR)
if len(audio) < target:
    audio = np.concatenate([audio, np.zeros(target - len(audio), dtype=np.float32)])
else:
    audio = audio[:target]

sf.write("demo_recording.wav", audio, SR, format="WAV", subtype="PCM_16")
```

If you significantly change the stimulus list or word count, also update
`demo_stimuli.txt`, the README above, and the assertions in
`tests/test_demo.py`.

## Why this isn't real speech

`gTTS` produces clear, intelligible synthetic speech rather than a
"credibly noisy real-room" recording. That tradeoff is deliberate: the
asset has to be reproducible offline-of-a-microphone, redistributable
without consent forms, and small enough to ship in a wheel. A future
release may swap in a permissively-licensed real recording; until then,
synthetic is the contract.
