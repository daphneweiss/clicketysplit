# clicketysplit — design docs

Design specs for refactoring [totalrecal/stim_pipeline/](../stim_pipeline/)
into a standalone open-source tool. Once the design here is reviewed and
locked, a fresh repo named `clicketysplit` is bootstrapped and subagents
implement the modules one doc at a time.

## Read order

0. **[CONTRACT_NOTES.md](CONTRACT_NOTES.md) — read first.** Cross-cutting
   contracts (statefulness, filesystem layout, status terminology, build
   artifacts, doc paths in worktrees). If a later doc disagrees with
   CONTRACT_NOTES, CONTRACT_NOTES wins.
1. [00_OVERVIEW.md](00_OVERVIEW.md) — goals, audience, all settled decisions, success criteria
2. [01_PACKAGING.md](01_PACKAGING.md) — repo layout, `pyproject.toml`, CLI entrypoint, CI
3. [02_CONFIG_AND_DISCOVERY.md](02_CONFIG_AND_DISCOVERY.md) — `clicketysplit.json` schema, recordings discovery
4. [03_AUDIO_AND_DETECTION.md](03_AUDIO_AND_DETECTION.md) — audio I/O, three VAD backends, refinement, labeling
5. [04_BACKEND_API.md](04_BACKEND_API.md) — Flask routes, request/response shapes, statelessness
6. [05_EXPORT.md](05_EXPORT.md) — token slicing, manifest, CSV, optional TextGrid
7. [06_FRONTEND.md](06_FRONTEND.md) — Svelte SPA, central store, review UI
8. [07_TESTING_AND_DOCS.md](07_TESTING_AND_DOCS.md) — pytest suite, demo data, mkdocs site

## Subagent task map

Each implementation subagent should be briefed with **00 + 01** plus one
focused doc. Approximate dependency order:

| # | Subagent task | Reads | Depends on |
|---|---|---|---|
| 1 | Bootstrap repo, pyproject, CI skeleton, src/ layout, MIT license | 00, 01 | — |
| 2 | Implement config schema + load/save (`config.py`) | 00, 01, 02 | 1 |
| 3 | Implement audio I/O (`audio_io.py`) | 00, 01, 03 | 1 |
| 4 | Implement detection backends + refinement + labeling | 00, 01, 03 | 3 |
| 5 | Implement denoise + detection pipeline orchestrator | 00, 01, 03 | 3, 4 |
| 6 | Implement export engine (WAV, manifest, CSV, TextGrid) | 00, 01, 05 | 3 |
| 7 | Implement Flask app factory + all routes + error handling | 00, 01, 02, 04 | 2, 5, 6 |
| 8 | Implement discovery + setup-wizard route + session JSON | 00, 01, 02, 04 | 2, 7 |
| 9 | Scaffold Svelte frontend; implement Setup step | 00, 01, 06 | 7, 8 |
| 10 | Implement Review step (waveform widget, keybindings, autosave) | 00, 01, 06 | 9 |
| 11 | Implement Select + Export steps | 00, 01, 06 | 10 |
| 12 | Wire frontend build into the wheel; verify `clicketysplit serve` from clean install | 00, 01, 06 | 11 |
| 13 | Write pytest suite + smoke tests + fixtures | 00, 01, 07 | 6, 7 |
| 14 | Author demo data + `clicketysplit demo` command | 00, 01, 07 | 12 |
| 15 | Write mkdocs site content and configure GH Pages deploy | 00, 01, 07 | 12 |

### Parallel-dispatch waves

Compute the levels from the Depends-on column, not from "they're in the
backend section so they're parallel" — earlier drafts of this file made
exactly that mistake.

- **Wave 1**: task **1** only.
- **Wave 2** (after 1 lands): **2** and **3** in parallel.
- **Wave 3** (after 3 lands): **4** and **6** in parallel.
- **Wave 4** (after 4 lands): **5**.
- **Wave 5** (after 2 + 5 + 6 land): **7**.
- **Wave 6** (after 2 + 7 land): **8**.
- **Wave 7** (after 7 + 8 land): **9**.
- **Wave 8** (after 9): **10**.
- **Wave 9** (after 10): **11**.
- **Wave 10** (after 11): **12**.
- **Wave 11** (after 6 + 7 land): **13** can run early in parallel with
  later UI waves; **14** and **15** need 12.

Tasks 4 and 6 both depend on 3, not on each other, so they can run in
parallel — but neither can start until 3 has merged.

## Source-of-truth code being refactored

| Old | New |
|---|---|
| `totalrecal/stim_pipeline/app.py` | `src/clicketysplit/server/app.py` + `routes.py` |
| `totalrecal/stim_pipeline/segment_recording.py` | Split across `detection/`, `denoise.py`, `export/`, `audio_io.py` |
| `totalrecal/stim_pipeline/index.html` | `frontend/src/` (Svelte) |
| `totalrecal/stim_pipeline/setup_experiment.py` | **Not ported.** Stays in totalrecal as an experiment-specific helper. |
| `totalrecal/stim_pipeline/review_tool.html` | **Dropped** (legacy). |

## What is NOT in scope

These live elsewhere or stay in totalrecal:

- Fricative continuum generation (`totalrecal/make_ambig_tokens/`)
- Agreement / inter-rater pipeline (`totalrecal/agreement_pipeline/`)
- Norming / Gorilla spreadsheet generators (`totalrecal/scripts/*`)
- Experiment-specific stim-list authoring (`setup_experiment.py`)

clicketysplit is *only* the segmentation tool.
