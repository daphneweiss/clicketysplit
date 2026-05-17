# 02 — Configuration and recordings discovery

## The big shift from the current code

Today's `app.py` hardcodes `RECORDINGS_DIR = PROJECT_ROOT / "recordings"` and
`EXPERIMENT_DIR = PROJECT_ROOT / "experiment"`. The tool only works if you run
it from inside the totalrecal repo. clicketysplit instead treats every
experiment as a self-describing directory anchored by a single config file:
`clicketysplit.json`.

The user picks an existing config in the GUI or runs the **Setup Wizard** to
create one. After that, every API call references that experiment by its
config path — there is no global "recordings dir."

## Experiment directory layout

```
my_experiment/                           ← experiment root (user chooses)
├── clicketysplit.json                   ← the only required file
├── recordings/                          ← location is configurable
│   ├── speaker_01/
│   │   ├── condition_a/
│   │   │   └── recording.wav
│   │   └── condition_b/
│   │       └── recording.wav
│   └── speaker_02/
│       └── recording.wav                ← single-level layout also OK
├── stimulus_lists/                      ← location is configurable
│   ├── condition_a.txt
│   └── condition_b.txt
└── output/                              ← created on first detection run
    ├── .session.json                    ← wizard UI state, PER-EXPERIMENT
    ├── .session.autosave.json           ← (step, active cond/token, zoom)
    └── speaker_01/
        └── condition_a/
            ├── proposed_segments.json
            ├── reviewed_segments.json   (after review)
            ├── denoised.wav             (if denoise enabled)
            ├── overview.png             (sanity-check plot)
            └── tokens/                  (after export)
                ├── speaker_01_apple-1.wav
                ├── speaker_01_apple-2.wav
                ├── token_manifest.json
                └── tokens.csv
```

Nothing about `crit_s`, `fill_word`, or specific N-of-conditions is encoded
anywhere. The user defines their own condition names; the tool just maps them
to stimulus lists.

## Config schema (`clicketysplit.json`)

Validated with pydantic v2. All paths in the JSON are stored **relative to the
config file's directory** so the whole experiment is portable between machines.

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

### Field-by-field

#### Top-level
- `schema_version` (int, required) — bump if we make breaking changes.
  The loader rejects unknown versions with a clear "this config was written
  by a newer clicketysplit" message.
- `name` (str, optional) — display label, no functional role.
- `recordings_root`, `stimulus_lists_root`, `output_root` (str, required) —
  paths relative to the config file. Resolved with `Path(config_dir, value).resolve()`.

#### `speakers` (list)
- `id` — used in output filenames and on disk paths under `output/`.
- `subdir` — directory under `recordings_root`. Defaults to `id` if omitted.

Speakers are an **explicit list**, not auto-discovered, so users can include
some recordings and exclude others. The Setup Wizard populates this from a
discovery scan but the user can edit.

#### `conditions` (list)
- `name` — used as the subdir name under each speaker's recording folder *and*
  under the output tree.
- `stimulus_list` (**required**) — path (relative to config dir) to a `.txt`
  with one stimulus name per line. Drives fuzzy-match autocomplete in the
  review UI and, if `presentation_order` is `cycled` or `blocked`, also
  drives auto-labeling at detection time. Empty stimulus list files are
  rejected with a validation error.
- `presentation_order`: `"random" | "cycled" | "blocked"`. Default `"random"`.
  Tells detection how stimuli were presented, which determines what (if any)
  auto-labeling is applied. See [03_AUDIO_AND_DETECTION.md](03_AUDIO_AND_DETECTION.md)
  for the algorithm; this section just defines the config field.
  - `"random"` — auto-labeling **off**. Every word-typed segment gets
    `assigned_name=""`. User labels with fuzzy autocomplete in the review UI.
  - `"cycled"` — list-order repeated through (A B C A B C …). Auto-labels
    are walked forward from anchors; user edits during review create new
    anchors and re-compute downstream labels.
  - `"blocked"` — each stimulus produced K times before the next
    (A A A B B B C C C …). Auto-labels are walked forward in groups of size
    `expected_reps_per_stimulus`; user edits create new anchors and
    re-compute downstream labels.
- `expected_reps_per_stimulus` (int, optional, default 3) — only meaningful
  when `presentation_order = "blocked"`. The **hint** for how many tokens
  to assign to each stimulus before advancing in initial auto-labeling.
  Treated as a *hint*, not a constraint: once the user edits a label, the
  hint applies forward from there but earlier groups keep whatever the
  user pinned. Ignored for `"random"` and `"cycled"`.

