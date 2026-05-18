# Quickstart

This walkthrough takes about five minutes and uses the bundled demo data.
You'll go from "no experiment" to "exported WAV tokens on disk" without
touching CLI flags or editing config files.

## Step 0: Launch the demo

```
clicketysplit demo
```

What this does:

1. Copies the bundled demo recording (~30 s of mixed-order speech) and its
   stimulus list to a temp directory.
2. Writes a minimal `clicketysplit.json` pointing at them.
3. Starts the Flask server on `127.0.0.1:5000`.
4. Opens your browser at that URL with the experiment already loaded.

Expected terminal output:

```
  clicketysplit
  http://127.0.0.1:5000
```

The browser opens to the wizard at Step 2 (Review) — the demo skips Setup
because the config is already valid.

## Step 1: Run detection

In the top of the browser, click **Run detection**. The backend loads the
demo audio, runs the energy VAD, and proposes word boundaries. You should
see:

- An overview waveform with green bars over each proposed word.
- A token strip below showing each detected segment with a play button.
- A status bar at the bottom: `8 segments proposed, 8 word-typed`.

Detection is the slow step — for the demo recording it finishes in under a
second. For real recordings it can take a few seconds per minute of audio,
longer with Silero.

## Step 2: Review and label

The Review step is where you confirm boundaries and assign labels. The demo
uses `presentation_order: "random"`, so every token starts with an empty label.

For each token:

1. Press `Tab` to play it.
2. Type the first few letters of what you heard. The fuzzy-match dropdown
   shows matching stimuli.
3. Press `Enter` to accept the label and advance to the next token.

Useful keys:

| Key | Action |
|---|---|
| `Tab` | Play current token |
| `Enter` | Accept label and advance |
| `R` | Reject token |
| `←` / `→` | Previous/next token |
| `Esc` | Cancel a modal |

If a boundary looks wrong, drag the L or R handle on the waveform. The change
auto-saves after one second.

## Step 3: Select tokens to export

Click **Select** in the step nav. You see your tokens grouped by label:

```
apple   [x] -1   [x] -2
banana  [x] -1
cherry  [x] -1   [ ] -2  (rejected)
```

Every accepted, labeled token is checked by default. Uncheck any you don't
want exported. Tokens with no label appear under an "Unlabeled" group at the
top and can't be exported until you label them.

## Step 4: Export

Click **Export**. The wizard writes one WAV per selected token to:

```
<demo-tmpdir>/output/demo_speaker/demo_condition/tokens/
```

Expected files:

```
demo_speaker_apple-1.wav
demo_speaker_apple-2.wav
demo_speaker_banana-1.wav
demo_speaker_cherry-1.wav
token_manifest.json
tokens.csv
```

The status bar shows `4 tokens exported`. Open the temp directory printed in
the terminal to inspect the files. You're done.

## What's next

- Read [Workflow](workflow.md) for the full four-step wizard, including
  `cycled` and `blocked` presentation orders.
- Set up your own experiment: see [Recordings layout](recordings-layout.md)
  for the directory conventions and [Configuration](config-schema.md) for the
  `clicketysplit.json` schema.
- If detection looks bad, try a different detector: see [Detection](detection.md).
