# 03 — Audio I/O and detection backends

## Audio I/O (`audio_io.py`)

A small module hiding all format and channel concerns from the rest of the
codebase. The detection engine and the export engine both work with
`(audio: np.ndarray[float32, 1D], sr: int)` — they never look at file
extensions or channel layouts.

### Supported formats

```python
SUPPORTED_EXTENSIONS = {
    # Lossless via soundfile (libsndfile)
    ".wav", ".flac", ".ogg", ".opus",
    # Lossy via pydub + ffmpeg (only if HAS_FFMPEG)
    ".mp3", ".m4a", ".aac",
}
```

The set is built at import time from runtime feature detection:

```python
import soundfile as sf
import shutil

_SF_FORMATS = {f".{fmt.lower()}" for fmt in sf.available_formats()}
HAS_FFMPEG = shutil.which("ffmpeg") is not None
try:
    import pydub                       # noqa
    HAS_PYDUB = True
except ImportError:
    HAS_PYDUB = False
```

If a user opens an `.mp3` and ffmpeg isn't on PATH, we raise an error with the
fix: "Install ffmpeg from https://ffmpeg.org or `pip install clicketysplit[mp3]`
and ensure ffmpeg is on PATH."

### Loading

```python
def load_audio(path: Path, target_sr: int | None = None) -> tuple[np.ndarray, int]:
    """
    Load an audio file as mono float32.

    - WAV/FLAC/OGG/OPUS via soundfile (no extra deps).
    - MP3/M4A/AAC via pydub (needs ffmpeg).
    - Multi-channel inputs are downmixed to mono (mean across channels).
    - If `target_sr` is given, resample with scipy.signal.resample_poly.
    """
```

Resampling uses `resample_poly` for speed and quality; we never use
soundfile's "resample on read" because it doesn't exist. Resampling is only
done when the consumer asks for it (Silero needs 16 kHz; export uses native
SR).

### Saving

```python
def save_audio(path: Path, audio: np.ndarray, sr: int, format: str = "wav") -> None:
    """
    Write mono float32 audio. `format` is the container ('wav' or 'flac').
    `path` should already have the correct extension.
    """
```

We deliberately only write WAV/FLAC — MP3 encoding is finicky, ffmpeg-dependent,
and lossy export is a bad default for a token corpus.

### Concatenating per-condition files

The current pipeline supports multiple files per condition by concatenating
them with a short silence between. Keep that behavior:

```python
def concatenate(audio_paths: list[Path], silence_sec: float = 0.5) -> tuple[np.ndarray, int]:
    """
    Load and concatenate audio files with a silence gap between each.
    All files must share a sample rate after loading; we error clearly if not.
    Returns (audio, sr).
    """
```

Return the boundary timestamps so the frontend can show "file 1 ends here" /
"file 2 begins here" dividers on the waveform.

## Detection backends

All detectors share an interface — they take mono float32 audio and return a
list of proposed segments plus an `analysis` dict for the frontend's waveform
plot.

### Base interface

```python
# src/clicketysplit/detection/base.py
from dataclasses import dataclass, field
from typing import Protocol

@dataclass
class ProposedSegment:
    start: float          # seconds in source audio
    end: float            # seconds in source audio
    duration_ms: float

@dataclass
class DetectionResult:
    segments: list[ProposedSegment]
    analysis: dict = field(default_factory=dict)
    # analysis keys (all optional, used by the frontend overview/waveform):
    #   "times":            np.ndarray[float]   — time axis for energy
    #   "energy":            np.ndarray[float]   — RMS or log-mel envelope
    #   "is_speech":         np.ndarray[bool]    — frame-level VAD mask
    #   "energy_threshold":  float               — threshold used for plot

class Detector(Protocol):
    name: str                 # "silero", "webrtc", "energy"
    requires_extras: list[str]   # e.g. ["silero"]

    @classmethod
    def is_available(cls) -> bool: ...

    def detect(
        self,
        audio: np.ndarray,
        sr: int,
        *,
        min_segment_ms: int,
        min_silence_ms: int,
        silence_margin_ms: int,
        **backend_specific,
    ) -> DetectionResult: ...
```

### Registry