A speaker may not have every condition recorded (e.g. flat layout: a speaker
has only one recording at `recordings/speaker_X/recording.wav`, no condition
subdir). The discovery logic handles three cases:

1. **`speaker_dir/condition_name/*.wav|.flac|...`** → use those files, multiple
   files concatenated.
2. **`speaker_dir/*.wav`** with no condition subdirs and only one condition
   configured → treat all files as that single condition.
3. **Mismatch** (e.g. condition is configured but no subdir exists) → mark
   that speaker×condition cell as "no recording" in the GUI; user can skip it
   without errors.

#### `detection`
Same parameters as today's globals, but per-experiment (not module globals).
- `backend`: `"silero" | "webrtc" | "energy"` — selected detector. The GUI
  filters to available backends.
- `vad_threshold` — only meaningful for Silero, ignored otherwise (validator
  warns if set for `energy`).
- `min_segment_ms`, `min_silence_ms`, `silence_margin_ms` — apply to all
  backends.
- `denoise` — if `true` and `noisereduce` is installed, write `denoised.wav`
  alongside `proposed_segments.json`, and segment from the denoised audio.

#### `labeling`
- `min_word_duration_ms` / `max_word_duration_ms` — segments outside this
  range are flagged (not deleted) so the user can review and accept/reject.
- `drop_intro_block` — opt-in port of the current intro-detection heuristic.
  Off by default since it's experiment-specific.

Auto-labeling strategy is **not** a global setting — it's derived per
condition from `presentation_order` (see the `conditions` section). This
lets a single experiment mix randomized and blocked conditions.

#### `export`
- `pad_ms`, `fade_ms` — applied when slicing tokens.
- `format`: `"wav" | "flac"` — output container.
- `produce_csv` (default true), `produce_textgrid` (default false) — see
  [05_EXPORT.md](05_EXPORT.md).

### Pydantic implementation sketch

```python
# src/clicketysplit/config.py
from pydantic import BaseModel, Field, model_validator
from pathlib import Path
from typing import Literal

class DetectionConfig(BaseModel):
    backend: Literal["silero", "webrtc", "energy"] = "silero"
    vad_threshold: float = 0.5
    min_segment_ms: int = 150
    min_silence_ms: int = 150
    silence_margin_ms: int = 25
    denoise: bool = True

class LabelingConfig(BaseModel):
    min_word_duration_ms: int = 250
    max_word_duration_ms: int = 1400
    drop_intro_block: bool = False

class ExportConfig(BaseModel):
    pad_ms: int = 20
    fade_ms: int = 3
    format: Literal["wav", "flac"] = "wav"
    produce_csv: bool = True
    produce_textgrid: bool = False

class Speaker(BaseModel):
    id: str
    subdir: str | None = None
    @model_validator(mode="after")
    def fill_subdir(self):
        if self.subdir is None:
            self.subdir = self.id
        return self

class Condition(BaseModel):
    name: str
    stimulus_list: str                     # required, relative to config dir
    presentation_order: Literal["random", "cycled", "blocked"] = "random"
    expected_reps_per_stimulus: int = 3    # only used when presentation_order=="blocked"

class ExperimentConfig(BaseModel):
    schema_version: int = 1
    name: str = ""
    recordings_root: str = "recordings"
    stimulus_lists_root: str = "stimulus_lists"
    output_root: str = "output"
    speakers: list[Speaker] = Field(default_factory=list)
    conditions: list[Condition] = Field(default_factory=list)
    detection: DetectionConfig = Field(default_factory=DetectionConfig)
    labeling: LabelingConfig = Field(default_factory=LabelingConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)

    # Filled in at load time, NOT serialized
    _config_dir: Path | None = None

    def resolve(self, *parts: str) -> Path:
        """Resolve a config-relative path."""
        assert self._config_dir is not None
        return (self._config_dir / Path(*parts)).resolve()

    def validate_disk_refs(self) -> None:
        """
        Filesystem-touching validation: every condition's stimulus_list
        resolves to an existing file under the config dir.

        This is NOT a pydantic model_validator. Pydantic validators run
        during `model_validate_json`, BEFORE `_config_dir` is set; an
        early-return guard there would just no-op forever. Call this
        explicitly from `load_config()` after the dir is set.
        """
        assert self._config_dir is not None
        for c in self.conditions:
            p = self.resolve(c.stimulus_list)
            if not p.is_file():
                raise ValueError(
                    f"Condition '{c.name}': stimulus_list not found at {p}"
                )
```

