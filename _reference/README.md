# `_reference/` — legacy code being refactored

**Read-only reference.** Subagents read these files to understand what's
being ported into `src/clicketysplit/`. Nothing in this directory is part of
the published clicketysplit artifact — like `_design/`, this is temporary
scaffolding that will be deleted before v0.1.0 ships.

## Source

Copied verbatim from `~/dev/totalrecal/stim_pipeline/` at commit
[totalrecal@3783026](https://github.com/daphneweiss/totalrecal/commit/3783026).
That repo remains the source of truth; if you need a newer version, re-copy
from there.

## Layout

```
_reference/
└── legacy_pipeline/
    ├── app.py                  # 528 lines — Flask backend
    ├── segment_recording.py    # 1475 lines — detection + export engine
    ├── setup_experiment.py     # 268 lines — totalrecal-specific CSV→stimlist generator
    ├── index.html              # 1699 lines — vanilla-JS review UI
    └── ORIGINAL_README.md      # the totalrecal user-facing README
```

## Where each file ends up in clicketysplit

| Legacy file | Becomes |
|---|---|
| `app.py` | `src/clicketysplit/server/app.py` (factory) + `routes.py` (endpoints), restructured to remove module-global state mutation. See [_design/04_BACKEND_API.md](../_design/04_BACKEND_API.md). |
| `segment_recording.py` | Split across: `audio_io.py`, `denoise.py`, `detection/{base,silero,webrtc,energy,refinement,labeling,pipeline}.py`, `export/{tokens,manifest,csv,textgrid}.py`. See [_design/03_AUDIO_AND_DETECTION.md](../_design/03_AUDIO_AND_DETECTION.md) and [_design/05_EXPORT.md](../_design/05_EXPORT.md). |
| `index.html` | `frontend/src/` (Svelte SPA). Not a line-by-line port — the state model is being redone. See [_design/06_FRONTEND.md](../_design/06_FRONTEND.md). |
| `setup_experiment.py` | **Not ported.** Experiment-specific. Kept here so subagents can see the totalrecal naming paradigm they're explicitly NOT carrying forward. |
| `ORIGINAL_README.md` | **Not ported.** Useful for the workflow language familiar to the lab; the new `README.md` and docs are rewritten from scratch. |

## Files deliberately NOT copied

- `review_tool.html` (771 lines) — explicitly dropped per
  [_design/00_OVERVIEW.md](../_design/00_OVERVIEW.md#key-decisions-settled-with-the-user).
- `og_dl/` — older internal backup.
- `all_words_concatenated.csv` — experiment-specific stimulus data.
- `__pycache__/`.

## Rules for subagents

1. **Read-only.** Never edit anything under `_reference/`. If you need to
   "fix" something, fix the port in `src/clicketysplit/`, not the legacy
   file.
2. **Port what's relevant, drop what isn't.** The legacy code embeds
   totalrecal-specific assumptions (s/ʃ paradigm, `crit_s`/`fill_word`
   suffixes, hardcoded paths, gap-cluster as default). The design docs spell
   out what the new code does differently — when there's a conflict between
   "what the legacy code does" and "what the design says," **the design
   wins**.
3. **Cite line ranges.** When porting a chunk, reference the source in your
   commit message: e.g. "Port `_refine_boundary` from
   `_reference/legacy_pipeline/segment_recording.py:415-462`."
