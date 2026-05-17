# TotalRecal — Recording Segmentation Applet

A browser-based tool for segmenting, reviewing, and exporting word tokens from
recorded speech sessions. Built for phonetics/linguistics experiments where
speakers produce each stimulus word multiple times.

## Quick Start

```bash
cd stim_pipeline/
python app.py                    # http://localhost:5000
python app.py --port 8080        # custom port
```

Then open your browser to the displayed URL.

## Requirements

Install Python dependencies:

```bash
pip install flask numpy scipy soundfile matplotlib praat-parselmouth noisereduce 
```

## Project Structure

```
totalrecal/
├── stim_pipeline/
│   ├── app.py                  # Flask backend
│   ├── index.html              # Comprehensive frontend
│   ├── segment_recording.py    # Detection & export engine
│   ├── review_tool.html        # Standalone review tool (legacy)
│   └── setup_experiment.py     # Experiment setup from CSV
├── recordings/                 # Audio recordings (symlink)
│   ├── m1/
│   │   ├── critical_sh_interleaved/
│   │   ├── critical_s_interleaved/
│   │   ├── filler_word/
│   │   └── filler_pseudo/
│   └── f1/, f2/, ...
├── experiment/
│   ├── stimulus_lists/         # Per-condition word lists
│   └── {speaker}/{condition}/  # Detection output & reviewed segments
├── sessions/                   # Saved session pickles
└── requirements.txt
```

## API Reference

