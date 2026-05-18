# Install

## Python version

clicketysplit requires **Python 3.10 or newer**. 3.11 and 3.12 are tested in CI.

Check your version:

```
python --version
```

## Pick an install flavor

clicketysplit ships a small default install plus optional extras for the
heavier detection backends and lossy-audio formats. Install only what you need.

| Command | What you get |
|---|---|
| `pip install clicketysplit` | Energy-based VAD, WAV/FLAC/OGG/OPUS audio. Minimal, no heavy deps. |
| `pip install 'clicketysplit[webrtc]'` | Adds WebRTC VAD (lightweight, good for clean recordings). |
| `pip install 'clicketysplit[silero]'` | Adds Silero VAD (ONNX model + onnxruntime, best for noisy recordings). |
| `pip install 'clicketysplit[denoise]'` | Adds spectral-gating noise reduction. |
| `pip install 'clicketysplit[mp3]'` | Adds MP3/M4A loading (also requires ffmpeg on PATH). |
| `pip install 'clicketysplit[praat]'` | Adds Praat TextGrid export. |
| `pip install 'clicketysplit[all]'` | Everything above. **Most users want this.** |

The `[all]` install is the most convenient but pulls in `onnxruntime` and a
few hundred MB of model + native libraries. If disk space is tight, install
only the extras you need.

## ffmpeg

You need `ffmpeg` on your `PATH` to load `.mp3`, `.m4a`, or `.aac` files.
WAV/FLAC/OGG/OPUS work without it.

=== "macOS"

    ```
    brew install ffmpeg
    ```

=== "Linux (Debian/Ubuntu)"

    ```
    sudo apt install ffmpeg
    ```

=== "Windows"

    Download a static build from <https://ffmpeg.org/download.html> and add
    the `bin/` folder to your `PATH`. Then open a new terminal and confirm:

    ```
    ffmpeg -version
    ```

## Verify the install

```
clicketysplit --version
```

Expected output:

```
0.1.0
```

Then launch the bundled demo:

```
clicketysplit demo
```

This opens your browser at `http://127.0.0.1:5000` with a demo experiment
already set up. Head to [Quickstart](quickstart.md) for the walkthrough.

## Common gotchas

### Windows: quote paths with spaces

Windows paths with spaces or backslashes need quotes:

```
clicketysplit serve --experiment "C:\Users\me\My Experiments\fricatives\clicketysplit.json"
```

In the Setup Wizard's "Recordings root" text field, use forward slashes or
double backslashes — single backslashes get interpreted as escapes by some
browsers when you paste.

### The `[all]` install is large

`pip install 'clicketysplit[all]'` pulls `onnxruntime`, the Silero VAD model,
`praat-parselmouth`, and `noisereduce`. Expect ~500 MB of disk usage after the
install. If that's too much, pick just the extras you need from the matrix above.

### "Detector unavailable" in the wizard

The Setup Wizard greys out detectors whose extras are missing. The tooltip
tells you which `pip install` line to run. After installing, restart
`clicketysplit serve` — the capabilities are detected at boot.

### Use a virtual environment

Always install into a virtualenv, not into system Python:

```
python -m venv .venv
source .venv/bin/activate          # Linux/macOS
.venv\Scripts\activate             # Windows
pip install 'clicketysplit[all]'
```
