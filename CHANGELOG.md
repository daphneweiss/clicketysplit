# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-08-06

First public release. Everything below is part of it.

### Added
- Initial repo scaffolding: `src/` layout, hatchling + hatch-vcs build, MIT license, mkdocs-material docs skeleton.
- `pyproject.toml` with default deps (flask, numpy, scipy, soundfile, pydantic, matplotlib) and optional extras (`silero`, `webrtc`, `denoise`, `mp3`, `praat`, `all`, `dev`).
- CI workflows: `ci.yml` (pytest + ruff + mypy across Python 3.10/3.11/3.12, frontend build, wheel-bundles-static smoke check), `docs.yml`, `release.yml` (npm build → wheel → PyPI via OIDC).
- Engine modules: `config` (pydantic schema + two-phase validation), `audio_io`, `discovery`, `denoise`, `session`.
- Detection package: registry with lazy optional-backend loading; `energy`, `webrtc`, `silero` detectors; energy-envelope boundary `refinement`; `labeling` with anchor-and-walk-forward auto-labeling; full detection `pipeline`.
- Export package: WAV `tokens`, token `manifest.json`, `tokens.csv`, optional Praat `textgrid`.
- Flask server: app factory, setup-wizard backend routes, `/api/health`, `/api/version`, `/api/capabilities`, config load/save, recordings discovery, detection, review, and export endpoints.
- CLI: `clicketysplit serve` and `clicketysplit demo` entry points, `--version` flag.
- Svelte 5 frontend with TypeScript: 4-step setup wizard (paths → conditions → detection → review), reactive store, typed API client, vite build pipeline writing into `src/clicketysplit/server/static/`.
- pytest infrastructure with markers for optional-extra gating and parity tests against the legacy lab pipeline.
- Frontend bundle is force-included in the wheel (overrides VCS-ignore filtering on the gitignored `server/static/` build artifact).
- Review UI: waveform + spectrogram panels, drag boundary adjustment, add-token mode, label autocomplete, token/context playback, zoom/pan, debounced autosave.
- Review label cursor ported from the lab-proven original: predicted-stimulus prefill, relabel-ahead jumps, extra repetitions and rejects never consume expected labels.
- Blocked auto-labeling by gap clustering (the original tool's algorithm), with adaptive crosstalk threshold and k-chunk fallback; shared Python/TypeScript test vectors carry real recording timings.
- Bundled demo: two clips of real experiment speech (real words + pseudowords, the author's own voice) as a two-condition experiment.
- Config-overwrite guard: saving over an existing `clicketysplit.json` the server didn't load requires explicit confirmation.

### Changed
- Silero VAD is a core dependency rather than an extra. It is the detector the boundary refinement and duration thresholds were tuned against; the energy detector over-segments real speech badly enough that shipping it as the default misrepresented the tool. `energy` stays registered for the test suite and as a config-level escape hatch but is no longer offered in the setup UI.
