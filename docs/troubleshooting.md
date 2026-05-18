# Troubleshooting

When something goes wrong, clicketysplit returns a structured error:

```json
{ "error": { "code": "...", "message": "..." } }
```

The UI surfaces the message in a toast. The `code` is the part you'd search
for in this page.

## Error codes

### `no_experiment`

**What it means:** You hit an API route that needs a loaded experiment, but
no `clicketysplit.json` has been loaded into the server. clicketysplit holds
one active experiment per process; the only route that mutates it is
`POST /api/config/load`.

**How to fix it:** Load a config first. From the wizard, walk through Setup
(Step 1) or use the file picker to open an existing `clicketysplit.json`. If
you launched with `--experiment PATH`, double-check the path resolves to a
valid config file.

### `not_found`

**What it means:** A speaker, condition, or file referenced in your request
doesn't exist on disk.

**How to fix it:** Check that:

- The speaker's `subdir` under `recordings_root` exists.
- The condition's subdirectory under that speaker exists (or, for flat
  layouts, the speaker dir has audio files directly).
- The config's `stimulus_list` path resolves to an existing file.

Re-run the Setup Wizard's discovery scan to see what the backend actually
finds on disk.

### `validation_error`

**What it means:** pydantic rejected a payload. This usually fires when you
edit `clicketysplit.json` by hand and introduce a type error or an unknown
field. The error includes the field path.

**How to fix it:** Read the field path in the message. Common cases:

- `presentation_order` value not one of `"random" | "cycled" | "blocked"`.
- `detection.backend` value not one of `"silero" | "webrtc" | "energy"`.
- `schema_version` set to a future version this clicketysplit doesn't know.

See [Configuration](config-schema.md) for the valid values.

### `missing_extra`

**What it means:** You asked for a detector or feature that requires an
optional pip extra you haven't installed. The response includes the `extra`
name.

**How to fix it:** Install the extra:

```
pip install 'clicketysplit[silero]'        # for Silero VAD
pip install 'clicketysplit[webrtc]'        # for WebRTC VAD
pip install 'clicketysplit[denoise]'       # for noisereduce
pip install 'clicketysplit[praat]'         # for TextGrid export
pip install 'clicketysplit[mp3]'           # for MP3/M4A loading
```

Or just `pip install 'clicketysplit[all]'`. Restart the server after
installing — capabilities are detected at boot.

### `already_reviewed`

**What it means:** You triggered detection (or another segments-overwriting
operation) on a condition that already has a `reviewed_segments.json`.
clicketysplit refuses to clobber your hand-reviewed labels without
confirmation.

**How to fix it:** Either:

- Keep the existing review and continue (the UI's default offer).
- Pass `force: true` (the wizard surfaces this as "Re-detect and discard
  review"). This overwrites `reviewed_segments.json` and you lose your
  labels for that condition.

### `bad_audio`

**What it means:** An audio file couldn't be decoded. Either the file is
corrupt or it's a format clicketysplit doesn't know how to read.

**How to fix it:** Confirm the file plays in another player. If it's MP3 /
M4A / AAC, make sure `ffmpeg` is on your `PATH` (`ffmpeg -version` should
work in the same terminal you launched clicketysplit from). If it's a format
not in `.wav | .flac | .ogg | .opus | .mp3 | .m4a | .aac`, convert it to WAV
first.

### `path_outside_experiment`

**What it means:** The backend computed a path that escapes the experiment
directory (e.g. via `../../etc/passwd`). This is a safety check. You should
never see this from normal use — it usually means a malformed API request.

**How to fix it:** If you're hitting this from the UI, file a bug. If you're
hitting this from a custom integration, make sure your paths stay within the
experiment root.

## General gotchas

### `ffmpeg` not on `PATH`

Loading `.mp3` / `.m4a` / `.aac` requires `ffmpeg`. clicketysplit detects it
at import time. Common signs of the problem:

- `bad_audio` error when you try to detect on a mp3.
- The Setup Wizard's "supported formats" list omits mp3.

Fix: install ffmpeg (see [Install](install.md#ffmpeg)) and **open a fresh
terminal** before launching clicketysplit so the updated `PATH` is picked up.

### Recordings root must be typed, not picked

The browser can't reliably hand the backend an absolute filesystem path from
a native folder-picker dialog. The Setup Wizard's "Recordings root" field is
a **text input**. Paste or type the absolute path.

A "Browse" button may pre-fill the path on browsers that support the File
System Access API, but the text field is the contract. If "Browse" doesn't
work for you, type the path.

### One experiment per server process

clicketysplit is a single-user desktop tool. There's exactly one active
experiment per server process, stored in `app.config["experiment_path"]`.

If you open two browser tabs pointing at the same `clicketysplit serve`
instance and load different experiments, the second one wins and the first
tab now silently operates on the wrong experiment. The status bar shows the
active experiment path so you can spot the collision.

To work on two experiments at once, run two `clicketysplit serve` processes
on different ports:

```
clicketysplit serve --port 5000 --experiment /path/to/exp1/clicketysplit.json
clicketysplit serve --port 5001 --experiment /path/to/exp2/clicketysplit.json
```

### Browser cached an old build

If you upgraded clicketysplit and the wizard looks unchanged, do a hard
reload (`Ctrl+Shift+R` or `Cmd+Shift+R`). The browser may be holding onto
the previous frontend bundle.

### "Detector unavailable"

The wizard greys out detectors whose extras aren't installed. The tooltip
tells you which extra to install. After `pip install`, restart
`clicketysplit serve` — capabilities are detected at boot, not per-request.

### Disk full mid-export

Token files are written atomically (`<name>.tmp`, fsync, rename). If you run
out of disk mid-export, the operation aborts with the IO error and reports
how many tokens succeeded. No partial WAVs end up on disk. Free up space and
re-run; existing tokens get overwritten.