All endpoints are served by the Flask backend at `http://localhost:5000`.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/speakers` | List available speakers |
| GET | `/api/conditions/{speaker}` | List conditions for a speaker |
| GET | `/api/stimlists` | List stimulus list files |
| GET | `/api/stimlist_content/{file}` | Get words in a stimulus list |
| GET | `/api/audio/{speaker}/{cond}/{file}` | Stream an audio file |
| GET | `/api/audio_combined/{speaker}/{cond}` | Stream combined/denoised audio |
| POST | `/api/detect` | Run detection on one condition |
| POST | `/api/detect_all` | Run detection on all conditions |
| GET | `/api/segments/{speaker}/{cond}` | Load segments JSON |
| POST | `/api/save_segments` | Save reviewed segments |
| POST | `/api/export` | Export tokens for one condition |
| POST | `/api/export_all` | Export all conditions |
| POST | `/api/save_session` | Save session to pickle |
| POST | `/api/load_session` | Load session from pickle |
| GET | `/api/sessions` | List saved sessions |


# Clickety Split: A Fast, ML-Powered Audio Segmentation Pipeline


A browser-based tool for segmenting, reviewing, and exporting word tokens from
recorded speech sessions. Built for phonetics/linguistics experiments where
speakers produce each stimulus word multiple times.

### Disclaimer
Like many academic scripts, this project was built by one person with one set of experiments in mind, and has not been extensively tested outside that context. Always run any processing tool on a COPY of your original stimuli. This pipeline is not under active development and is not guaranteed to work on your system. 

## Quickstart

### Problem: 
Manual audio segmentation is one of the most tedious bottlenecks in preparing spoken language experiments and often requires experience with specific software (Praat, Audacity). Yet fully AI/ML approaches are not yet robust enough to handle all recording scenarios or stimuli (e.g. pseudowords).

### Solution:
Clickety Split runs one auto-segmentation pass to propose segments, which you may then adjust token-by-token by clicking or dragging the boundaries. Filenames are validated against the stimulus list to prevent typos, and numbers of tokens per word are tracked and automatically appended to the recordings. 

## Researcher Set-up
Clickety Split expects your recordings to be set up in one parent directory with *subdirectories for each speaker* and then *one subdirectory per condition, per speaker (optional)* If there is more than one audio file in a condition subfolder, they will be concatenated by default.

Ex:

├── recordings/                 
│   ├── male1/ 
│   │   ├── critical_sh
│   │   ├── critical_s
│   │   ├── filler_word/
│   │   └── filler_pseudo/
│   └── female1/, female2/, ...


You will also pass one or more lists of valid stimuli names. The segmenter will be able to pass one stimulus list per condition, and only names from that list will be allowed to populate a segment's label. (You can use the same list for all conditions, but the label auto-fill will work faster if you limit each category to its own valid names.)

Filenames will be formed as speaker-label-token#.wav


## Segmentation Instructions

The applet guides you through four steps:

### Step 1: Setup Experiment

- **Pick a speaker** from the dropdown (auto-populated from whatever you pass as the `recordings/` parent directory)
- **Map conditions** to their recording folders and stimulus lists
  - Click **Auto-detect** to populate from available condition folders, if available
  - Each condition shows which audio files are in its folder
  - Assign the matching stimulus list for each condition, or select the single master list
- **Adjust detection parameters**:
  - *Min word duration (ms)*: segments shorter than this are rejected as noise (default: 500 ms, may require trial and error for shorter stimmli)
  - *Max word duration (ms)*: segments longer than this are flagged as crosstalk (default: 1400ms)
  - *Min silence gap (ms)*: minimum silence between words to count as a gap (default: 150ms)
  - *Noise reduction*: spectral-gating noise reduction on/off
- Click **Run Detection on All Conditions** to process everything at once. Be patient, this step may take some time since it will run a ML library to automatically propose segments. 

### Step 2: Review & Adjust Segments

Review detected word tokens one by one for each condition:

- **Switch conditions** via the buttons at the top
- **Waveform + Spectrogram**: visualization of the recording with adjustable boundaries
- **Boundary adjustment**: click where you would like to move the boundary to. Alternatively, drag the proposed L (left/start) and R (right/end) boundary handles independently
- **Zoom**: mouse wheel to zoom in/out on the waveform (anchored at cursor position)
- **Pan**: Shift+drag to scroll the view horizontally
- **Playback**: Press **tab** or ▶ button to hear the current segment
- **Label editing**: type to rename the segment, with autocomplete from the stimulus list
- **Actions**:
  - **Accept (Enter)**: approve the token with current boundaries and label
  - **Reject (R)**: mark the token as rejected (excluded from export)
  - **Skip (S)**: move to next without marking
  - **Back (←)**: go to previous token
  - **Add Token (A)**: manually add a missed token by clicking start and end positions

Reviewed segments are **automatically saved** to `experiment/{speaker}/{condition}/reviewed_segments.json` when you reach the end.

### Step 3: Select Tokens

Pick which tokens to export for each word:

- Tokens are grouped by word, showing filename and duration
- **Click to toggle** selection (checkbox)
- Multiple tokens per word are supported — they export with `-1`, `-2`, `-3` suffixes
- Use **Select All** / **Deselect All** for bulk operations
- **Play button** (▶) on each token row for quick comparison

### Step 4: Export

Bulk-export all selected tokens across all conditions:

- Summary view showing which conditions are reviewed and how many tokens are selected
- **Export All** writes WAV files to `experiment/{speaker}/{condition}/tokens/`
- Each token file is named: `{speaker}_{word}-{N}.wav`
- A `token_manifest.json` is saved alongside the exports

## Save / Load Progress

Click **💾 Save** or **📂 Load** in the top bar at any time:

- Sessions are saved as `.pkl` files in the `sessions/` directory at the project root
- Each session captures the full state: speaker, conditions, parameters, review progress
- Load a previous session to resume where you left off

## Keyboard Shortcuts (Review Mode)

| Key | Action |
|-----|--------|
| Tab | Play current segment |
| Enter | Accept token |
| R | Reject token |
| S | Skip to next |
| ← | Go back |
| A | Toggle add-token mode |
| Esc | Cancel add-token mode |
| Scroll wheel | Zoom in/out |
| Shift+drag | Pan/scroll view |
