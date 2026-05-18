# Exports

For every export, clicketysplit writes one WAV (or FLAC) per selected token
plus a JSON manifest and a CSV. A Praat TextGrid is opt-in via the
`export.produce_textgrid` config flag.

The source of truth for this schema is `_design/05_EXPORT.md` in the repo.

## Output directory layout

```
output/<speaker>/<condition>/
├── proposed_segments.json
├── reviewed_segments.json
├── denoised.wav                 (if detection.denoise was true)
├── overview.png
├── <condition>.TextGrid         (if export.produce_textgrid is true)
└── tokens/
    ├── speaker_01_apple-1.wav
    ├── speaker_01_apple-2.wav
    ├── speaker_01_banana-1.wav
    ├── ...
    ├── token_manifest.json
    └── tokens.csv
```

The TextGrid sits *next to* `tokens/`, not inside it — TextGrids describe the
source audio, not the slices.

## Filename convention

```
<speaker_id>_<assigned_name>-<N>.<ext>
```

- `speaker_id` comes from your config.
- `assigned_name` is the token's label, run through a slugifier that replaces
  anything outside `[A-Za-z0-9_-]` with `_`. Non-ASCII labels work but the
  filenames stay portable.
- `N` is 1-indexed within the `(speaker, condition, word)` group. Sequencing
  follows source-order of the segments.
- `ext` is `wav` or `flac` per `export.format`.

The original (non-slugified) label is preserved in `token_manifest.json`
under `tokens[].assigned_name`.

Files are written atomically: write to `<name>.tmp`, fsync, rename.

## Default selection rule

The Select step pre-checks every segment where **all three** are true:

- `segment_type == "word"`
- `status == "accepted"`
- `assigned_name` is non-empty

Tokens with empty labels appear under an "Unlabeled" group and are *not*
exported until you give them a label.

## `token_manifest.json`

One per `(speaker, condition)` export. Always written.

```json
{
  "schema_version": 1,
  "speaker_id": "speaker_01",
  "condition": "condition_a",
  "exported_at": "2026-05-17T14:23:18Z",
  "audio_source": "output/speaker_01/condition_a/denoised.wav",
  "sample_rate": 44100,
  "pad_ms": 20,
  "fade_ms": 3,
  "audio_format": "wav",
  "tokens_per_word": { "apple": 2, "banana": 1 },
  "tokens": [
    {
      "filename": "speaker_01_apple-1.wav",
      "assigned_name": "apple",
      "token_index": 1,
      "start_sec": 1.23,
      "end_sec": 1.78,
      "duration_ms": 550,
      "padded_start_sec": 1.21,
      "padded_end_sec": 1.80,
      "padded_duration_ms": 590
    }
  ]
}
```

## `tokens.csv`

One row per exported token. UTF-8, RFC-4180-quoted. Written by default
(`export.produce_csv: true`).

Columns:

```
speaker_id,condition,assigned_name,token_index,filename,start_sec,end_sec,duration_ms,padded_duration_ms,sample_rate,audio_format
```

Example:

```
speaker_01,condition_a,apple,1,speaker_01_apple-1.wav,1.23,1.78,550,590,44100,wav
speaker_01,condition_a,apple,2,speaker_01_apple-2.wav,3.41,3.97,560,600,44100,wav
speaker_01,condition_a,banana,1,speaker_01_banana-1.wav,5.12,5.78,660,700,44100,wav
```

Headers are stable across versions. New columns are appended, never inserted
or reordered.

## TextGrid (opt-in)

Set `export.produce_textgrid: true` in your config and install the `[praat]`
extra. clicketysplit writes one `.TextGrid` per condition, describing the
*source audio* (i.e. the concatenated condition recording), with three tiers:

1. `tokens` — interval tier of every exported word token. Label =
   `assigned_name`. Non-word segments not included.
2. `all_segments` — every segment including rejected / `short_noise` /
   `crosstalk` / `intro`. Label = `segment_type`. Useful for debugging
   detection.
3. `file_boundaries` — point tier marking where one source file ended and the
   next began (when condition audio came from multiple files).

The TextGrid path is `output/<speaker>/<condition>/<condition>.TextGrid`.

## Slicing details

- Each token is padded by `pad_ms` on each side, clamped to the audio bounds.
- A `fade_ms` linear fade in/out is applied at the token edges to prevent
  clicks.
- Segments where the padded slice would be empty are skipped (defensive
  against bad boundaries).

## Skipped tokens

The export response reports skipped tokens grouped by reason:

- `skipped_not_accepted` — `status != "accepted"`.
- `skipped_not_word` — `segment_type != "word"`.
- `skipped_unnamed` — `assigned_name` empty.

If anything was skipped, the UI surfaces the count and offers to jump back to
the relevant review step.

## What we don't export

Out of scope for v0.1.0:

- Praat Pitch / Intensity objects.
- Per-token spectrogram images.
- Per-token PNG / SVG waveforms.
- ZIP bundles. Zip your `output/` directory yourself if you need to ship to
  collaborators.
