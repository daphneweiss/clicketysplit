# 07 — Testing, demo data, and documentation

## Test strategy

v1 ships with **pytest unit tests for the engine + a Flask smoke test**.
End-to-end UI tests (Playwright) are intentionally out of scope; we revisit
once we have lab users hitting real bugs.

### Layout

```
tests/
├── conftest.py                    # fixtures: tmp experiment dir, demo audio
├── data/
│   ├── tiny_speech.wav            # ~3 sec, 3 words, mono 16 kHz
│   ├── tiny_stereo.wav            # 1 sec stereo for downmix test
│   ├── tiny_speech.mp3            # for mp3-loading test (skipped if no ffmpeg)
│   └── tiny_stimuli.txt
├── test_audio_io.py
├── test_detection_energy.py
├── test_detection_silero.py       # @pytest.mark.skipif(no Silero)
├── test_detection_webrtc.py       # @pytest.mark.skipif(no webrtcvad)
├── test_labeling.py               # segment classification
├── test_auto_label.py             # anchor-and-walk-forward algorithm
├── test_labeling_parity.py        # Python vs shared test vectors
├── labeling_test_vectors.json     # the contract; read by Py + TS tests
├── test_export.py
├── test_config.py
├── test_discovery.py
└── test_server_smoke.py
```

### What each test file covers

**`test_audio_io.py`**
- Round-trip: load → save → load. Sample-accuracy within tolerance.
- Multi-channel WAV gets downmixed to mono (verify shape == 1D).
- Resampling to 16 kHz preserves duration within ±1 sample.
- Unknown extension raises a clear error.
- MP3 load raises clear error if ffmpeg missing; works if present.

