# 06 — Frontend (Svelte SPA)

## Why Svelte

Reactive state management is the whole reason for moving off vanilla JS. The
current 1700-line `index.html` has multiple state-desync bug categories
(boundary handle position out of sync with label autocomplete, zoom level
forgetting itself on token switch, autosave fighting with manual save).
Svelte's reactive stores collapse all of this into "render from state, write
to state."

We're picking Svelte over React because:
- Compiled output is smaller (~10–20 KB runtime vs ~40 KB for React).
- The single-file component model (`.svelte` files with `<script>`, markup,
  `<style>` together) reads more like HTML than JSX, which matters for lab
  contributors who don't live in JS.
- No virtual DOM — surgical updates are faster for the canvas-heavy review UI.

We're picking Svelte 5 (with runes) over Svelte 4. Runes (`$state`,
`$derived`, `$effect`) are clearer than the magical reactive-assignment of
Svelte 4 and they're the supported path going forward.

## Stack

- **Svelte 5** with TypeScript
- **Vite** as the dev server and bundler (`npm run build` → static assets)
- **No router library** — a single store-driven step state is enough for our
  four-step wizard
- **No UI library** — hand-rolled minimal CSS. Keeps the install small and
  avoids a styling-library lock-in.
- **vitest** for component/store unit tests (optional in v1; nice to have)

`package.json` (sketch):

```json
{
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "check": "svelte-check"
  },
  "devDependencies": {
    "@sveltejs/vite-plugin-svelte": "^4",
    "svelte": "^5",
    "svelte-check": "^4",
    "typescript": "^5.4",
    "vite": "^5"
  }
}
```

`vite.config.ts`:

```ts
export default defineConfig({
  plugins: [svelte()],
  build: { outDir: '../src/clicketysplit/server/static', emptyOutDir: true },
  server: { proxy: { '/api': 'http://127.0.0.1:5000' } }
});
```

## Directory layout

```
frontend/src/
├── App.svelte                 # top-level shell, step routing
├── lib/
│   ├── api.ts                 # typed wrappers around fetch('/api/...')
│   ├── store.ts               # central runes-based state
│   ├── types.ts               # mirrors backend JSON shapes
│   ├── waveform.ts            # canvas waveform + spectrogram renderer
│   ├── audio.ts               # WebAudio playback engine
│   ├── keybindings.ts         # global hotkey registration
│   └── format.ts              # number/duration formatters
├── components/
│   ├── StepNav.svelte         # 4-step progress bar + jump
│   ├── Toast.svelte           # bottom-of-screen notifications
│   ├── ConditionTabs.svelte
│   └── WaveformView.svelte    # the big interactive widget
└── routes/
    ├── Setup.svelte           # Step 1
    ├── Review.svelte          # Step 2
    ├── Select.svelte          # Step 3
    └── Export.svelte          # Step 4
```

## Central store

A single runes-based store. Components subscribe by importing from this
module; nothing else is a source of truth.

```ts
// frontend/src/lib/store.ts
import type { ExperimentConfig, Segment, DiscoveryResult } from './types';

export const state = $state({
  // Loaded config
  config: null as ExperimentConfig | null,
  configPath: null as string | null,

  // Wizard step
  step: 1 as 1 | 2 | 3 | 4,

  // Per (speaker, condition) review state
  reviewProgress: {} as Record<string, Record<string, ConditionReviewState>>,
  activeSpeakerId: null as string | null,
  activeCondition: null as string | null,

  // Detection capabilities (filled from /api/capabilities at boot)
  capabilities: null as Capabilities | null,

  // Discovery in progress
  discovery: null as DiscoveryResult | null,

  // Toasts
  toasts: [] as Toast[],
});

export interface ConditionReviewState {
  segments: Segment[];
  // Source of truth for tentative labels. Edited via setLabel().
  labelAnchors: LabelAnchor[];
  presentationOrder: 'random' | 'cycled' | 'blocked';
  expectedRepsPerStimulus: number;
  currentTokenIndex: number;
  zoom: { startSec: number; endSec: number };
  dirty: boolean;
}

/**
 * setLabel(segmentIdx, newLabel) mutates segments + labelAnchors atomically:
 *   1. Find the word_index of segments[segmentIdx]
 *   2. Insert/replace a user anchor at that word_index
 *   3. Re-run autoLabel(segments, stimulus_list, presentationOrder, ..., anchors)
 *   4. Project the resulting labels back onto segments[*].assigned_name +
 *      label_source
 *   5. Mark state dirty (triggers autosave)
 *
 * Implementation lives in lib/labeling.ts. Must match
 * clicketysplit.detection.labeling exactly — both pull test vectors from
 * tests/labeling_test_vectors.json.
 */
```