```python
# src/clicketysplit/detection/__init__.py
from .silero import SileroDetector
from .webrtc import WebRTCDetector
from .energy import EnergyDetector

_REGISTRY: dict[str, type[Detector]] = {
    "silero": SileroDetector,
    "webrtc": WebRTCDetector,
    "energy": EnergyDetector,
}

def get_detector(name: str) -> Detector:
    cls = _REGISTRY[name]
    if not cls.is_available():
        raise RuntimeError(
            f"Detector '{name}' requires extras: {cls.requires_extras}. "
            f"Install with: pip install 'clicketysplit[{','.join(cls.requires_extras)}]'"
        )
    return cls()

def available_detectors() -> list[str]:
    return [name for name, cls in _REGISTRY.items() if cls.is_available()]
```

The GUI calls `/api/capabilities` → `available_detectors()` and disables
unavailable ones with a tooltip explaining the missing extra.

### EnergyDetector

Direct port of `detect_segments_raw` from
`stim_pipeline/segment_recording.py`. RMS-based silence detection, median
filter, fill short gaps, drop short bursts. No external dependencies — this
is the always-available fallback.

### WebRTCDetector

Direct port of `detect_segments_vad`. Frame-based VAD (10/20/30 ms frames),
smoothing, energy-envelope boundary refinement. Optional via
`clicketysplit[webrtc]`.

### SileroDetector

Direct port of `detect_segments_silero`. ONNX Silero VAD, runs at 16 kHz,
boundaries refined against the **original-rate** energy envelope (don't
discard the resolution of the source audio when refining). Optional via
`clicketysplit[silero]`.

**Lazy load** the ONNX model in a module-level singleton (current code already
does this) — first detection is slow, subsequent ones reuse the model.

### Boundary refinement (`detection/refinement.py`)

Shared by all VAD backends. Takes a coarse start/end timestamp from the
detector and walks the energy envelope outward (for end) or backward (for
start) until the energy drops below a per-segment threshold. This gives
sub-frame precision regardless of which VAD produced the proposal.

Port `_refine_boundary` and `compute_energy_envelope` as-is from the current
code; they're already well-isolated.

### Labeling (`detection/labeling.py`)

Labeling has two phases that share an algorithm:

1. **At detection time** — produce tentative `assigned_name` values for every
   word-typed segment, based on the condition's `presentation_order` and
   `expected_reps_per_stimulus`. This is what gets written to
   `proposed_segments.json`.
2. **At review time** — when the user edits a token's label, every
   *downstream* tentative label is recomputed forward from that edit. The
   user's edit becomes an **anchor**. Anchors and downstream tentatives are
   stored together in `reviewed_segments.json`; the difference is just a
   boolean flag.

Both phases use the same forward-walk algorithm, just with different starting
anchor sets. Detection-time starts with an implicit anchor at index 0; the
review UI adds more anchors as the user edits.

#### Segment typing (always applied)

```python
def classify_segments(
    segments: list[ProposedSegment],
    *,
    min_word_duration_ms: int = 250,
    max_word_duration_ms: int = 1400,
    drop_intro_block: bool = False,
) -> list[LabeledSegment]:
    """
    Type-classify every segment as:
      - "word"        — duration in [min_word_duration_ms, max_word_duration_ms]
      - "short_noise" — shorter than min_word_duration_ms
      - "crosstalk"   — longer than max_word_duration_ms
      - "intro"       — only if drop_intro_block=True AND the initial-block
                        heuristic matched
    Leaves `assigned_name=""` for all segments. Use `auto_label()` to fill
    word-typed segments with tentative names.
    """
```

#### Forward-walk labeling

```python
@dataclass
class LabelAnchor:
    """An anchor: 'word-typed-segment index i gets exactly this label.'"""
    word_index: int        # zero-indexed position among WORD-TYPED segments
    label: str             # must be a member of stimulus_list
    source: Literal["initial", "user"]   # for telemetry/UI

def auto_label(
    segments: list[LabeledSegment],
    stimulus_list: list[str],
    *,
    presentation_order: Literal["random", "cycled", "blocked"],
    expected_reps_per_stimulus: int = 3,
    anchors: list[LabelAnchor] | None = None,
) -> list[LabeledSegment]:
    """
    Apply tentative auto-labels to word-typed segments using a forward-walk
    from anchors. Non-word segments are untouched.

    - presentation_order="random": no-op. assigned_name stays "" for every
      tentative segment. User-anchored segments keep their anchored label.

    - presentation_order="cycled" (A B C A B C ...):
      Starting from the earliest anchor (or an implicit one: word_index=0
      with label=stimulus_list[0]), walk forward stride 1: each subsequent
      word-typed segment gets the NEXT stimulus in cyclic order. When a
      later user anchor is encountered, the cursor jumps to that anchor's
      label and continues stride-1 from there. Anchors override the
      walked-forward value at their own index.

    - presentation_order="blocked" (A A A B B B C C C ...):
      Same forward walk, but the cursor only advances to the NEXT stimulus
      after `expected_reps_per_stimulus` consecutive tokens have been
      assigned the current stimulus. User anchors reset the rep-count to 1
      and override the current stimulus; subsequent tokens stay on the new
      stimulus until the count again reaches K (or another anchor appears).

    Each labeled segment carries a `label_source` field:
      - "anchor" — came directly from an anchor (user-pinned or initial)
      - "auto"   — walked forward from a preceding anchor
      - ""       — segment is not word-typed, or strategy is random with no
                   user anchor on this segment
    """
```