```python
def load_config(path: Path) -> ExperimentConfig:
    # Phase 1: schema validation (pydantic). No filesystem access yet.
    cfg = ExperimentConfig.model_validate_json(path.read_text())
    if cfg.schema_version > 1:
        raise ValueError(
            f"This clicketysplit.json was written for schema_version="
            f"{cfg.schema_version}; please upgrade clicketysplit."
        )
    # Phase 2: anchor the resolution root.
    cfg._config_dir = path.parent.resolve()
    # Phase 3: filesystem-dependent checks.
    cfg.validate_disk_refs()
    return cfg

def save_config(cfg: ExperimentConfig, path: Path) -> None:
    path.write_text(cfg.model_dump_json(indent=2, exclude={"_config_dir"}))
```

## Discovery

`discovery.py` has one job: given a directory the user just picked, propose
the speakers/conditions/files they'd probably want.

```python
def scan_recordings(root: Path) -> DiscoveryResult:
    """
    Walk `root` and return what we found.

    Returns:
        DiscoveryResult(
            speakers=[
                ScannedSpeaker(
                    id="speaker_01",
                    subdir="speaker_01",
                    conditions=[
                        ScannedCondition("condition_a", n_files=1, files=[...]),
                        ScannedCondition("condition_b", n_files=3, files=[...]),
                    ],
                    flat_files=[],          # files directly under speaker_dir/
                ),
                ...
            ],
            unique_condition_names=["condition_a", "condition_b"],
        )
    """
```

Rules:
- A directory immediately under `root` becomes a speaker if it contains either
  audio files or audio-containing subdirs.
- A directory under a speaker becomes a condition if it contains audio files.
- Audio = any extension `audio_io.SUPPORTED_EXTENSIONS` recognizes.
- Names starting with `.` or `_`, and reserved names (`output`, `tokens`,
  `final`, `alternates`), are skipped.
- The scan is **cheap** — it does not read the audio, only stat'ing files for
  presence/size.

The Setup Wizard takes this result, fills in the `clicketysplit.json` fields,
and lets the user check/uncheck speakers and rename conditions before saving.

## Setup Wizard flow (frontend → backend)

**Folder picking — what the browser can and can't do.** A web app cannot
silently read an absolute path from a native folder dialog. The
File System Access API (Chromium-only) gives a directory handle but not an
absolute path; `<input webkitdirectory>` gives file *names* without absolute
paths. We need an absolute path because the Flask backend reads files
directly from disk. So:

1. **Primary path entry: text input.** The Setup Wizard's first screen has a
   text field for "Recordings root (absolute path)" plus a small "Browse"
   button. The user pastes or types the path. We pre-fill it from:
   - The CLI `--experiment` arg's parent dir, if launched that way; or
   - The directory `output/.last_recordings_root` saved by the previous
     run; or
   - empty (user types it).
2. **"Browse" button (optional, opportunistic).** Uses the File System Access
   API when the browser supports it. The handle gives a directory *name*
   that we POST to a helper endpoint `POST /api/resolve_browse { name }`
   which searches the user's `$HOME` and common locations (Desktop,
   Documents, ~/dev, ~/recordings) for a directory by that name. If exactly
   one match, we autofill the text field; if multiple, we show a chooser;
   if none, we leave the text field for manual entry. Best-effort only —
   the text field is the contract.
3. Frontend calls `POST /api/discover { root: "/abs/path" }`.
4. Backend returns the `DiscoveryResult`.
5. Frontend renders a tree: each speaker has its conditions; the user can
   toggle inclusion, rename conditions, and assign a stimulus list per
   condition (file picker scans `stimulus_lists_root`).
6. Frontend calls `POST /api/config { config_dir, config: {...} }` → backend
   writes `clicketysplit.json`.
7. The wizard transitions to Step 2 (Review).

The wizard always writes a valid config — even with empty `speakers` or
`conditions`, the rest of the app handles "no data yet" gracefully.

## Where the global state used to live

In the current code, `app.py`'s module-level `session_state` dict tracked the
current speaker, conditions, and progress in memory. clicketysplit replaces
this entirely:

- The **backend holds one active experiment** in `app.config["experiment_path"]`
  (see [04_BACKEND_API.md](04_BACKEND_API.md)). All durable data is on disk
  inside that experiment directory; nothing important is kept only in
  process memory.
- **In-progress UI state** (current step, which speaker×condition is active,
  current token index, zoom) lives in `output/.session.json` —
  **per-experiment, not per-condition**. A `output/.session.autosave.json`
  is written every few seconds by the backend's autosave timer. On load,
  if autosave is newer, the frontend offers to restore from it.
- The **frontend Svelte store** is the single source of truth during a
  session; the backend just persists.

This eliminates the "two browser tabs collide" class of bug entirely and
makes the API trivially testable.
