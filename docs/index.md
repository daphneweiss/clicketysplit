# clicketysplit

A browser-based tool for extracting single-word tokens from speech recordings.
You point it at a folder of recordings, it proposes word boundaries with a VAD,
you review and label them in a four-step wizard, and it writes one WAV per
token plus a manifest and CSV. No Praat, no Audacity, no hand-segmenting from
scratch.

## Install and run

```
pip install 'clicketysplit[all]'
clicketysplit demo
```

That opens a browser at `http://127.0.0.1:5000` already loaded with a bundled
demo experiment. See [Quickstart](quickstart.md) for the five-minute walkthrough.

## What it does

- **Detects word boundaries** using one of three VADs (energy / WebRTC / Silero).
- **Labels tokens** by fuzzy-matching against your stimulus list, with optional
  auto-labeling for `cycled` or `blocked` presentation orders.
- **Exports** WAV (or FLAC) tokens with a JSON manifest, CSV, and optional
  Praat TextGrid.
- **Stays out of your way**: stimulus lists, condition names, and presentation
  orders are yours to define — nothing about the experiment is hardcoded.
