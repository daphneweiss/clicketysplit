# Contributing

Thanks for thinking about contributing. clicketysplit is small enough that
one person can hold the whole codebase in their head; we want to keep it that
way.

## Dev setup

You need Python 3.10+ and Node 18+ (for the Svelte frontend).

```
git clone https://github.com/daphneweiss/clicketysplit.git
cd clicketysplit
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,all]'
cd frontend
npm install
cd ..
```

The `[dev,all]` install gives you pytest, ruff, mypy, mkdocs, **and** every
optional detection / audio / format backend. This matches the CI install
line, so what runs locally matches what runs on PRs.

## Running the dev server

You typically run two terminals.

**Terminal 1 — Flask backend:**

```
source .venv/bin/activate
clicketysplit serve --port 5000
```

**Terminal 2 — Vite dev server with hot reload:**

```
cd frontend
npm run dev
```

Open `http://localhost:5173`. Vite proxies `/api/*` to Flask on port 5000.
Edits to `.svelte` files hot-reload without losing wizard state.

To produce the bundle that gets shipped in the wheel:

```
cd frontend
npm run build
```

That writes to `../src/clicketysplit/server/static/`, which the wheel includes.

## Running tests

```
source .venv/bin/activate
pytest
```

Useful flags:

```
pytest -m "not slow"                   # skip slow tests
pytest tests/test_detection_silero.py  # one file
pytest -x                              # stop at first failure
pytest --cov                           # with coverage
```

Tests gated on a specific extra use `@pytest.mark.skipif` keyed on
`importlib.util.find_spec`, so a partial install just skips the tests it
can't run.

## Code style

We use `ruff` for lint + format and `mypy --strict` for type checking. Both
are required in CI.

```
ruff check src tests
ruff format src tests
mypy src/clicketysplit
```

`ruff` is configured in `pyproject.toml` with `line-length = 100` and
`target-version = "py310"`. Run `ruff format` before committing.

## Building the docs locally

```
source .venv/bin/activate
mkdocs serve
```

That serves the docs at `http://127.0.0.1:8000` with live reload. On push to
`main`, the `docs.yml` workflow runs `mkdocs gh-deploy --force` to publish
to GitHub Pages.

## Writing a new detector

clicketysplit's detection registry is plugin-friendly. To add a new VAD
backend:

1. Create `src/clicketysplit/detection/my_detector.py`.
2. Implement the `Detector` protocol from
   `src/clicketysplit/detection/base.py`:
    - A `name: str` class attribute.
    - A `requires_extras: list[str]` class attribute (your pip extra names).
    - A classmethod `is_available(cls) -> bool` that checks whether your
      backend's deps are importable.
    - A `detect(self, audio, sr, *, min_segment_ms, min_silence_ms,
      silence_margin_ms, **backend_specific) -> DetectionResult` method
      returning a `DetectionResult` with a list of `ProposedSegment`s and
      an optional `analysis` dict.
3. Register it in `src/clicketysplit/detection/__init__.py`'s `_REGISTRY`
   dict.
4. If your backend needs new deps, add an extra to `pyproject.toml`:

    ```toml
    [project.optional-dependencies]
    my_backend = ["some-package>=1.0"]
    ```

5. Add a parity test file `tests/test_detection_my_backend.py` mirroring the
   shape of `tests/test_detection_energy.py`. Use `pytest.importorskip` to
   gate it on your extra.

Look at `src/clicketysplit/detection/energy.py` for the simplest reference
implementation (no external deps, straight-line code).

## Pull requests

- One change per PR. If you touch multiple unrelated things, split them.
- Add or update tests for any code change.
- Run `ruff check`, `ruff format`, `mypy`, and `pytest` locally before
  pushing.
- Update `CHANGELOG.md` under `[Unreleased]` with a one-line summary.

## Filing issues

Useful info to include:

- Output of `clicketysplit --version`.
- Output of `python --version` and your OS.
- The error code from any toast or terminal output.
- The relevant slice of `clicketysplit.json` if config is involved.
- A minimal repro: a tiny recording + stimulus list that triggers the bug.
