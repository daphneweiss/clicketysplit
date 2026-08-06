# Recordings layout

clicketysplit's discovery scan is flexible. It accepts two layouts for a
speaker's recordings and gracefully handles missing condition folders.

The source of truth for discovery rules is
the discovery code in `src/clicketysplit/discovery.py`.

## The standard layout: per-condition subdirs

```
recordings/
├── speaker_01/
│   ├── condition_a/
│   │   └── recording.wav
│   └── condition_b/
│       └── recording.wav
└── speaker_02/
    ├── condition_a/
    │   ├── take1.wav
    │   └── take2.wav
    └── condition_b/
        └── recording.wav
```

Each speaker has a directory under `recordings_root`. Each condition is a
subdirectory under the speaker. Audio files live inside the condition
directory. Multiple files per condition are concatenated with a short
silence gap (see [Exports](exports.md) and [Detection](detection.md) for how
boundaries are tracked across the seam).

This is what the Setup Wizard generates by default and what we recommend for
new experiments.

## The flat layout: one condition per speaker

When a speaker has just one condition, you can put the audio file(s)
directly under the speaker directory:

```
recordings/
└── speaker_01/
    └── recording.wav
```

If your config has exactly one condition declared and a speaker's directory
contains audio files but no condition subdirs, the discovery scan treats
those files as that single condition.

## Mismatch handling

If a speaker has no recordings for a condition (e.g. they only did
`condition_a`, not `condition_b`), the discovery scan marks that
speaker × condition cell as "no recording" in the Setup UI. You can skip it
without errors — the wizard just won't run detection for empty cells.

## What counts as a speaker / condition

A directory immediately under `recordings_root` becomes a speaker if it
contains either:

- audio files directly, or
- subdirectories that contain audio files.

A directory under a speaker becomes a condition if it contains audio files.

"Audio file" means any extension recognized by `audio_io.SUPPORTED_EXTENSIONS`:
`.wav`, `.flac`, `.ogg`, `.opus`, and (with ffmpeg on `PATH`) `.mp3`, `.m4a`,
`.aac`.

## Reserved names and skipped directories

The scan **skips** any directory whose name:

- starts with `.` (dotfiles) — e.g. `.DS_Store`, `.git`.
- starts with `_` (underscore-prefixed) — by convention, these are scratch
  or backup folders.
- exactly matches one of the reserved names:
    - `output`
    - `tokens`
    - `final`
    - `alternates`

These names are reserved because clicketysplit writes its own directories
with these names under `output_root`. Skipping them in `recordings_root`
prevents you from accidentally running detection on already-exported tokens.

## Stimulus lists

Stimulus lists are separate from the recordings tree. They live under
`stimulus_lists_root` (typically `stimulus_lists/`) as plain `.txt` files
with one stimulus per line:

```
apple
banana
cherry
```

Each condition's config entry references its stimulus list by config-relative
path:

```json
{
  "name": "condition_a",
  "stimulus_list": "stimulus_lists/condition_a.txt"
}
```

Empty stimulus lists are rejected at config-load time.

## Performance

The discovery scan is cheap — it only stats files for presence and size, never
reads audio data. Scanning thousands of files takes well under a second on a
modern disk.
