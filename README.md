# clicketysplit

A browser-based segmentation tool for speech recordings of single-word or pseudoword 
stimuli

You give it a stimulus list (real words, pseudowords, whatever your study
needs) and a recording. It proposes word boundaries, auto-labels each
token from the stimulus list (optional), and includes a fuzzy-match autocomplete
review UI for quick review/corrections. The output is a
folder of cleanly-sliced WAV tokens plus a manifest — optionally a Praat
TextGrid so the source recording stays usable in Praat. Keyboard shortcut to play audio is the same as Praat for easy migration.

## Why this exists

This tool was built to replace the Praat-based workflow used in many labs for this type of recording session (single-word tokens read in sequence, which may or may not be real words). Clickety Split offers an intuitive, browser-based tool for auto-detection of word boundaries and rapid file naming.

- **Stimulus-list aware.** You declare what the speaker was supposed to
  say. The tool's autocomplete and auto-labeling work just as well on
  *pseudowords* as on real words (there's no
  dictionary or forced-aligner assuming you used English lexical items, and no need to transcribe your items.)
- **Fast labeling for RAs.** Auto-labels are walked forward from anchors
  based on the condition's presentation order (`random` / `cycled` /
  `blocked`). When the RA corrects one label, every downstream label
  re-computes to avoid unnecessary typing.                           
- **Praat-compatible, but you only touch Praat if you want to.** Optional
  TextGrid export means the source recording is still useful in Praat for
  follow-up acoustic analysis. The fast path — propose → review → export
  WAV tokens — never requires opening Praat at all.
- **Browser UI, local backend, no install for participants.** It's a
  Python package; you `pip install` it, run `clicketysplit serve`, and
  the segmenter opens in your browser. No Electron, no cloud.

## Status

**Pre-alpha, under active construction.** I've extensively used this tool in my own work; currently refactoring to remove my baked-in assumptions and make it usable outside my lab.

## Planned install

```bash
pip install clicketysplit          # core (energy-based VAD, WAV/FLAC/OGG)
pip install 'clicketysplit[all]'   # + Silero VAD, webrtcvad, denoise, MP3 via ffmpeg, TextGrid
clicketysplit serve                # opens the local web app
clicketysplit demo                 # runs against bundled demo data
```

Requires Python 3.10+. Optional system dependency: `ffmpeg` (only for
MP3/M4A input).

## License

MIT — see [LICENSE](LICENSE).
