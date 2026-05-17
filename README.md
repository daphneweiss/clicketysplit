# clicketysplit

A browser-based tool for extracting single-word tokens from speech
recordings — whether your speaker produced each stimulus once, many times
in a row, or in randomized order.

> **Status: pre-alpha, under active construction.** Nothing here works yet.
> The design is in `_design/` (locally, not committed); implementation is
> coming module by module. Follow this repo for the first usable release.

## Planned install

```bash
pip install clicketysplit       # core (energy-based VAD)
pip install 'clicketysplit[all]' # + Silero, webrtcvad, denoise, MP3 via ffmpeg, Praat TextGrid
clicketysplit serve              # opens the local web app
clicketysplit demo               # runs against bundled demo data
```

Requires Python 3.10+. Optional system dependency: `ffmpeg` (only for MP3/M4A input).

## License

MIT — see [LICENSE](LICENSE).
