# Configuration

Every clicketysplit experiment is anchored by a `clicketysplit.json` at the
experiment root. All paths inside the file are interpreted **relative to the
config file's directory**, so the whole experiment is portable between machines.

The Setup Wizard writes this file for you. You can also edit it by hand —
it's plain JSON, validated by pydantic v2 on load.

The source of truth for this schema is
`_design/02_CONFIG_AND_DISCOVERY.md` in the repo.

## Top-level fields

`schema_version`
:   **Type:** `int` (required). **Default:** `1`.
    The version of the config schema. The loader rejects unknown versions
    with a "this config was written by a newer clicketysplit" message.
    Example: `1`.

`name`
:   **Type:** `str` (optional). **Default:** `""`.
    A human-readable label. No functional role.
    Example: `"fricative perception 2026"`.

`recordings_root`
:   **Type:** `str` (required). **Default:** `"recordings"`.
    Path to the recordings directory, relative to the config file.
    Example: `"recordings"`.

`stimulus_lists_root`
:   **Type:** `str` (required). **Default:** `"stimulus_lists"`.
    Path to the directory holding stimulus-list `.txt` files.
    Example: `"stimulus_lists"`.

`output_root`
:   **Type:** `str` (required). **Default:** `"output"`.
    Where detection results, session state, and exported tokens are written.
    Example: `"output"`.

## `speakers` (list)

Speakers are an **explicit list**, not auto-discovered. The Setup Wizard
populates it from a discovery scan but you can edit.

`id`
:   **Type:** `str` (required).
    Used in output filenames and on disk paths under `output/`.
    Example: `"speaker_01"`.

`subdir`
:   **Type:** `str` (optional). **Default:** same as `id`.
    Directory under `recordings_root` for this speaker's audio.
    Example: `"speaker_01"`.

## `conditions` (list)

`name`
:   **Type:** `str` (required).
    Used as the subdirectory under each speaker's folder *and* under
    `output/`. Example: `"condition_a"`.

`stimulus_list`
:   **Type:** `str` (required).
    Path (relative to the config dir) to a `.txt` file with one stimulus per
    line. Drives fuzzy-match autocomplete in Review and, for `cycled` /
    `blocked` orders, auto-labeling. Empty files are rejected at load time.
    Example: `"stimulus_lists/condition_a.txt"`.

`presentation_order`
:   **Type:** `"random" | "cycled" | "blocked"`. **Default:** `"random"`.
    How stimuli were presented. This drives whether labels auto-fill:
    - `random` — no auto-labeling. You label every token by hand.
    - `cycled` — stimuli repeat through the list (A B C A B C …).
    - `blocked` — each stimulus produced K times in a row (A A A B B B …).
    See [Detection](detection.md) for the auto-labeling algorithm.

`expected_reps_per_stimulus`
:   **Type:** `int`. **Default:** `3`.
    Only meaningful when `presentation_order = "blocked"`. A *hint*, not a
    constraint — once you anchor a label, the hint applies forward from
    there. Example: `3`.

## `detection`

`backend`
:   **Type:** `"silero" | "webrtc" | "energy"`. **Default:** `"silero"`.
    The VAD to use. The Setup Wizard filters to detectors whose extras are
    installed.

`vad_threshold`
:   **Type:** `float`. **Default:** `0.5`.
    Silero's speech-probability threshold. Ignored by `webrtc` and `energy`.

`min_segment_ms`
:   **Type:** `int`. **Default:** `150`.
    Drop segments shorter than this. Applies to all backends.

`min_silence_ms`
:   **Type:** `int`. **Default:** `150`.
    Minimum silence between segments to count as a gap.

`silence_margin_ms`
:   **Type:** `int`. **Default:** `25`.
    Extend each boundary outward by this much to capture word-edge consonants.

`denoise`
:   **Type:** `bool`. **Default:** `true`.
    Apply spectral-gating noise reduction before detection (requires the
    `[denoise]` extra). The denoised audio is written to `denoised.wav` and
    the review UI plays it back so you hear what gets exported.

## `labeling`

`min_word_duration_ms`
:   **Type:** `int`. **Default:** `250`.
    Segments shorter than this are typed `short_noise` (not exported).

`max_word_duration_ms`
:   **Type:** `int`. **Default:** `1400`.
    Segments longer than this are typed `crosstalk` (not exported).

`drop_intro_block`
:   **Type:** `bool`. **Default:** `false`.
    Opt-in port of an experiment-specific intro-detection heuristic. Off by
    default. Turn on if your recordings start with a long setup utterance.

## `export`

`pad_ms`
:   **Type:** `int`. **Default:** `20`.
    Context added on each side of a token when slicing.

`fade_ms`
:   **Type:** `int`. **Default:** `3`.
    Linear fade in/out at the token edges to prevent clicks.

`format`
:   **Type:** `"wav" | "flac"`. **Default:** `"wav"`.
    Output container. WAV is broadly compatible; FLAC is smaller.

`produce_csv`
:   **Type:** `bool`. **Default:** `true`.
    Write `tokens.csv` alongside the WAVs.

`produce_textgrid`
:   **Type:** `bool`. **Default:** `false`.
    Write a Praat `.TextGrid` describing the source audio (requires the
    `[praat]` extra).

## Complete example

```json
{
  "schema_version": 1,
  "name": "my fricative experiment",
  "recordings_root": "recordings",
  "stimulus_lists_root": "stimulus_lists",
  "output_root": "output",

  "speakers": [
    { "id": "speaker_01", "subdir": "speaker_01" },
    { "id": "speaker_02", "subdir": "speaker_02" }
  ],

  "conditions": [
    {
      "name": "condition_a",
      "stimulus_list": "stimulus_lists/condition_a.txt",
      "presentation_order": "random"
    },
    {
      "name": "condition_b",
      "stimulus_list": "stimulus_lists/condition_b.txt",
      "presentation_order": "blocked",
      "expected_reps_per_stimulus": 3
    }
  ],

  "detection": {
    "backend": "silero",
    "vad_threshold": 0.5,
    "min_segment_ms": 150,
    "min_silence_ms": 150,
    "silence_margin_ms": 25,
    "denoise": true
  },

  "labeling": {
    "min_word_duration_ms": 250,
    "max_word_duration_ms": 1400,
    "drop_intro_block": false
  },

  "export": {
    "pad_ms": 20,
    "fade_ms": 3,
    "format": "wav",
    "produce_csv": true,
    "produce_textgrid": false
  }
}
```
