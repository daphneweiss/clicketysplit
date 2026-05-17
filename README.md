# clicketysplit

A browser-based segmentation tool for speech recordings of single-word
stimuli — built for the way phonetics and psycholinguistics experiments
actually run.

You give it a stimulus list (real words, pseudowords, whatever your study
needs) and a recording. It proposes word boundaries, auto-labels each
token from the stimulus list, and hands you a fuzzy-match autocomplete
review UI tuned for fast keystroke-level correction. The output is a
folder of cleanly-sliced WAV tokens plus a manifest — optionally a Praat
TextGrid so the source recording stays usable in Praat.

## Why this exists

For experiments where each speaker reads a stimulus list — once, blocked
in repetitions, or in a randomized order — Praat is overkill and slow:
manually marking, zooming, naming, and exporting every token by hand
costs hours of RA time per session and accumulates labeling errors.

clicketysplit is built specifically for that workflow:

- **Stimulus-list aware.** You declare what the speaker was supposed to
  say. The tool's autocomplete and auto-labeling work just as well on
  *pseudowords* (`mip`, `glorf`, `dax`) as on real words — there's no
  dictionary or forced-aligner assuming you used English lexical items.
- **Fast labeling for RAs.** Auto-labels are walked forward from anchors
  based on the condition's presentation order (`random` / `cycled` /
  `blocked`). When the RA corrects one label, every downstream label
  re-computes from that anchor. Fewer keystrokes, fewer drift errors when
  the speaker over- or under-produces.
- **Praat-compatible, but you only touch Praat if you want to.** Optional
  TextGrid export means the source recording is still useful in Praat for
  follow-up acoustic analysis. The fast path — propose → review → export
  WAV tokens — never requires opening Praat at all.
- **Browser UI, local backend, no install for participants.** It's a
  Python package; you `pip install` it, run `clicketysplit serve`, and
  the segmenter opens in your browser. No Electron, no cloud, no
  per-machine accounts.

## Status

**Pre-alpha, under active construction.** The first usable release is
being assembled module by module. Star or watch the repo for the v0.1.0
announcement.

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
