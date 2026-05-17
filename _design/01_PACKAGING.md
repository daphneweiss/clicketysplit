# 01 — Packaging, repo layout, CLI

## Repo layout

```
clicketysplit/
├── pyproject.toml
├── README.md
├── LICENSE                       # MIT
├── CHANGELOG.md
├── .gitignore
├── .github/
│   └── workflows/
│       ├── ci.yml                # pytest + lint on push/PR
│       ├── docs.yml              # mkdocs build → GH Pages on push to main
│       └── release.yml           # build wheel + publish to PyPI on tag
├── src/
│   └── clicketysplit/
│       ├── __init__.py           # exposes version
│       ├── __main__.py           # `python -m clicketysplit` → cli.main()
│       ├── cli.py                # argparse / click; `clicketysplit serve`, `clicketysplit demo`
│       ├── config.py             # config schema (pydantic), load/save/validate
│       ├── discovery.py          # scan a recordings root → speakers/conditions
│       ├── audio_io.py           # load_audio(), to_mono(), resample(), supported formats
│       ├── detection/
│       │   ├── __init__.py       # registry + get_detector(name)
│       │   ├── base.py           # Detector ABC + SegmentProposal dataclass
│       │   ├── silero.py
│       │   ├── webrtc.py
│       │   ├── energy.py
│       │   └── refinement.py     # energy-envelope boundary refinement, smoothing
│       ├── denoise.py            # optional noisereduce wrapper
│       ├── export/
│       │   ├── __init__.py
│       │   ├── tokens.py         # WAV slicing, fade, naming
│       │   ├── manifest.py       # token_manifest.json
│       │   ├── csv.py            # tokens.csv
│       │   └── textgrid.py       # opt-in Praat TextGrid
│       ├── session.py            # JSON load/save of in-progress review state
│       ├── server/
│       │   ├── __init__.py
│       │   ├── app.py            # Flask app factory create_app(config_path)
│       │   ├── routes.py         # all HTTP routes
│       │   └── static/           # populated at build time from frontend/dist/
│       ├── demo_assets/          # bundled demo recording + stimulus list
│       │   ├── demo_recording.wav
│       │   └── demo_stimuli.txt
│       └── _version.py           # set by hatch-vcs from git tags
├── frontend/                     # Svelte source (not shipped to PyPI)
│   ├── package.json
│   ├── svelte.config.js
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── src/
│   │   ├── App.svelte
│   │   ├── routes/               # one component per wizard step
│   │   ├── lib/
│   │   │   ├── store.ts          # central reactive store
│   │   │   ├── api.ts            # typed wrappers around /api/*
│   │   │   ├── waveform.ts       # canvas/WebGL waveform + spectrogram
│   │   │   └── audio.ts          # WebAudio playback
│   │   └── types/                # shared TS types mirroring backend JSON
│   └── public/
├── tests/
│   ├── conftest.py
│   ├── data/                     # tiny test fixtures (a few seconds of audio)
│   ├── test_audio_io.py
│   ├── test_detection.py
│   ├── test_export.py
│   ├── test_config.py
│   ├── test_session.py
│   └── test_server_smoke.py
└── docs/
    ├── index.md
    ├── install.md
    ├── quickstart.md
    ├── config-schema.md
    ├── detection.md
    ├── exports.md
    ├── troubleshooting.md
    └── contributing.md
```

## `pyproject.toml`

Use **hatchling** as the build backend (lightweight, no `setup.py` plumbing,
handles `src/` layout natively, easy entry points).

```toml
[build-system]
requires = ["hatchling", "hatch-vcs"]
build-backend = "hatchling.build"

[project]
name = "clicketysplit"
dynamic = ["version"]
description = "A browser-based tool for segmenting and exporting word tokens from speech recordings."
readme = "README.md"
license = { file = "LICENSE" }
requires-python = ">=3.10"
authors = [{ name = "Daphne Weiss", email = "daphnerweiss@gmail.com" }]
keywords = ["audio", "segmentation", "phonetics", "speech", "psycholinguistics"]
classifiers = [
  "Development Status :: 3 - Alpha",
  "Environment :: Web Environment",
  "Intended Audience :: Science/Research",
  "License :: OSI Approved :: MIT License",
  "Operating System :: OS Independent",
  "Programming Language :: Python :: 3.10",
  "Programming Language :: Python :: 3.11",
  "Programming Language :: Python :: 3.12",
  "Topic :: Multimedia :: Sound/Audio :: Analysis",
  "Topic :: Scientific/Engineering",
]

dependencies = [
  "flask>=3.0",
  "numpy>=1.24",
  "scipy>=1.10",
  "soundfile>=0.12",       # WAV/FLAC/OGG read+write
  "pydantic>=2.5",         # config schema validation
  "matplotlib>=3.7",       # overview plot for sanity-checks
]

[project.optional-dependencies]
silero  = ["silero-vad>=5.0", "onnxruntime>=1.16"]
webrtc  = ["webrtcvad>=2.0.10"]
denoise = ["noisereduce>=3.0"]
mp3     = ["pydub>=0.25"]           # also requires ffmpeg on PATH (system)
praat   = ["praat-parselmouth>=0.4"]
all = [
  "clicketysplit[silero,webrtc,denoise,mp3,praat]",
]
dev = [
  "pytest>=7.4",
  "pytest-cov",
  "ruff",
  "mypy",
  "mkdocs>=1.5",
  "mkdocs-material>=9.5",
]

[project.scripts]
clicketysplit = "clicketysplit.cli:main"

[project.urls]
Homepage      = "https://github.com/daphneweiss/clicketysplit"
Documentation = "https://daphneweiss.github.io/clicketysplit"
Issues        = "https://github.com/daphneweiss/clicketysplit/issues"

[tool.hatch.version]
source = "vcs"

[tool.hatch.build.hooks.vcs]
version-file = "src/clicketysplit/_version.py"

[tool.hatch.build.targets.wheel]
packages = ["src/clicketysplit"]
# Include the pre-built frontend in the wheel
include = ["src/clicketysplit/server/static/**", "src/clicketysplit/demo_assets/**"]

[tool.ruff]
line-length = 100
target-version = "py310"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"
```

