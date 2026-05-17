# clicketysplit — design overview

## What this is

`clicketysplit` is a refactor of the segmentation pipeline currently living in
`totalrecal/stim_pipeline/` (`app.py`, `segment_recording.py`, `index.html`),
packaged as a standalone open-source tool. The current pipeline grew organically
to support one specific s/ʃ fricative-perception experiment; this rewrite peels
out the experiment-specific assumptions and turns the segmenter into a
general-purpose tool for **extracting single-word tokens from any speech
recording** — whether the speaker produces each stimulus once, many times in
a row, or in randomized order.

## Audience

Speech, phonetics, psycholinguistics, and clinical-speech researchers who:

- Have recordings of speakers producing single-word stimuli — in any order,
  any number of repetitions per word
- Need to extract individual word tokens from those recordings
- Want auto-segmentation proposals they can review and label in a browser,
  not manually segment from scratch in Praat/Audacity

A **stimulus list is always required** — it's the canonical label set for
fuzzy autocomplete in the review UI. What the tool does *not* assume is the
**presentation order**. Each condition declares its order:

- `random` *(default)* — randomized presentation. Labels start blank;
  the user fills them in with fuzzy autocomplete from the stimulus list.
- `cycled` — list-order repeated through (A B C A B C …). The user provides
  the expected pattern; auto-labeling walks forward through the list.
- `blocked` — each stimulus produced K times consecutively before the next
  (A A A B B B C C C …). Auto-labeling walks forward, K tokens per stimulus.

For `cycled` and `blocked`, auto-labels are **tentative** and **re-anchor on
user edits**: when the user corrects a token's label, every subsequent
auto-label is recomputed forward from that token's new label, treating the
expected-reps count K as a hint rather than a hard rule. This makes the
tool tolerant of speakers who produced more or fewer tokens than the
script called for — a constant source of bugs in the original totalrecal
implementation, which used a one-shot label-by-position pass that broke
silently when the count was off.

## Goals

1. **Domain-agnostic.** No hardcoded suffix patterns (no `crit_s`, no `fill_word`),
   no fixed N conditions, no intro-block heuristic baked into core. Labels are
   whatever the user provides in their stimulus list.
2. **Robust to varied input.** Accept WAV/FLAC/OGG natively, MP3/M4A via
   optional ffmpeg. Auto-downmix multi-channel to mono. Tolerate different
   directory layouts.
3. **Easy install.** `pip install clicketysplit` → `clicketysplit serve`.
   No build step for end users. No node, no docker required.
4. **Hackable.** Clean module boundaries so a lab member can swap a detector,
   add an export format, or change a keybinding without spelunking through
   1700-line files.
5. **Safe by default.** No pickle. Localhost-only by default. No global mutable
   state leaking between experiments.

## Non-goals

- Multi-user/concurrent editing. One researcher per running instance.
- Phoneme-level segmentation. Word tokens only.
- Forced alignment. We propose word boundaries from VAD; we don't align to
  transcripts.
- Continuum generation, agreement pipelines, experiment scaffolding (CSV →
  stimulus lists). Those stay in the parent `totalrecal` repo or get their own.

## Key decisions (settled with the user)

| Decision | Choice |
|---|---|
| Scope | Core segmenter only (refactor of `app.py` + `segment_recording.py` + `index.html`). Drop `review_tool.html` (legacy), `setup_experiment.py` (experiment-specific), and the continuum/agreement pipelines. |
| Distribution | pip-installable from PyPI, CLI entrypoint `clicketysplit` |
| Config | GUI-driven setup wizard, persisted as a per-experiment JSON |
| Frontend | Svelte SPA, pre-built static assets shipped inside the wheel |
| Naming/paradigm | Fully generic — no hardcoded experiment structure |
| Audio formats | WAV/FLAC/OGG native (soundfile); MP3/M4A via optional ffmpeg; auto-downmix multi-channel to mono |
| Session storage | JSON only (no pickle) |
| Detection backends | Silero VAD (default), webrtcvad, energy-based — all three selectable in GUI |
| Networking | localhost-only by default; `--host` flag allows LAN binding |
| Exports | WAV tokens + manifest JSON + CSV by default; Praat TextGrid opt-in |
| License | MIT |
| Tests | Pytest unit tests for engine + Flask smoke test |
| Docs | Markdown in `docs/`, hosted via mkdocs-material on GitHub Pages |
| Python | 3.10+ |
| Recordings layout | Flexible — user picks a root, we discover speakers/conditions |
| Demo data | Small ~30s sample + stimulus list bundled in the wheel |
| Repo layout | `src/` layout, pyproject.toml |
| Positioning | Domain-agnostic, framed as "any speech experiment with single-word repetitions" |

