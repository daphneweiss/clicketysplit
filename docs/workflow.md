# Workflow

clicketysplit is a four-step wizard. You move forward and backward between
steps; in-progress work autosaves to disk so you can close the browser and
pick up where you left off.

The four steps:

1. **Setup** — point the tool at your recordings, define speakers and
   conditions, configure detection.
2. **Review** — confirm word boundaries and label each token.
3. **Select** — pick which tokens to export.
4. **Export** — write WAV tokens, manifest, CSV, and optionally a TextGrid.

## Step 1: Setup

The Setup step writes a `clicketysplit.json` to an experiment directory of
your choosing. If you already have a config, load it from the file picker.
Otherwise the wizard walks you through creating one.

**Pick the recordings root.** Type the absolute path to your recordings
folder in the text field. The browser cannot reliably pick a folder for you;
the text field is the contract. A "Browse" button may pre-fill the path on
supported browsers, but you can always type or paste.

**Confirm speakers and conditions.** The backend scans your recordings root
and proposes a tree:

```
speaker_01
├── condition_a (3 files)
└── condition_b (1 file)
speaker_02
└── condition_a (2 files)
```

Toggle speakers on/off, rename conditions, and assign each condition:

- a **stimulus list** (required) — a `.txt` file with one stimulus per line,
- a **presentation order** — `random`, `cycled`, or `blocked`,
- **expected reps per stimulus** — only used when `presentation_order` is
  `blocked` (default 3).

See [Detection](detection.md) for what each presentation order does to
auto-labeling.

**Configure detection.** Pick a detector (energy / WebRTC / Silero), adjust
the thresholds, and toggle denoising. Unavailable detectors are greyed out
with a hint about which `pip install` line enables them.

**Save and detect.** The wizard writes `clicketysplit.json` and runs
detection on every speaker × condition cell. A progress modal blocks the UI
until detection finishes.

## Step 2: Review

The Review step is the heart of the tool. Per speaker × condition, you see:

- A condition tab bar at the top.
- The full-recording waveform with proposed segments overlaid.
- A token strip listing each segment.
- A label input below the active token.

**Labels.** How a token starts depends on the condition's `presentation_order`:

- `random` — labels start empty. You fuzzy-match against the stimulus list.
- `cycled` — labels are pre-filled with `stimulus_list[0], [1], [2], [0], [1], …`.
- `blocked` — labels are pre-filled in groups of `expected_reps_per_stimulus`:
  `A, A, A, B, B, B, …`.

When you change a label in `cycled` or `blocked`, every *downstream* tentative
label is recomputed. Your edit becomes an **anchor** — the forward walk
continues from there. This makes the tool tolerant of speakers who produced
more or fewer tokens than the script called for.

**Boundary tweaks.** Drag the L or R handle to adjust a boundary. Click the
waveform (not a handle) to move the *nearer* boundary to the click point.
Scroll to zoom anchored at the cursor; `Shift`+drag to pan.

**Status.** Every segment carries a status:

- `pending` (gray) — not yet reviewed.
- `accepted` (green check) — confirmed as a real word token.
- `rejected` (red strikethrough) — not a word, will not be exported.
- `intro` (yellow) — only used when `drop_intro_block` is enabled.

### Keybindings

The review step is keyboard-first. You should rarely need the mouse.

| Key | Action |
|---|---|
| `Tab` | Play the current segment |
| `Enter` | Accept the label and advance |
| `R` | Reject the current segment |
| `S` | Skip — advance without changing status |
| `←` / `→` | Previous / next token |
| `A` | Add-token mode (click start, then click end) |
| `Esc` | Cancel add-token mode or close a modal |
| `Scroll` | Zoom anchored at the cursor |
| `Shift`+drag | Pan the waveform |

**Iterating on one token.** Type a label, press `Tab` to listen, adjust if
needed, press `Enter` to accept and advance. Your hands stay on the keyboard.

**Clearing an anchor.** Right-click a token row and choose "Clear anchor" to
remove a user anchor and re-derive the label from the forward walk. Useful
when you anchored by accident.

**Autosave.** Edits debounce-save after one second. The UI shows a small
"saved" indicator when the write completes.

## Step 3: Select

Tokens are grouped by `assigned_name`:

```
apple   [x] -1   [x] -2   [x] -3
banana  [x] -1   [ ] -2  (you unchecked this one)
cherry  [x] -1
Unlabeled  3 tokens — label these before export
```

Default selection is **every word-typed token with `status == "accepted"` and
a non-empty `assigned_name`**. Uncheck any you don't want exported. Bulk
"select all" / "deselect all" per word.

Tokens under "Unlabeled" can't be exported. Click one to jump back to Review
on that token.

## Step 4: Export

The Export step is mostly a status display: per-condition counts and an
estimated output size. Click **Export all**. For each condition, the backend
writes:

```
output/<speaker>/<condition>/
├── <condition>.TextGrid              (if produce_textgrid is true)
└── tokens/
    ├── <speaker>_<word>-<N>.wav
    ├── ...
    ├── token_manifest.json
    └── tokens.csv
```

See [Exports](exports.md) for the filename convention and the manifest /
CSV schemas.

## Resuming work

clicketysplit saves three kinds of state to disk:

- **Per-condition segments** in `output/<speaker>/<condition>/reviewed_segments.json`.
- **Wizard UI state** (active step, active condition, current token, zoom)
  in `output/.session.json`, written every five seconds when something changes.
- **An autosave snapshot** in `output/.session.autosave.json`.

When you reload the page, the wizard restores your active speaker, condition,
token, and zoom level. If the autosave snapshot is newer than `.session.json`
(e.g. the browser crashed), the wizard offers to restore from it.