#### Worked example (blocked, K=3)

```
stimulus_list = ["apple", "banana", "cherry"]
word-typed segments: [s0, s1, s2, s3, s4, s5, s6, s7, s8]   # 9 segments

# Phase 1: detection-time, only implicit anchor at index 0
anchors = [LabelAnchor(0, "apple", "initial")]
→ labels: [apple, apple, apple,    banana, banana, banana,    cherry, cherry, cherry]

# Phase 2: user listens to s4 and realizes the speaker started "cherry" early.
# User edits s4's label from "banana" to "cherry". The UI creates an anchor:
anchors = [LabelAnchor(0, "apple", "initial"), LabelAnchor(4, "cherry", "user")]
→ recomputed labels: [apple, apple, apple, apple, cherry, cherry, cherry, ???, ???]
#                    ^^^^^ K=3 from index 0    ^^^^^^^ K=3 from index 4
# After cherry exhausts its 3 reps, advance to next stimulus in list:
# but cherry is the LAST stimulus and "blocked" wraps around back to apple.
# (Or stops if expected_total_words is known and reached — see notes below.)
→ final labels: [apple, apple, apple, apple, cherry, cherry, cherry, apple, apple]

# Phase 3: user looks at s7-s8, realizes those should be unlabeled because
# the speaker stopped here. User explicitly clears s7's label.
# Clearing creates an "" anchor that breaks the forward walk:
anchors = [..., LabelAnchor(4, "cherry", "user"), LabelAnchor(7, "", "user")]
→ s7 and s8 stay as ""
```

#### Worked example (cycled)

```
stimulus_list = ["apple", "banana", "cherry"]
word-typed segments: [s0..s7]   # 8 segments

# Phase 1: implicit anchor at index 0
→ labels: [apple, banana, cherry, apple, banana, cherry, apple, banana]

# Phase 2: user notices that the speaker skipped "banana" at s1.
# User edits s1's label from "banana" to "cherry".
anchors = [LabelAnchor(0, "apple", "initial"), LabelAnchor(1, "cherry", "user")]
# From the anchor at s1=cherry, cycle continues:
→ recomputed: [apple, cherry, apple, banana, cherry, apple, banana, cherry]
```

#### Notes on the algorithm

- Anchors are stored by **word-segment index** (position among word-typed
  segments), not by absolute segment index. This way, marking a segment as
  short_noise/crosstalk during review doesn't shift downstream anchors.
- An anchor with `label=""` explicitly tells the walk "stop labeling from
  here" — used when the user knows the recording trailed off.
- For `blocked`, the walk wraps back to the start of the stimulus list once
  it reaches the end. This handles the "speaker did an extra rep" case
  gracefully.
- The detection pipeline produces phase-1 anchors only (`source="initial"`)
  and writes the resulting labels to `proposed_segments.json`. All re-anchor
  recomputation happens in the frontend during review, with the algorithm
  ported to TypeScript. The Python and TS implementations share a
  test-vector file (`tests/labeling_test_vectors.json`) so they stay in sync.

#### Function signatures

```python
@dataclass
class LabeledSegment(ProposedSegment):
    segment_type: Literal["word", "short_noise", "crosstalk", "intro"]
    assigned_name: str = ""
    label_source: Literal["", "auto", "anchor"] = ""
    status: Literal["pending", "accepted", "rejected", "intro"] = "pending"
    token_index: int = 0     # filled at export
    cluster_size: int = 0    # filled at export
```