## What's wrong with the current code (problems to fix in the refactor)

These are real issues in `totalrecal/stim_pipeline/`, called out so subagents
don't accidentally preserve them:

1. **Globals mutated per-request.** `app.py` patches
   `seg_engine.WORD_DUR_MIN_MS` etc. on every `/api/detect` call. Not
   thread-safe; impossible to test in isolation. → Parameters must be passed
   through, not mutated.
2. **Hardcoded paths.** `RECORDINGS_DIR = PROJECT_ROOT / "recordings"`,
   `EXPERIMENT_DIR = PROJECT_ROOT / "experiment"`. The user can't point the
   tool at an arbitrary recordings folder. → Paths come from the per-experiment
   config.
3. **Pickle sessions.** `pickle.load` on a file from disk is a code-execution
   risk and breaks across Python versions. → JSON only.
4. **`detect_all` reuses `detect` via `app.test_request_context`.** Janky.
   → Pull shared logic into a function; the routes call it directly.
5. **In-memory `session_state` is process-global.** Multiple browser tabs
   collide. → State is per-experiment-on-disk; the backend is stateless.
6. **Audio path in segments JSON is absolute.** Moving the project dir breaks
   exports. → Paths stored relative to the experiment root.
7. **Intro-block heuristic, crosstalk flagging, and `WORD_DUR_*` defaults are
   hardcoded.** These are specific to your sessions. → Move to optional
   per-condition settings; sensible neutral defaults for first-time users.
8. **Stimulus-list autocomplete assumes one list per condition file in
   `experiment/stimulus_lists/`.** → Stimulus lists are referenced by absolute
   or experiment-relative path in the config; no magic directory.
9. **Auto-labeling silently assumes blocked repetitions in fixed order.**
   The current `gap_cluster` strategy assigns names by recording position
   and never adapts when the speaker produced more or fewer tokens than
   expected — labels silently drift wrong from the mis-count onward.
   → Auto-labeling is opt-in per condition via `presentation_order`
   (`random` (default) / `cycled` / `blocked`), AND uses an
   anchor-and-walk-forward algorithm: user edits during review become
   anchors that re-compute every downstream auto-label.
9. **`index.html` is 1700 lines of vanilla JS with hand-managed state.** Many
   review-UI bugs trace back to state desync. → Svelte SPA with a single
   reactive store.
10. **No tests at all.** → pytest suite for the detection/export engine,
    smoke test that boots the app and hits each route.

## Success criteria

1. A new user can `pip install clicketysplit`, run `clicketysplit serve`, point
   it at a recordings folder, and segment + export tokens **without ever
   touching CLI flags**.
2. The tool works whether a recording has 1 token per stimulus or 100 — and
   whether they're presented blocked, cycled, or randomized. The user picks
   `presentation_order` per condition; the tool never guesses.
3. The review UI's fuzzy-match autocomplete makes labeling fast (a few
   keystrokes per token) regardless of which presentation order applies.
4. A lab member can swap in a custom detector by adding one Python file and
   registering it in a plugin entry point — no fork required.
5. The example workflow (demo data → bundled tokens) runs end-to-end with one
   command for smoke-testing.
6. Sessions saved on machine A can be opened on machine B (JSON, no pickle, no
   absolute paths leaking through).
7. mkdocs site is live at `https://<username>.github.io/clicketysplit/` with
   install, quickstart, config schema, and detection internals pages.

## Document map

- [01_PACKAGING.md](01_PACKAGING.md) — repo layout, pyproject.toml, dependencies, CLI entrypoint
- [02_CONFIG_AND_DISCOVERY.md](02_CONFIG_AND_DISCOVERY.md) — `clicketysplit.json` schema, recordings discovery
- [03_AUDIO_AND_DETECTION.md](03_AUDIO_AND_DETECTION.md) — audio I/O, VAD backends, boundary refinement
- [04_BACKEND_API.md](04_BACKEND_API.md) — Flask routes, request/response shapes, statelessness
- [05_EXPORT.md](05_EXPORT.md) — token export, manifest, CSV, TextGrid
- [06_FRONTEND.md](06_FRONTEND.md) — Svelte app, state model, build process
- [07_TESTING_AND_DOCS.md](07_TESTING_AND_DOCS.md) — pytest suite, demo data, mkdocs site

Read 00 then 01–02 before any subagent starts implementing. Implementation
subagents can typically focus on a single later doc.
