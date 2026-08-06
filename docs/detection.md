# Detection

clicketysplit ships three voice-activity detectors. The energy detector is
always available; the other two are optional extras. You pick which one to
use in the Setup Wizard or by editing `clicketysplit.json`'s
`detection.backend`.

The implementation lives in `src/clicketysplit/detection/`.

## Which detector should I use?

| Detector | Install | When to use |
|---|---|---|
| `energy` | Default — no extras needed | Baseline. Clean recordings with clear silences. The always-available fallback. |
| `webrtc` | `pip install 'clicketysplit[webrtc]'` | Lightweight frame-based VAD. Good for clean recordings; faster than Silero. |
| `silero` | `pip install 'clicketysplit[silero]'` | Best for noisy recordings. Heavier install (ONNX runtime + model) and slower first detection. |

If you're not sure, start with `silero` (assuming you installed `[all]`) and
fall back to `energy` or `webrtc` if Silero misses your speech.

## Shared parameters

These apply to **all three** detectors:

`min_segment_ms` (default 150)
:   Drop any proposed segment shorter than this. Filters out clicks, taps,
    and breath noise.

`min_silence_ms` (default 150)
:   Minimum silence between segments. Two bursts separated by less are merged
    into one segment.

`silence_margin_ms` (default 25)
:   Pad each boundary outward by this much before refinement. Useful for
    capturing the leading consonant of `[s]`, `[ʃ]`, `[f]` etc., which can be
    quieter than the vowel and get clipped by a tight VAD threshold.

## Detector-specific parameters

`vad_threshold` (default 0.5, **silero only**)
:   Silero's per-frame speech-probability threshold. Lower = more permissive
    (catches more speech but more false positives). Higher = stricter.
    Ignored by `webrtc` and `energy`.

The `webrtc` and `energy` detectors have additional hardcoded internals
ported from the legacy pipeline; the public knobs are the four parameters
above.

## What happens at detection time

For each speaker × condition cell:

1. Load and (if needed) concatenate the audio files.
2. If `detection.denoise: true` and `noisereduce` is installed, write a
   `denoised.wav` and segment from that.
3. Run the configured detector → list of `(start, end)` segment proposals.
4. Run boundary refinement against the original-rate energy envelope to
   tighten each boundary to a sub-frame edge.
5. Classify each segment by duration:
    - `word` — duration in `[min_word_duration_ms, max_word_duration_ms]`
    - `short_noise` — shorter than `min_word_duration_ms`
    - `crosstalk` — longer than `max_word_duration_ms`
    - `intro` — only if `labeling.drop_intro_block: true` and the
      initial-block heuristic matched.
6. Apply auto-labeling (below).
7. Write `proposed_segments.json` and `overview.png`.

## Auto-labeling

Auto-labeling is **per condition** and driven by `presentation_order`, *not*
by the detector. You can mix presentation orders across conditions in the
same experiment.

### `random` (default)

Every word-typed segment gets `assigned_name: ""`. You label each one by
hand in Review using fuzzy-match autocomplete against the stimulus list.

### `cycled`

Labels walk the stimulus list, stride 1, cycling forever:

```
stimulus_list = ["apple", "banana", "cherry"]
9 word-typed segments → [apple, banana, cherry, apple, banana, cherry, apple, banana, cherry]
```

### `blocked`

Each stimulus is assigned to `expected_reps_per_stimulus` consecutive segments
before advancing to the next stimulus:

```
stimulus_list = ["apple", "banana", "cherry"], K = 3
9 word-typed segments → [apple, apple, apple, banana, banana, banana, cherry, cherry, cherry]
```

If the speaker produced more tokens than `len(stimulus_list) * K`, the walk
wraps back to the first stimulus.

### Anchor-and-walk-forward

In `cycled` and `blocked`, every edit you make in Review becomes a **user
anchor**, and every *downstream* tentative label is recomputed from that
anchor forward. This makes the tool tolerant of speakers who produced more
or fewer tokens than the script called for.

For example, in `blocked` with `K = 3` and `stimulus_list = ["apple",
"banana", "cherry"]`, you might initially see:

```
[apple, apple, apple, banana, banana, cherry, cherry, cherry, cherry]
```

If you edit index 5 from `cherry` to `banana` (the speaker actually did one
more banana before moving on), the downstream labels recompute:

```
[apple, apple, apple, banana, banana, banana, banana, banana, cherry]
```

Anchors are keyed by **word-segment index**, so marking a noisy segment as
`short_noise` does not shift downstream labels.

An anchor with an empty label tells the walk to stop labeling from that
point onward — useful if the speaker trailed off.

## Re-running detection

If you change a detection parameter and re-run, clicketysplit overwrites
`proposed_segments.json` — but only after you confirm. If
`reviewed_segments.json` already exists for that condition, the backend
returns a `409 already_reviewed`. The frontend prompts you to either keep
the existing review or pass `force=true` to overwrite. Re-detection wipes
your hand labels; you've been warned.