**`test_detection_energy.py`**
- Three loud bursts in silence → 3 segments with reasonable boundaries.
- Pure silence → 0 segments.
- Continuous noise above threshold → handled (no error, may detect 1 huge
  segment, that's OK).
- `min_segment_ms` filters short bursts.
- `silence_margin_ms` extends boundaries outward.

**`test_detection_silero.py`** / `test_detection_webrtc.py`
Same shape as energy tests. Use `pytest.importorskip` for the backend.

**`test_labeling.py`** — segment classification
- `drop_intro_block=True` removes intro-typed segments.
- `min_word_duration_ms` typifies short segments as `short_noise`.
- `max_word_duration_ms` typifies long segments as `crosstalk`.

**`test_auto_label.py`** — the anchor-and-walk-forward algorithm. Every
test case lives in `tests/labeling_test_vectors.json` so the TypeScript
implementation runs the same cases.
- `presentation_order="random"`: every word-typed segment gets
  `assigned_name=""`. Adding user anchors only labels the anchored segments;
  no forward walk.
- `presentation_order="cycled"`, no user anchors: labels are
  `stimulus_list[i mod N]` for the i-th word-typed segment.
- `presentation_order="cycled"`, single user anchor at index 3 with label
  `stimulus_list[7]`: indices 0–2 keep cycled-from-0; from index 3 onward
  cycle continues from `stimulus_list[7]` stride 1.
- `presentation_order="blocked"`, K=3, 9 segments: labels are
  `[A,A,A, B,B,B, C,C,C]`.
- `presentation_order="blocked"`, K=3, 9 segments, user anchor at index 4
  setting label to `C`: labels become `[A,A,A,A, C,C,C, A,A]`
  (the K=3 count from the original A group is "carried" up to index 3 — one
  more A — then C starts at the anchor and runs K=3, then wraps back to A).
- `presentation_order="blocked"`, K=3, anchor with empty label at index 7:
  indices 7+ stay `""`.
- Re-running `auto_label` with the same anchors is idempotent.
- Anchors are keyed by **word-index**, not absolute index — adding a
  `short_noise`-typed segment in the middle doesn't shift downstream
  labels.

**`test_labeling_parity.py`** — loads `labeling_test_vectors.json`, runs
each case through `auto_label`, compares to the embedded expected output.
A second copy of this test runs in the frontend test suite (`vitest`)
against the TS implementation. The vectors file is the contract; both
implementations must match it.

**`test_export.py`**
- Export 2 accepted segments → 2 files written with `-1` and `-2` suffixes.
- `selected_tokens` filter only exports the chosen indices.
- Manifest has correct `tokens_per_word`.
- CSV has correct row count and headers.
- Filename slugification: `apple/pear` → `apple_pear`.
- Atomic write: no `.tmp` files survive a successful export.
- TextGrid export (skipped if no parselmouth) produces valid TextGrid (parse it back).

**`test_config.py`**
- Round-trip: write config → load → identical fields.
- Unknown `schema_version` raises clear error.
- Pydantic validation: bad detector name rejected with field path in error.
- Relative-path resolution: paths are interpreted relative to the config file.

**`test_discovery.py`**
- 2-level recordings dir → speakers and conditions discovered correctly.
- Flat speaker dir (audio directly under speaker) → handled as 1-condition.
- `.hidden` dirs and reserved names (`output`, `tokens`) skipped.
- No audio in a dir → that dir is not a speaker.

**`test_server_smoke.py`**
- `create_app()` → app starts.
- `GET /api/version` returns 200 with expected fields.
- `GET /api/capabilities` returns expected keys.
- With a fixture experiment dir:
  - `GET /api/config` returns the loaded config.
  - `GET /api/stimulus_lists` returns the test stim list.
  - `POST /api/detect` for the fixture's tiny audio → 200, segments found.
  - `POST /api/segments/<spk>/<cond>` round-trips through `GET /api/segments/...`.
  - `POST /api/export` writes the expected files to the fixture dir.
- Path-traversal probe: `GET /api/stimulus_lists/../../../etc/passwd` → 400
  with `path_outside_experiment`.

### Fixtures

```python
# tests/conftest.py
@pytest.fixture
def tiny_audio():
    """3 second mono 16 kHz audio with 3 word bursts at known offsets."""
    sr = 16000
    audio = np.zeros(sr * 3, dtype=np.float32)
    for offset_sec in [0.3, 1.2, 2.1]:
        i = int(offset_sec * sr)
        audio[i:i + sr // 5] = 0.5 * np.sin(2 * np.pi * 440 * np.arange(sr // 5) / sr)
    return audio, sr

@pytest.fixture
def fixture_experiment(tmp_path, tiny_audio):
    """A complete fixture experiment dir for server tests."""
    audio, sr = tiny_audio
    exp = tmp_path / "exp"
    (exp / "recordings" / "spk1" / "cond_a").mkdir(parents=True)
    sf.write(exp / "recordings" / "spk1" / "cond_a" / "rec.wav", audio, sr)
    (exp / "stimulus_lists").mkdir()
    (exp / "stimulus_lists" / "cond_a.txt").write_text("apple\nbanana\ncherry\n")
    cfg = make_minimal_config(
        speakers=[{"id": "spk1"}],
        conditions=[{"name": "cond_a", "stimulus_list": "stimulus_lists/cond_a.txt"}],
    )
    (exp / "clicketysplit.json").write_text(json.dumps(cfg))
    return exp
```

### Markers

```toml
[tool.pytest.ini_options]
markers = [
    "needs_silero: requires silero-vad",
    "needs_webrtc: requires webrtcvad",
    "needs_ffmpeg: requires ffmpeg on PATH",
    "needs_praat: requires praat-parselmouth",
    "slow: takes >1s",
]
```

CI runs the matrix on Python 3.10/3.11/3.12 with `pip install -e '.[dev,all]'`
and ffmpeg present, so every backend and the dev tooling are available.
This matches the install line spelled out in
[01_PACKAGING.md](01_PACKAGING.md) — don't drift between docs. Developers
can run a quick subset locally with `pytest -m "not slow"`.

### Lint and type

`ruff` for lint + format. `mypy --strict` on the `clicketysplit/` package.
Both wired into CI as required checks.

## Demo data (`src/clicketysplit/demo_assets/`)

Bundled in the wheel:

```
src/clicketysplit/demo_assets/
├── demo_recording.wav        # ~30 sec, ~8-10 words in mixed order
├── demo_stimuli.txt          # short stimulus list including the spoken words
└── README.md                 # explains what this is
```

Keep under 1 MB. 16 kHz mono is fine and keeps the size down. Use the
included recording to make a credibly noisy real-room example, not a
sterile silent-treated one — users should see that the tool handles
real recordings.

**Use mixed-order presentation, not blocked repetitions.** The demo is the
first thing a new user sees; if it's "3 stimuli × 4 reps in fixed order"
they may incorrectly assume the tool requires that paradigm. A
randomized-order demo with the default `strategy="none"` showcases the
core labeling workflow (detect → fuzzy-autocomplete-label → export) that
most users will actually do.

The `clicketysplit demo` command:

```python
def setup_demo_experiment() -> Path:
    """
    Copy the bundled demo assets to a temp dir, write a minimal
    clicketysplit.json, and return the experiment dir path.
    """
    tmp = Path(tempfile.mkdtemp(prefix="clicketysplit_demo_"))
    pkg = Path(__file__).parent  # demo_assets/
    (tmp / "recordings" / "demo_speaker" / "demo_condition").mkdir(parents=True)
    shutil.copy(pkg / "demo_recording.wav",
                tmp / "recordings" / "demo_speaker" / "demo_condition" / "rec.wav")
    (tmp / "stimulus_lists").mkdir()
    shutil.copy(pkg / "demo_stimuli.txt", tmp / "stimulus_lists" / "demo.txt")
    write_config(tmp / "clicketysplit.json", _DEMO_CONFIG)
    return tmp
```

First-run UX: `clicketysplit demo` opens the browser at the wizard already
past Setup, ready for the user to hit "Detect" and see something happen.

## Documentation (`docs/` + mkdocs)

### Pages

```
docs/
├── index.md             # one-paragraph pitch + 3-line install/run
├── install.md           # Python version, extras matrix, ffmpeg, common gotchas
├── quickstart.md        # 5-min walkthrough using the demo
├── workflow.md          # The 4-step wizard in detail
├── config-schema.md     # Every clicketysplit.json field
├── detection.md         # Three detectors compared; when to use which
├── exports.md           # Token naming, manifest, csv, textgrid
├── recordings-layout.md # Accepted layouts, what counts as a speaker/condition
├── troubleshooting.md   # Each error code + how to fix
├── contributing.md      # Dev setup, running tests, writing a new detector
└── changelog.md         # Generated from CHANGELOG.md (mkdocs-material include)
```

### mkdocs config

```yaml
# mkdocs.yml
site_name: clicketysplit
site_url: https://daphneweiss.github.io/clicketysplit/
repo_url: https://github.com/daphneweiss/clicketysplit
theme:
  name: material
  features:
    - navigation.tabs
    - navigation.sections
    - content.code.copy
    - search.suggest
  palette:
    - scheme: default
    - scheme: slate
markdown_extensions:
  - admonition
  - pymdownx.details
  - pymdownx.superfences
  - pymdownx.tabbed
  - toc:
      permalink: true
nav:
  - Home: index.md
  - Install: install.md
  - Quickstart: quickstart.md
  - Workflow: workflow.md
  - Configuration: config-schema.md
  - Detection: detection.md
  - Exports: exports.md
  - Recordings layout: recordings-layout.md
  - Troubleshooting: troubleshooting.md
  - Contributing: contributing.md
  - Changelog: changelog.md
```

### Deployment

GitHub Actions workflow `.github/workflows/docs.yml`:

```yaml
name: docs
on:
  push:
    branches: [main]
permissions:
  contents: write
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install '.[dev]'
      - run: mkdocs gh-deploy --force
```

This deploys to the `gh-pages` branch of the clicketysplit repo. In the
repo's Settings → Pages, set source to "Deploy from a branch: gh-pages".
URL becomes `https://daphneweiss.github.io/clicketysplit/`, which is a **project
site** and does not conflict with the user's personal portfolio at
`https://daphneweiss.github.io/`.

### Content priorities

If short on time, prioritize in this order:
1. `index.md`, `install.md`, `quickstart.md` — first contact with users
2. `workflow.md` — fills the role today's README's "Step 1/2/3/4" plays
3. `config-schema.md` — the reference users will consult often
4. `troubleshooting.md` — grows as real bugs come in; start small
5. `detection.md`, `exports.md` — once stable
6. `contributing.md` — only needed when someone wants to contribute

### Style

- Active voice, second person ("you click", not "the user clicks").
- Every code block is copy-pasteable. No `$` prompts.
- Show outputs, not just commands.
- Screenshots are great for the wizard pages but only after the UI is
  stable — replacing them as the UI shifts is a maintenance tax. Wait
  until post-v0.1.0.

## Release process

1. Bump version with a git tag: `git tag v0.1.0 && git push --tags`.
2. `release.yml` workflow triggers:
   - Build the frontend (`npm ci && npm run build`).
   - Run tests on Python 3.10/3.11/3.12.
   - Build the sdist and wheel (`python -m build`).
   - Publish to PyPI via OIDC trusted publishing (no API token in repo).
3. GitHub Release notes auto-generated from `CHANGELOG.md`.

For v0.1.0 (first PyPI release), publish to TestPyPI first and verify a
clean-env `pip install --index-url https://test.pypi.org/simple/
clicketysplit[all]` actually works before pushing to the real PyPI.
