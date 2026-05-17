# 05 — Export

## What gets produced

For every `export` call on a (speaker, condition), clicketysplit writes
artifacts into `output/<speaker>/<condition>/`:

```
output/<speaker>/<condition>/
├── ...                             # detection-time files (segments JSON, denoised.wav, overview.png)
├── <condition>.TextGrid            # only if config.export.produce_textgrid=true
└── tokens/
    ├── speaker_01_apple-1.wav
    ├── speaker_01_apple-2.wav
    ├── speaker_01_banana-1.wav
    ├── ...
    ├── token_manifest.json         # always
    └── tokens.csv                  # always (config.export.produce_csv defaults true)
```

**TextGrid sits one level up from `tokens/`.** TextGrids describe the source
audio (with token intervals as a tier), not the sliced tokens themselves —
keeping them next to the segments JSON makes the relationship obvious.
The TextGrid section below restates this; the earlier draft showed it
inside `tokens/` by mistake.

The previous pipeline's "finalize" step (best → `final/`, rest → `alternates/`)
is **dropped**. Users select which tokens to export in the GUI; what gets
written is exactly what was selected. If they want multiple tokens per word,
they select multiple — the `-N` suffix communicates which is which.

## Filename convention

```
<speaker_id>_<assigned_name>-<N>.<ext>
```

- `speaker_id` from the config; `assigned_name` from the segment label;
  `N` is 1-indexed within the (speaker, condition, word) group.
- `assigned_name` is run through `slugify_label()` which replaces anything
  outside `[A-Za-z0-9_-]` with `_`. Stimulus lists with non-ASCII labels work,
  but the filenames stay portable.
- The full word→count is preserved in `token_manifest.json` so the user can
  reconstruct the slug if needed.

Files are written atomically: write to `<name>.tmp`, fsync, rename. Prevents
half-written files if the user closes the browser mid-export.

## Slicing logic (`export/tokens.py`)

```python
def export_tokens(
    audio: np.ndarray,
    sr: int,
    segments: list[LabeledSegment],
    output_dir: Path,
    speaker_id: str,
    *,
    pad_ms: int = 20,
    fade_ms: int = 3,
    audio_format: Literal["wav", "flac"] = "wav",
) -> ExportResult:
    """
    Slice each word-typed segment with status=="accepted" out of `audio`
    and write to disk.
    Adds `pad_ms` of context on each side (clipped to audio bounds), applies
    `fade_ms` linear fade in/out to prevent clicks at the boundaries.
    Returns a manifest of what was exported.
    """
```

The current implementation in `segment_recording.py:export_tokens` is close
to right; port it with these changes:

1. Take `pad_ms`/`fade_ms` as arguments instead of reading from
   `EXPORT_PAD_MS` / `FADE_MS` module globals.
2. Make the per-word token index strictly sequential by source order (not by
   token_index from the segments, which can be stale after manual reordering).
3. Skip segments where `status != "accepted"` OR `segment_type != "word"` OR
   `assigned_name` is empty. Return per-reason skip counts in the result
   (`skipped_not_accepted`, `skipped_not_word`, `skipped_unnamed`) so the
   frontend can surface what got left out and why.
4. Use atomic writes (`tmp` + rename).
5. Don't write a token if its slice would be empty after padding clipping
   (defensive against bad boundaries).

## `token_manifest.json` schema

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
    },
    ...
  ]
}
```

## `tokens.csv` schema

One row per exported token. UTF-8, RFC-4180-quoted commas.

```
speaker_id,condition,assigned_name,token_index,filename,start_sec,end_sec,duration_ms,padded_duration_ms,sample_rate,audio_format
speaker_01,condition_a,apple,1,speaker_01_apple-1.wav,1.23,1.78,550,590,44100,wav
speaker_01,condition_a,apple,2,speaker_01_apple-2.wav,3.41,3.97,560,600,44100,wav
...
```

Headers are stable across versions; if we add columns we append (don't insert
or reorder).

## TextGrid export (`export/textgrid.py`)

Opt-in via `config.export.produce_textgrid = true`. Requires
`praat-parselmouth` (the `[praat]` extra).

The current `export_textgrid` in `segment_recording.py` produces a single
TextGrid for the concatenated condition audio. Keep that behavior, with one
addition: include a `file_boundaries` tier marking where one source file ends
and the next begins so users opening this in Praat can see the seams.

Tiers:
1. `tokens` — point or interval tier (interval) of every exported word token.
   Label = `assigned_name`. Non-word segments not included.
2. `all_segments` — every segment including rejected/noise/intro, label is
   `segment_type`. Useful for debugging detection.
3. `file_boundaries` — point tier marking offsets between source recordings
   when concatenation was used.

Output path: `output/<speaker>/<condition>/<condition>.TextGrid` (alongside
the segments JSON, not inside `tokens/` — TextGrids describe the source
audio, not the slices).

## Overview plot (`detection/pipeline.py`)

Port `plot_overview` from the current code as a side-product of detection.
Saved to `output/<speaker>/<condition>/overview.png`. The frontend pulls it
into the Setup-step "did detection look reasonable?" summary panel.

Keep it minimal: time axis, RMS envelope, colored bars for detected
segments tinted by `segment_type` (word=green, short_noise=gray,
crosstalk=red, intro=yellow). Don't overengineer.

## What we are NOT exporting in v1

- Praat Pitch/Intensity objects.
- Spectrogram images per token.
- Per-token PNG/SVG waveforms.
- Anything bundled into a single archive (ZIP). Users zip their own output
  dir if they need to ship to collaborators.

These are all reasonable future asks but adding them in v1 means more
test surface for marginal value.

## Export errors

| Scenario | Handling |
|---|---|
| Output dir already has tokens from a previous export | Overwrite. We don't try to merge. (Documented behavior.) |
| Selected token has empty `assigned_name` | Skip with `skipped_unnamed` count in result. (Common when the user forgot to label some tokens — the response surfaces the count so the frontend can show "12 tokens skipped because they have no label" and offer to jump back to the review step.) |
| Selected token's source audio missing | Abort the whole export with a clear error citing the missing file. (Partial exports are a worse failure mode than no export.) |
| Disk full mid-write | Atomic-write guarantees no half-token; the export aborts with the IO error from the first failed write and reports how many tokens succeeded before failure. |

## Performance

Detection is the slow step (Silero, denoising); export is fast — slicing and
writing a few hundred WAVs is a fraction of a second on any modern machine.
No need for threading or progress bars in v1 for export; the GUI can show a
plain "exporting..." spinner.