## Why "extras" instead of hard dependencies

Each VAD/format/feature gets its own extra so a minimal install stays small:

- **Default install** (`pip install clicketysplit`) ships with energy-based VAD
  and WAV/FLAC/OGG support — usable out of the box without heavy deps.
- `pip install clicketysplit[silero]` adds the Silero model.
- `pip install clicketysplit[all]` is what most users will want.

The detection registry (see [03_AUDIO_AND_DETECTION.md](03_AUDIO_AND_DETECTION.md))
auto-detects which backends are available; the GUI greys out the rest with a
hint about which extra to install.

## CLI

```
clicketysplit serve [--host HOST] [--port PORT] [--experiment PATH] [--open]
clicketysplit demo  [--port PORT]
clicketysplit --version
```

Implementation:

```python
# src/clicketysplit/cli.py
import argparse, webbrowser, sys
from pathlib import Path
from .server.app import create_app
from .demo_assets import setup_demo_experiment

def main(argv=None):
    parser = argparse.ArgumentParser(prog="clicketysplit")
    parser.add_argument("--version", action="store_true")
    sub = parser.add_subparsers(dest="cmd")

    p_serve = sub.add_parser("serve", help="Launch the segmenter")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=5000)
    p_serve.add_argument("--experiment", type=Path, default=None,
        help="Path to a clicketysplit.json (default: pick one in the GUI)")
    p_serve.add_argument("--open", action="store_true",
        help="Open the browser automatically")

    p_demo = sub.add_parser("demo", help="Run with bundled demo data")
    p_demo.add_argument("--port", type=int, default=5000)

    args = parser.parse_args(argv)

    if args.version:
        from . import __version__
        print(__version__); return 0

    if args.cmd == "demo":
        exp_path = setup_demo_experiment()      # extracts bundled data to a tmp dir
        return _run(host="127.0.0.1", port=args.port, experiment=exp_path, open_browser=True)

    if args.cmd == "serve":
        return _run(host=args.host, port=args.port,
                    experiment=args.experiment, open_browser=args.open)

    parser.print_help()
    return 1

def _run(host, port, experiment, open_browser):
    app = create_app(experiment_path=experiment)
    url = f"http://{host}:{port}"
    print(f"\n  clicketysplit\n  {url}\n")
    if open_browser:
        webbrowser.open(url)
    app.run(host=host, port=port)
    return 0
```

## Frontend build flow

The wheel ships **pre-built** Svelte static assets. Users never run npm.
Contributors do:

```bash
# One-time
cd frontend && npm install

# Dev mode (live reload, proxies /api to Flask)
npm run dev                 # vite at :5173
# in another shell:
clicketysplit serve --port 5000

# Build static assets that the Python wheel will bundle
npm run build               # writes to ../src/clicketysplit/server/static/
```

`vite.config.ts` sets `build.outDir = "../src/clicketysplit/server/static"` and
configures a dev proxy from `/api` → `http://127.0.0.1:5000`.

The CI release workflow runs `npm ci && npm run build` before `python -m build`
so the wheel always contains fresh static assets.

**`package-lock.json` is committed** (under `frontend/`). `npm ci` requires
it — without a committed lockfile, CI fails before it can build anything.
Task 1 (the bootstrap subagent) MUST run `npm install` inside `frontend/`
once after creating `package.json`, then commit the resulting
`frontend/package-lock.json`. `frontend/node_modules/` stays gitignored;
`frontend/dist/` and `src/clicketysplit/server/static/` (the build outputs)
also stay gitignored.

## CI workflows

All workflows install the package with **`pip install -e '.[dev,all]'`** so
both the dev tooling (pytest, ruff, mypy, mkdocs) and every optional
detection/audio/format backend are available. Tests gated on a specific
extra use `@pytest.mark.skipif` based on import availability, not on a
matrix dimension.

- **`ci.yml`**: matrix on Python 3.10/3.11/3.12. Steps: `pip install -e
  '.[dev,all]'`, `ruff check`, `mypy src/clicketysplit`, `pytest --cov`,
  then (on the matrix's chosen Node-having job) `cd frontend && npm ci &&
  npm run build` and run the smoke test that ensures the bundled static
  dir contains an `index.html`.
- **`docs.yml`**: on push to `main`, `pip install -e '.[dev]'` then
  `mkdocs gh-deploy --force`.
- **`release.yml`**: on tag `v*`, build frontend (`npm ci && npm run build`)
  → `python -m build` → publish to PyPI via OIDC trusted publishing (no
  API token in the repo).

## Versioning

Driven by git tags via `hatch-vcs`. Tag `v0.1.0` produces wheel `0.1.0`. The
in-process version comes from the auto-generated `_version.py` which is
written by hatch at build time.
