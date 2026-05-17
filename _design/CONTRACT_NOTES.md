# Contract notes — read this BEFORE any other design doc

These are the cross-cutting contracts the rest of the design depends on.
An external review caught earlier-draft drift between docs; everything
here is the authoritative version, and every other doc has been updated
to match. If a later doc disagrees with this page, **this page wins** —
flag the disagreement so we can correct the offending doc.

## C1 — Backend statefulness model

clicketysplit's Flask server is **a single-user desktop tool that happens
to speak HTTP**, not a multi-tenant API.

- **One active experiment per server process.** The currently active
  experiment's absolute path lives in `app.config["experiment_path"]`.
- The only route that mutates it is `POST /api/config/load`. Every other
  route that needs experiment data reads it from `app.config`.
- All durable state lives **on disk** inside the active experiment's
  directory. Process memory holds only request-scoped data and a small
  `lru_cache(maxsize=1)` of the active `ExperimentConfig` keyed by the
  config-file mtime.
- Two browser tabs against the same server pointed at different
  experiments is **unsupported**. The tab that last hit `/api/config/load`
  wins. The frontend surfaces the active path in the status bar so the
  collision is visible.
- "Stateless" appears nowhere in routing logic. The earlier "REST-stateless"
  framing is retired; do not reintroduce it.

## C2 — Filesystem layout, single source of truth

Inside an experiment directory:

```
<experiment-root>/
├── clicketysplit.json
├── <recordings_root>/                  # e.g. recordings/
├── <stimulus_lists_root>/              # e.g. stimulus_lists/
└── <output_root>/                      # e.g. output/
    ├── .session.json                   # wizard UI state, PER-EXPERIMENT
    ├── .session.autosave.json          # ditto, written by autosave timer
    └── <speaker>/<condition>/
        ├── proposed_segments.json
        ├── reviewed_segments.json      (after first review save)
        ├── denoised.wav                (if config.detection.denoise=true)
        ├── overview.png                (detection-time plot)
        ├── <condition>.TextGrid        (if config.export.produce_textgrid=true)
        └── tokens/                     (after first export)
            ├── <speaker>_<word>-<N>.wav
            ├── token_manifest.json
            └── tokens.csv
```

Specifically:

- **`.session.json` is per-EXPERIMENT**, at `<output_root>/.session.json`.
  It holds wizard UI state (active step, active speaker×condition, current
  token index, zoom). There is no per-condition session file.
- **TextGrid sits at the condition root**, sibling to `proposed_segments.json`
  and the `tokens/` directory. Not inside `tokens/`. TextGrids describe the
  source audio, not the slices.

## C3 — Stimulus list is required; presentation_order drives auto-labeling

Per condition:

- `stimulus_list` is **required** (string, config-relative path to a
  `.txt`). An empty file is rejected at load time.
- `presentation_order ∈ {"random", "cycled", "blocked"}`, default `"random"`.
- `expected_reps_per_stimulus` (int, default 3) — only meaningful for
  `"blocked"`. A hint, not a constraint.

`auto_label_strategy` as a separate config field **no longer exists**. The
labeling algorithm is derived per condition from `presentation_order`
plus user anchors in the segments JSON. See
[03_AUDIO_AND_DETECTION.md](03_AUDIO_AND_DETECTION.md) for the algorithm.

## C4 — Segment-status terminology

Every segment carries a `status` field:
`"pending" | "accepted" | "rejected" | "intro"`.

When the docs say "accepted" they always mean `status == "accepted"`. There
is no separate boolean `accepted` field. Export's default-selection rule:
**word-typed AND `status == "accepted"` AND non-empty `assigned_name`**.

## C5 — Config validation is two-phase

Pydantic validators run at `model_validate_json` time, **before**
`_config_dir` is set. Filesystem checks (does this stimulus_list exist?)
can't run as pydantic validators — they'd see `_config_dir is None` and
no-op forever. Instead:

```python
def load_config(path: Path) -> ExperimentConfig:
    cfg = ExperimentConfig.model_validate_json(path.read_text())   # Phase 1
    cfg._config_dir = path.parent.resolve()                        # Phase 2
    cfg.validate_disk_refs()                                       # Phase 3
    return cfg
```

`validate_disk_refs` is a regular method, not a `@model_validator`. Same
pattern for any other "field references something on disk" check.

## C6 — Folder picking is a text input, not a browser folder dialog

The browser cannot reliably hand Flask an absolute filesystem path. The
Setup Wizard collects the recordings root via a **text field** (with
sensible pre-fills). A "Browse" button using the File System Access API
may opportunistically help when the browser supports it, but the text
field is the contract. Do not design any flow that depends on a native
folder dialog returning an abs path.

## C7 — Frontend build artifacts are not committed; package-lock IS

In `frontend/`:

- `package.json` — committed.
- `package-lock.json` — **committed** (npm ci needs it).
- `node_modules/` — gitignored.
- `dist/` — gitignored.
- `src/clicketysplit/server/static/` (the build output bundled into the
  wheel) — **gitignored**. The release workflow rebuilds it from `frontend/`.

Task 1 (bootstrap) MUST run `npm install` inside `frontend/` once and
commit the resulting `package-lock.json`.

## C8 — CI install line

Every CI workflow (except `docs.yml`) installs:

```
pip install -e '.[dev,all]'
```

That gets the dev tooling (pytest, ruff, mypy, mkdocs) AND every optional
detection/audio/format backend. Tests gated on a specific extra use
`@pytest.mark.skipif` based on `importlib.util.find_spec`, not on a CI
matrix dimension. `docs.yml` uses `'.[dev]'` (mkdocs only).

## C9 — Where the docs live; how subagents read them

Design docs are at **`/home/daphn/dev/clicketysplit/_design/`** in the main
checkout. They are **gitignored** and therefore **do not appear in git
worktrees**. Subagents using `isolation: "worktree"` must reference the
docs by their absolute path in the main checkout, e.g.:

> `/home/daphn/dev/clicketysplit/_design/04_BACKEND_API.md`

Never use relative paths in subagent briefs, and never assume `_design/`
exists in the working tree the subagent is editing.

## C10 — Overview PNG has its own route

`output/<speaker>/<condition>/overview.png` is served by
**`GET /api/overview/<speaker>/<condition>`**, not by an audio route. The
earlier draft had the URL nested under `/api/audio/working/...`, which
implies an audio MIME type. Fixed.