Derived values use `$derived`:

```ts
export const activeCondState = $derived(
  state.activeSpeakerId && state.activeCondition
    ? state.reviewProgress[state.activeSpeakerId]?.[state.activeCondition]
    : null
);

export const currentSegment = $derived(
  activeCondState ? activeCondState.segments[activeCondState.currentTokenIndex] : null
);
```

Effects auto-save:

```ts
$effect(() => {
  // Whenever activeCondState.dirty flips true, debounced-save to backend.
  if (activeCondState?.dirty) scheduleSave(state.activeSpeakerId!, state.activeCondition!);
});
```

## API client

Strongly-typed wrappers in `lib/api.ts`:

```ts
export async function discoverRecordings(root: string): Promise<DiscoveryResult> {
  return jsonPost('/api/discover', { root });
}

export async function detect(speakerId: string, condition: string, force = false) {
  return jsonPost<DetectResponse>('/api/detect', { speaker_id: speakerId, condition, force });
}

export async function loadSegments(speakerId: string, condition: string) {
  return jsonGet<SegmentsFile>(`/api/segments/${speakerId}/${condition}`);
}

export async function saveSegments(speakerId: string, condition: string, payload: SegmentsFile) {
  return jsonPost(`/api/segments/${speakerId}/${condition}`, payload);
}
```

Every wrapper throws on `!response.ok` with a typed error containing
`code` and `message` from the backend envelope; UI catches and toasts.

## Step components

### Setup.svelte

Drives the four sub-steps from [02_CONFIG_AND_DISCOVERY.md](02_CONFIG_AND_DISCOVERY.md):

1. **Pick experiment**: load existing `clicketysplit.json` or start new.
2. **Pick recordings root** → call `/api/discover`.
3. **Edit speakers/conditions**: editable tree of the discovery result.
   Each condition row has:
   - a required stimulus-list picker (file dropdown from
     `stimulus_lists_root`),
   - a `presentation_order` radio (Random / Cycled / Blocked), with a small
     inline explanation of each,
   - an `expected_reps_per_stimulus` number input — only enabled when
     Blocked is selected.
4. **Choose detection parameters**: backend (only available ones enabled),
   thresholds, denoise toggle. Sliders with sensible ranges; show units.
5. Save config → call `/api/detect_all`. Block UI with progress modal.

### Review.svelte

The big one. Per-condition view with the waveform widget. Behavior matches
today's `index.html` review tab but driven by the central store:

- **Condition tabs** at top. Switching tabs persists current state via store
  + autosave, loads new condition's `reviewed_segments.json` or
  `proposed_segments.json`.
- **WaveformView** displays the current token in context, with neighbors
  visible at lower opacity.
- **Keybindings** (from `keybindings.ts`):
  | Key | Action |
  |---|---|
  | Tab | Play current segment |
  | Enter | Accept |
  | R | Reject |
  | S | Skip (next without changing status) |
  | ← / → | Previous/next token |
  | A | Add-token mode (click start, click end) |
  | Esc | Cancel add-token / close modal |
  | Scroll | Zoom anchored at cursor |
  | Shift+drag | Pan |
- **Labeling**: labels are the primary work of the review step. How tokens
  start out depends on the condition's `presentation_order`:
  - `random` — every word-typed token starts with `assigned_name=""`. The
    user labels each one.
  - `cycled` / `blocked` — tokens are pre-filled with **tentative
    auto-labels** from the anchor-and-walk-forward algorithm
    (see [03_AUDIO_AND_DETECTION.md](03_AUDIO_AND_DETECTION.md)). The
    label input still gets focus per token; the user confirms with Enter
    or edits.
  - The label input shows the stimulus list as a fuzzy-match dropdown
    (substring + small-edit-distance). Arrow keys + Enter to pick.
    Free-text labels allowed but rendered with a "not in list" warning
    icon — useful when the user spots a word the experimenter forgot.
  - Empty labels are allowed at review time but block export — the export
    UI surfaces these as "unlabeled, will be skipped" and offers to jump
    the user back to the first unlabeled token.
- **Re-anchoring** (only when `presentation_order` is `cycled` or `blocked`):
  when the user changes a token's `assigned_name`, the store creates a new
  user anchor at that token's word-index and re-runs the forward-walk on
  every *downstream* tentative label. Anchors persist in the segments JSON
  alongside the per-segment labels. Two visual cues:
  - **Anchor badge** on each token row whose label is anchor-sourced (a
    small pin icon). Hovering shows "user-anchored at HH:MM:SS" or
    "initial".
  - **Diff highlight** the first time a token's tentative label changes
    due to a downstream re-walk (fades after 2s). Helps the user see the
    effect of an edit.