The frontend persists user anchors as a flat list inside the segments JSON
(see schema below) AND projects them onto `assigned_name` + `label_source`
on each segment. This redundancy is intentional: anchors are the source of
truth for re-running the algorithm; the per-segment projection is what
downstream code (export, display) reads.

## Noise reduction (`denoise.py`)

Port `reduce_background_noise` 1:1. Optional dependency on `noisereduce`.
Behavior:

- Estimate noise profile from quietest 20% of frames.
- Apply spectral gating.
- Write the denoised audio to `output/{speaker}/{condition}/denoised.wav` for
  the frontend to load (the review UI plays denoised audio so the user hears
  what gets exported).
- Source-of-truth segments are **time-aligned to the input audio**, which
  after concatenation may itself include silence padding — store the
  audio offset of each per-file boundary in the segments JSON.

## Detection pipeline orchestration (`detection/pipeline.py`)

The piece that ties it all together. Given a config and a speaker×condition,
produces the `proposed_segments.json`:

```python
def detect_for_condition(
    config: ExperimentConfig,
    speaker_id: str,
    condition_name: str,
) -> ProposalResult:
    """
    1. Resolve audio file paths for this speaker×condition.
    2. Load and concatenate.
    3. Optionally denoise.
    4. Run the configured detector.
    5. Label the segments.
    6. Write proposed_segments.json, denoised.wav (if denoised), overview.png.
    7. Return paths + counts for the API to forward to the frontend.
    """
```

This function is what the Flask `/api/detect` route calls — no more
mutating module globals, no more `app.test_request_context` shenanigans for
"detect all." For "detect all" the route just iterates conditions and calls
this function once per condition.

### `proposed_segments.json` schema

```json
{
  "schema_version": 1,
  "speaker_id": "speaker_01",
  "condition": "condition_a",
  "source_files": [
    { "path": "recordings/speaker_01/condition_a/take1.wav",
      "duration_sec": 42.31, "offset_sec": 0.0 },
    { "path": "recordings/speaker_01/condition_a/take2.wav",
      "duration_sec": 38.17, "offset_sec": 42.81 }
  ],
  "denoised_audio": "output/speaker_01/condition_a/denoised.wav",
  "audio_duration_sec": 80.98,
  "sample_rate": 44100,
  "detector": "silero",
  "detector_params": { "vad_threshold": 0.5, "...": "..." },
  "stimulus_list": ["apple", "banana", "cherry"],
  "presentation_order": "blocked",
  "expected_reps_per_stimulus": 3,
  "label_anchors": [
    { "word_index": 0, "label": "apple", "source": "initial" }
  ],
  "segments": [
    {
      "start": 1.23, "end": 1.78, "duration_ms": 550,
      "segment_type": "word",
      "assigned_name": "apple",
      "label_source": "anchor",
      "status": "pending",
      "token_index": 0,
      "cluster_size": 0
    },
    {
      "start": 2.41, "end": 2.96, "duration_ms": 550,
      "segment_type": "word",
      "assigned_name": "apple",
      "label_source": "auto",
      "status": "pending",
      "token_index": 0,
      "cluster_size": 0
    },
    ...
  ]
}
```

All `path`s here are relative to the experiment root (the directory holding
`clicketysplit.json`). Loading code resolves them against the config's dir
so moving the experiment to another machine just works.

## What gets dropped from today's code

- The intro-block heuristic stays in the codebase but defaults **off**.
  Documented as "drop_intro_block: useful if your recordings start with a
  long setup utterance before the stimuli."
- Crosstalk flagging stays but is renamed to "long-segment flagging" in the
  GUI and docs.
- The `WORD_DUR_MIN_MS = 500` global is gone; the per-experiment config
  default is **250 ms** (broader, more general — your specific experiment
  bumps it up).
- The hardcoded "filename stem = assigned_name" mapping from
  `setup_experiment.py` is gone. The tool only knows: stimulus list →
  candidate labels. The user's stimulus list contains whatever they want the
  output filenames to be.
- Auto-labeling is no longer the default and is no longer a one-shot
  detection-time pass. The old `gap_cluster` heuristic is replaced by an
  anchor-and-walk-forward algorithm parameterized by `presentation_order`
  (`random` | `cycled` | `blocked`). User edits during review become
  anchors that re-compute downstream tentative labels — this makes the
  tool resilient to speakers who produced more or fewer tokens than the
  script called for, which silently broke the original totalrecal logic.