- **Tab vs Enter**: Tab plays the segment without leaving the label input;
  Enter both accepts the label and advances. This lets the user iterate
  "type → tab → listen → adjust → enter" without leaving the keyboard.
- **Pinning vs reverting**: any manual edit to `assigned_name` creates an
  anchor. Right-click on a token row → "Clear anchor" removes the user
  anchor and re-derives the label from the forward walk. Useful if the
  user anchored by accident.

### Select.svelte

For each (speaker, condition), show tokens grouped by `assigned_name` (with
an "Unlabeled" group at the top for any blank labels — these can't be
exported until labeled). Each token row has a play button, duration, and a
checkbox. Bulk "select all" / "deselect all" per word. Default selection:
all word-typed tokens with `status == "accepted"` and non-empty `assigned_name`.

### Export.svelte

Read-only summary: per-condition counts of selected tokens, total token
count, estimated output size. Big "Export all" button → `/api/export_all`.
Show a per-condition status list as results stream back.

## Waveform widget (`WaveformView.svelte` + `lib/waveform.ts`)

The single most complex piece of UI. Today's vanilla version mixes
canvas-drawing, mouse-event handling, and state mutation. Split into:

- **`waveform.ts`** is pure: given audio samples, sr, view range, and a list
  of segments, it draws to a passed-in CanvasRenderingContext2D. Stateless.
- **`WaveformView.svelte`** owns the canvas DOM element, the mouse/touch
  event handlers, and the zoom/pan state. On every state change it calls
  `waveform.draw()`. No imperative state lives in here that isn't recreated
  on each render pass.

Audio data flow:
1. On condition load, fetch `/api/audio/working/<speaker>/<condition>` as a
   Blob and `decodeAudioData` into an `AudioBuffer`.
2. Cache the AudioBuffer in the store keyed by `(speaker, condition)` so
   switching back is instant.
3. Pre-compute a downsampled envelope (e.g. 2 px-per-sample at max zoom out)
   once per audio buffer; the renderer uses the envelope when zoomed out
   and the raw samples when zoomed in.

Spectrogram: render lazily — only when the user toggles it on, and only for
the visible window. Use a Web Worker to compute FFTs if performance is a
problem; in v1 keep it on the main thread and only render at low frequency
resolution (128-bin) to keep paints fast.

Boundary handles:
- Two draggable handles (L and R). Hit-test in `WaveformView` mouse handlers,
  update segment in the store, autosave-debounced.
- Click on waveform (not on a handle) moves the **nearer** boundary to the
  click position. This matches the documented current behavior.

Zoom & pan:
- Wheel: factor 1.2 in/out, anchored at cursor X. Clamp to `[20 ms, full
  audio]`.
- Shift+drag: pan, clamped to audio bounds.

## Autosave & dirty state

The store marks a condition `dirty: true` on any segment edit. A debounced
effect (`$effect`, 1s after last edit) calls
`POST /api/segments/<speaker>/<condition>` with the full segments payload.
After successful save the effect clears `dirty`.

A second effect writes the wizard-wide UI state (step, active condition,
current token index, zoom) to `POST /api/session` every 5s if anything
changed.

On boot:
1. `GET /api/capabilities` → fill `state.capabilities`.
2. If the server was launched with `--experiment`, `GET /api/config`. Else
   show the "pick or create experiment" Setup screen.
3. If `GET /api/session` returns non-empty, restore step/active condition.

## Accessibility & sanity

- Every button has a visible label, not just an icon.
- Keyboard works for the entire review flow without a mouse (today's tool
  already does this; preserve).
- Focus management: when entering Review, focus the waveform container so
  keybindings work immediately. Modal dialogs trap focus.
- High-contrast colors for segments (rejected = strikethrough, accepted =
  green check, pending = gray). Don't rely on color alone.

## What we explicitly drop

- The legacy "Select Best" / "finalize" mode from `review_tool.html` —
  selection happens in step 3, no "best vs alternates" distinction.
- Drag-to-resize segments below 50 ms — clamped to a minimum to prevent
  accidental zero-duration tokens.
- The current "intro" highlight color logic — only rendered if
  `config.labeling.drop_intro_block` is true and the segment is typed `intro`.

## Dev workflow for contributors

```bash
# Terminal 1: Flask backend
cd src/  # or wherever the venv is
clicketysplit serve --port 5000

# Terminal 2: Vite dev with hot reload
cd frontend
npm install
npm run dev               # opens http://localhost:5173
```

Vite proxies `/api/*` to the Flask backend. Edits to `.svelte` files
hot-reload without losing wizard state. To produce the bundle that gets
shipped in the wheel: `npm run build` writes to
`../src/clicketysplit/server/static/`.
