<script lang="ts">
  import {
    detectAll as apiDetectAll,
    discoverRecordings,
    listStimulusLists,
    loadConfig as apiLoadConfig,
    pickFolder,
    resolveBrowse,
    writeConfig,
  } from "../lib/api";
  import { ApiError } from "../lib/types";
  import {
    rememberConfigPath,
    rememberRecordingsRoot,
    setStep,
    state as appState,
    toastError,
    pushToast,
  } from "../lib/store.svelte";
  import type {
    Condition,
    DetectAllResponse,
    DetectionConfig,
    DiscoveryResult,
    ExperimentConfig,
    PresentationOrder,
    ScannedSpeaker,
    Speaker,
  } from "../lib/types";
  import { formatBytes } from "../lib/format";

  // ---- Sub-step state -----------------------------------------------------
  // 1: Pick existing config OR new
  // 2: Pick recordings root (and discover)
  // 3: Edit speakers/conditions tree
  // 4: Choose detection parameters
  // (Save+detect is the action that leaves step 4 and advances to wizard
  // step 2.)
  type Sub = 1 | 2 | 3 | 4;
  let sub = $state<Sub>(1);

  // Sub-step 1 inputs
  let configPathInput = $state<string>(appState.lastConfigPath ?? "");
  let loadingExisting = $state<boolean>(false);

  // Sub-step 2 inputs
  let recordingsRootInput = $state<string>(appState.lastRecordingsRoot ?? "");
  let discovering = $state<boolean>(false);
  let discovery = $state<DiscoveryResult | null>(null);
  // Best-effort Browse helper. Chromium has showDirectoryPicker (clean UX,
  // no file listing). Firefox/Safari fall back to <input webkitdirectory>,
  // which prompts to read the folder's files — we only use the first file's
  // webkitRelativePath to extract the folder name. C6 still says the text
  // field is the contract; this is one-click pre-fill, not the source of truth.
  let browseFileInput: HTMLInputElement | undefined = $state();

  // Sub-step 3 inputs: editable mirrors of the discovered tree.
  // `speakers` is a list of { id, subdir, included } and `conditions` is the
  // de-duplicated unioned list across speakers, each with name + stim list +
  // presentation_order + reps. The user can rename conditions and toggle
  // speakers in/out before save.
  interface EditableSpeaker {
    id: string;
    subdir: string;
    included: boolean;
    nConditions: number;
    nFiles: number;
  }
  interface EditableCondition {
    name: string;
    stimulus_list: string;
    presentation_order: PresentationOrder;
    expected_reps_per_stimulus: number;
  }

  let speakers = $state<EditableSpeaker[]>([]);
  let conditions = $state<EditableCondition[]>([]);
  let stimulusListFiles = $state<string[]>([]);
  let stimulusListsRoot = $state<string>("stimulus_lists");

  // Where to save clicketysplit.json. Defaults to the recordings root's
  // parent (which is the typical experiment-dir layout); user can edit.
  let configDirInput = $state<string>("");
  let experimentName = $state<string>("");
  // Where the per-pair detection / review / token outputs go. Relative
  // to the config dir if not absolute. "output" is the v0.1 default.
  let outputRootInput = $state<string>("output");

  // Sub-step 4: detection params (mirrors of the pydantic DetectionConfig
  // defaults; backend is filtered to available detectors).
  let detection = $state<DetectionConfig>({
    backend: "silero",
    vad_threshold: 0.5,
    min_segment_ms: 150,
    min_silence_ms: 150,
    silence_margin_ms: 25,
    denoise: true,
  });

  // After save + detect_all: show a modal with per-pair results.
  let savingDetecting = $state<boolean>(false);
  let detectResults = $state<DetectAllResponse | null>(null);

  // ---- Helpers ------------------------------------------------------------

  // Mirrors _normalize_wsl_path in routes.py. Cleans up paths pasted from
  // Windows Explorer (UNC \\wsl.localhost\<distro>\foo\bar) into POSIX so
  // the backend's Path joining works on Linux.
  const UNC_WSL = /^\\\\wsl(?:\.localhost|\$)\\[^\\]+\\(.*)$/i;
  function normalizeWslPath(s: string): string {
    s = s.trim();
    if (!s) return s;
    const m = UNC_WSL.exec(s);
    if (m) return "/" + m[1].replaceAll("\\", "/");
    if (s.includes("\\") && !s.startsWith("/")) return s.replaceAll("\\", "/");
    return s;
  }

  function normalizeRecordingsRootOnBlur(): void {
    const cleaned = normalizeWslPath(recordingsRootInput);
    if (cleaned !== recordingsRootInput) recordingsRootInput = cleaned;
  }

  function normalizeConfigDirOnBlur(): void {
    const cleaned = normalizeWslPath(configDirInput);
    if (cleaned !== configDirInput) configDirInput = cleaned;
  }

  function normalizeOutputRootOnBlur(): void {
    const cleaned = normalizeWslPath(outputRootInput);
    if (cleaned !== outputRootInput) outputRootInput = cleaned;
  }

  function normalizeConditionStimulusListOnBlur(i: number): void {
    const cleaned = normalizeWslPath(conditions[i].stimulus_list);
    if (cleaned !== conditions[i].stimulus_list) {
      conditions[i].stimulus_list = cleaned;
    }
  }

  function defaultConfigDirFromRoot(root: string): string {
    if (!root) return "";
    // If root ends with "recordings" or similar, use its parent. Otherwise
    // use the root itself.
    const trimmed = root.replace(/[/\\]+$/, "");
    const sepIdx = Math.max(trimmed.lastIndexOf("/"), trimmed.lastIndexOf("\\"));
    if (sepIdx < 0) return trimmed;
    const tail = trimmed.slice(sepIdx + 1).toLowerCase();
    if (tail === "recordings" || tail === "audio" || tail === "data") {
      return trimmed.slice(0, sepIdx) || trimmed;
    }
    return trimmed;
  }

  function uniqueConditionsFromDiscovery(d: DiscoveryResult): EditableCondition[] {
    const seen = new Set<string>();
    const out: EditableCondition[] = [];
    for (const c of d.unique_condition_names) {
      if (seen.has(c)) continue;
      seen.add(c);
      // Pre-fill the stimulus_list slot with the conventional path; the
      // user picks the actual file in the dropdown once we list them.
      out.push({
        name: c,
        stimulus_list: "",
        presentation_order: "blocked",
        expected_reps_per_stimulus: 2,
      });
    }
    return out;
  }

  function pickStimulusListFor(name: string): string {
    // Same matcher as the original tool's Auto-detect: exact "<name>.txt"
    // first, then any file whose name contains the condition name, then any
    // file containing every "_"-separated part of it (so condition
    // "filler_word" matches "filler_word_stimuli.txt").
    const cLow = name.toLowerCase();
    const exact = stimulusListFiles.find(
      (f) => f.toLowerCase() === `${cLow}.txt`,
    );
    const match =
      exact ??
      stimulusListFiles.find((f) => {
        const sLow = f.toLowerCase();
        return (
          sLow.includes(cLow) ||
          cLow.split("_").every((part) => sLow.includes(part))
        );
      });
    return match ? `${stimulusListsRoot}/${match}` : "";
  }

  function selectAvailableDetector(): void {
    const caps = appState.capabilities;
    if (!caps) return;
    if (caps.detectors.length === 0) return;
    if (!caps.detectors.includes(detection.backend)) {
      detection.backend = caps.detectors[0];
    }
    if (!caps.denoise_available) {
      detection.denoise = false;
    }
  }

  // ---- Sub-step 1: existing config ---------------------------------------

  async function loadExisting(): Promise<void> {
    if (!configPathInput.trim()) {
      pushToast("Enter a path to a clicketysplit.json first.", "warn");
      return;
    }
    loadingExisting = true;
    try {
      const resp = await apiLoadConfig(configPathInput.trim());
      appState.config = resp.config;
      appState.configPath = resp.config._experiment_path ?? configPathInput.trim();
      rememberConfigPath(appState.configPath);
      pushToast("Loaded existing experiment.", "success");
      // Move directly to wizard step 2 (Review). Setup is done.
      setStep(2);
    } catch (err) {
      toastError(err, "Failed to load config");
    } finally {
      loadingExisting = false;
    }
  }

  function startNew(): void {
    sub = 2;
  }

  // ---- Sub-step 2: recordings root + discover -----------------------------

  async function fillFromFolderName(name: string): Promise<void> {
    const result = await resolveBrowse(name);
    if (result.matches.length === 0) {
      pushToast(
        `Browse picked "${name}" but no matching folder was found in your home. Please paste the absolute path.`,
        "warn",
      );
    } else if (result.matches.length === 1) {
      recordingsRootInput = result.matches[0];
      pushToast("Filled in path from Browse.", "info");
    } else {
      recordingsRootInput = result.matches[0];
      pushToast(
        `Found ${result.matches.length} candidates; using "${result.matches[0]}". Edit the field if that's wrong.`,
        "info",
      );
    }
  }

  async function browseRecordingsRoot(): Promise<void> {
    // Prefer the server-side native folder picker (returns an absolute
    // path — the whole point). Falls back to the browser-side pickers
    // only if the server picker can't run (no display, etc.).
    try {
      const r = await pickFolder({
        title: "Pick the recordings folder",
        initial_dir: recordingsRootInput.trim() || undefined,
      });
      if (r.path) {
        recordingsRootInput = r.path;
        return;
      }
      // r.path is null = user cancelled OR no display. Treat cancel
      // silently; if no display, the browser fallbacks below still try.
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn("native pick_folder failed; falling back:", err);
    }
    const win = window as unknown as {
      showDirectoryPicker?: () => Promise<{ name: string }>;
    };
    if (typeof win.showDirectoryPicker === "function") {
      try {
        const handle = await win.showDirectoryPicker();
        await fillFromFolderName(handle.name);
      } catch (err) {
        if ((err as { name?: string }).name !== "AbortError") {
          // eslint-disable-next-line no-console
          console.warn("browse failed:", err);
        }
      }
      return;
    }
    if (browseFileInput) browseFileInput.click();
  }

  async function browseConfigDir(): Promise<void> {
    try {
      const r = await pickFolder({
        title: "Pick the folder to save clicketysplit.json into",
        initial_dir:
          configDirInput.trim() ||
          recordingsRootInput.trim() ||
          undefined,
      });
      if (r.path) {
        configDirInput = r.path;
      }
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn("native pick_folder failed:", err);
    }
  }

  async function browseOutputRoot(): Promise<void> {
    try {
      const r = await pickFolder({
        title: "Pick the output folder (tokens + manifests land here)",
        initial_dir:
          outputRootInput.trim() && outputRootInput.startsWith("/")
            ? outputRootInput.trim()
            : configDirInput.trim() ||
              recordingsRootInput.trim() ||
              undefined,
      });
      if (r.path) {
        outputRootInput = r.path;
      }
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn("native pick_folder failed:", err);
    }
  }

  async function onBrowseFilesPicked(event: Event): Promise<void> {
    const input = event.currentTarget as HTMLInputElement;
    const files = input.files;
    if (!files || files.length === 0) return;
    const rel =
      (files[0] as File & { webkitRelativePath?: string }).webkitRelativePath ??
      "";
    const folderName = rel.split("/")[0] ?? "";
    // Reset so picking the same folder again still fires a change event.
    input.value = "";
    if (folderName) await fillFromFolderName(folderName);
  }

  async function runDiscover(): Promise<void> {
    const root = recordingsRootInput.trim();
    if (!root) {
      pushToast("Enter a recordings root path first.", "warn");
      return;
    }
    discovering = true;
    try {
      const d = await discoverRecordings(root);
      discovery = d;
      appState.discovery = d;
      rememberRecordingsRoot(root);
      configDirInput = configDirInput || defaultConfigDirFromRoot(root);
      // Build editable mirrors.
      speakers = d.speakers.map((s: ScannedSpeaker) => ({
        id: s.id,
        subdir: s.subdir,
        included: true,
        nConditions: s.conditions.length,
        nFiles:
          s.conditions.reduce((a, c) => a + c.n_files, 0) + s.flat_files.length,
      }));
      conditions = uniqueConditionsFromDiscovery(d);

      // Discover already probed a few conventional locations for
      // stimulus_lists/ (sibling of recordings/, child of parent, etc.) and
      // returned both the absolute root dir it found and any *.txt files in
      // it. Use that root so pickStimulusListFor builds absolute paths the
      // backend can resolve regardless of where the config dir ends up.
      stimulusListFiles = d.stimulus_list_files;
      if (d.stimulus_lists_root) stimulusListsRoot = d.stimulus_lists_root;
      if (stimulusListFiles.length > 0) {
        for (const c of conditions) {
          if (!c.stimulus_list) c.stimulus_list = pickStimulusListFor(c.name);
        }
      } else {
        // Fall back to the saved-config endpoint (loading an existing
        // experiment goes through here too).
        try {
          const sl = await listStimulusLists();
          stimulusListFiles = sl.files;
          stimulusListsRoot = sl.stimulus_lists_root;
          for (const c of conditions) {
            if (!c.stimulus_list) c.stimulus_list = pickStimulusListFor(c.name);
          }
        } catch {
          // No sibling stimulus_lists/ and no saved experiment — user types
          // the paths directly.
        }
      }

      if (d.speakers.length === 0) {
        pushToast(
          "No speakers found under that path. Check the structure: each speaker is a subfolder containing audio.",
          "warn",
        );
      } else {
        pushToast(
          `Discovered ${d.speakers.length} speaker(s) and ${d.unique_condition_names.length} condition name(s).`,
          "success",
        );
        sub = 3;
      }
    } catch (err) {
      toastError(err, "Discovery failed");
    } finally {
      discovering = false;
    }
  }

  // ---- Sub-step 3: speakers/conditions edits -----------------------------

  function addCondition(): void {
    conditions.push({
      name: "",
      stimulus_list: "",
      presentation_order: "random",
      expected_reps_per_stimulus: 2,
    });
  }

  function removeCondition(idx: number): void {
    conditions.splice(idx, 1);
  }

  function canAdvanceToParams(): boolean {
    if (speakers.filter((s) => s.included).length === 0) return false;
    if (conditions.length === 0) return false;
    for (const c of conditions) {
      if (!c.name.trim()) return false;
      if (!c.stimulus_list.trim()) return false;
    }
    return true;
  }

  function goToParams(): void {
    if (!canAdvanceToParams()) {
      pushToast(
        "Each speaker stays included or excluded, and every condition needs a name plus a stimulus list path.",
        "warn",
      );
      return;
    }
    selectAvailableDetector();
    sub = 4;
  }

  // ---- Sub-step 4: save and detect ---------------------------------------

  function buildExperimentConfig(): ExperimentConfig {
    const cfgSpeakers: Speaker[] = speakers
      .filter((s) => s.included)
      .map((s) => ({ id: s.id, subdir: s.subdir }));
    const cfgConditions: Condition[] = conditions.map((c) => ({
      name: c.name.trim(),
      stimulus_list: c.stimulus_list.trim(),
      presentation_order: c.presentation_order,
      expected_reps_per_stimulus: c.expected_reps_per_stimulus,
    }));
    return {
      schema_version: 1,
      name: experimentName.trim(),
      // The wizard supplies an absolute recordings root; the config stores
      // it as-is. Backend resolves it relative to the config dir if
      // possible, so this works whether the user typed absolute or
      // experiment-relative.
      recordings_root: recordingsRootInput.trim(),
      stimulus_lists_root: stimulusListsRoot,
      output_root: outputRootInput.trim() || "output",
      speakers: cfgSpeakers,
      conditions: cfgConditions,
      detection: { ...detection },
      labeling: {
        min_word_duration_ms: 500,
        max_word_duration_ms: 1400,
        drop_intro_block: true,
      },
      export: {
        pad_ms: 20,
        fade_ms: 3,
        format: "wav",
        produce_csv: true,
        produce_textgrid: false,
      },
    };
  }

  async function saveAndDetect(): Promise<void> {
    const configDir = configDirInput.trim();
    if (!configDir) {
      pushToast("Pick a folder to save clicketysplit.json into.", "warn");
      return;
    }
    savingDetecting = true;
    detectResults = null;
    try {
      const cfg = buildExperimentConfig();
      let saved: Awaited<ReturnType<typeof writeConfig>>;
      try {
        saved = await writeConfig(configDir, cfg);
      } catch (err) {
        // A clicketysplit.json already lives there and it isn't the one we
        // loaded — make the user say so explicitly before replacing it.
        if (err instanceof ApiError && err.code === "config_exists") {
          const ok = window.confirm(
            `${configDir}/clicketysplit.json already exists.\n\n` +
              "Overwrite it? (Cancel keeps the existing file untouched — " +
              "change the config directory to save elsewhere.)",
          );
          if (!ok) {
            savingDetecting = false;
            return;
          }
          saved = await writeConfig(configDir, cfg, true);
        } else {
          throw err;
        }
      }
      appState.config = cfg;
      appState.config._experiment_path = saved.path;
      appState.configPath = saved.path;
      rememberConfigPath(saved.path);
      pushToast(`Saved config to ${saved.path}.`, "success");

      // Kick off detection across every configured speaker × condition.
      // The backend blocks while detection runs — for a five-minute file
      // that is a few seconds of work per condition with Silero.
      const results = await apiDetectAll();
      detectResults = results;
      const okCount = results.results.filter((r) => r.status === "ok").length;
      const errCount = results.results.length - okCount;
      pushToast(
        `Detection complete: ${okCount} succeeded${
          errCount ? `, ${errCount} failed` : ""
        }.`,
        errCount ? "warn" : "success",
      );
    } catch (err) {
      toastError(err, "Save / detect failed");
    } finally {
      savingDetecting = false;
    }
  }

  function finishToReview(): void {
    detectResults = null;
    setStep(2);
  }
</script>

<section class="setup">
  <header class="setup-head">
    <h1>Set up your experiment</h1>
    <ol class="sub-progress" aria-label="Setup sub-steps">
      <li class:active={sub === 1}>1. Experiment</li>
      <li class:active={sub === 2}>2. Recordings</li>
      <li class:active={sub === 3}>3. Speakers and conditions</li>
      <li class:active={sub === 4}>4. Detection</li>
    </ol>
  </header>

  <!-- Sub-step 1: pick existing or new -->
  {#if sub === 1}
    <div class="section">
      <h2>Load an existing experiment</h2>
      <p class="muted">
        Paste the absolute path to an existing
        <span class="mono">clicketysplit.json</span>. If you don't have one yet,
        skip this and start a new experiment below.
      </p>
      <div class="form-row">
        <input
          class="inp"
          type="text"
          placeholder="/abs/path/to/experiment/clicketysplit.json"
          bind:value={configPathInput}
          spellcheck={false}
        />
        <button
          class="btn primary"
          onclick={loadExisting}
          disabled={loadingExisting || !configPathInput.trim()}
        >
          {loadingExisting ? "Loading…" : "Load"}
        </button>
      </div>
    </div>

    <div class="section">
      <h2>Start a new experiment</h2>
      <p class="muted">
        You'll pick a recordings folder, confirm speakers and conditions, and
        choose detection parameters. The wizard writes a fresh
        <span class="mono">clicketysplit.json</span> at the end.
      </p>
      <button class="btn primary" onclick={startNew}>Start new experiment</button>
    </div>
  {/if}

  <!-- Sub-step 2: recordings root -->
  {#if sub === 2}
    <div class="section">
      <h2>Pick the recordings folder</h2>
      <p class="muted">
        Type or paste the absolute path to the folder that contains your
        speaker subfolders (e.g. <code>/data/recordings</code> with
        <code>f1/</code>, <code>f2/</code>… inside).
      </p>
      <div class="form-row">
        <input
          class="inp"
          type="text"
          placeholder="/abs/path/to/recordings"
          bind:value={recordingsRootInput}
          onblur={normalizeRecordingsRootOnBlur}
          spellcheck={false}
        />
        <button
          class="btn"
          type="button"
          onclick={browseRecordingsRoot}
          title="Best-effort: picks a folder and asks the server to resolve its absolute path"
        >
          Browse…
        </button>
        <input
          bind:this={browseFileInput}
          type="file"
          style="display: none"
          multiple
          onchange={onBrowseFilesPicked}
          webkitdirectory={true}
        />
        <button
          class="btn primary"
          onclick={runDiscover}
          disabled={discovering || !recordingsRootInput.trim()}
        >
          {discovering ? "Scanning…" : "Scan"}
        </button>
      </div>
      <div class="back-row">
        <button class="btn" type="button" onclick={() => (sub = 1)}>
          Back
        </button>
      </div>
    </div>
  {/if}

  <!-- Sub-step 3: edit speakers/conditions -->
  {#if sub === 3 && discovery}
    <div class="section">
      <h2>Speakers</h2>
      <p class="muted">
        Check the speakers you want to include in this experiment. Each row
        shows what the scan found under
        <span class="mono">{discovery.root}</span>.
      </p>
      <table class="simple">
        <thead>
          <tr>
            <th>Include</th>
            <th>ID</th>
            <th>Subdir</th>
            <th>Conditions found</th>
            <th>Files</th>
          </tr>
        </thead>
        <tbody>
          {#each speakers as s, i (s.id)}
            <tr>
              <td>
                <input
                  type="checkbox"
                  bind:checked={speakers[i].included}
                  aria-label={`Include speaker ${s.id}`}
                />
              </td>
              <td class="mono">{s.id}</td>
              <td class="mono">{s.subdir}</td>
              <td>{s.nConditions}</td>
              <td>{s.nFiles}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>

    <div class="section">
      <h2>Conditions</h2>
      <p class="muted">
        Each condition maps to a subfolder inside every included speaker's
        directory and a stimulus list. Paths are relative to the config file
        you'll save at the end.
      </p>
      <table class="simple">
        <thead>
          <tr>
            <th>Name</th>
            <th>Stimulus list</th>
            <th>Presentation order</th>
            <th>Reps</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {#each conditions as c, i}
            <tr>
              <td>
                <input
                  class="inp"
                  type="text"
                  bind:value={conditions[i].name}
                  spellcheck={false}
                  placeholder="condition_a"
                />
              </td>
              <td>
                {#if stimulusListFiles.length > 0}
                  <select class="inp" bind:value={conditions[i].stimulus_list}>
                    <option value="">— pick a list —</option>
                    {#each stimulusListFiles as f}
                      <option value={`${stimulusListsRoot}/${f}`}>{f}</option>
                    {/each}
                  </select>
                {:else}
                  <input
                    class="inp"
                    type="text"
                    bind:value={conditions[i].stimulus_list}
                    onblur={() => normalizeConditionStimulusListOnBlur(i)}
                    placeholder="stimulus_lists/condition_a.txt"
                    spellcheck={false}
                  />
                {/if}
              </td>
              <td>
                <select
                  class="inp"
                  bind:value={conditions[i].presentation_order}
                  title="How the stimuli were presented, which decides how labels prefill: massed repeats each word K times before the next; spaced runs the whole list each round; random prefills nothing."
                >
                  <option value="random">Random (no prefill)</option>
                  <option value="cycled">Spaced (abc abc abc)</option>
                  <option value="blocked">Massed (aaa bbb ccc)</option>
                </select>
              </td>
              <td>
                <input
                  class="inp"
                  type="number"
                  min="1"
                  style="width:64px"
                  bind:value={conditions[i].expected_reps_per_stimulus}
                  disabled={conditions[i].presentation_order !== "blocked"}
                />
              </td>
              <td>
                <button
                  class="btn"
                  type="button"
                  onclick={() => removeCondition(i)}
                  aria-label="Remove condition"
                >
                  Remove
                </button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
      <div style="margin-top:8px">
        <button class="btn" type="button" onclick={addCondition}>
          + Add condition
        </button>
      </div>
    </div>

    <div class="section">
      <h2>Where to save</h2>
      <p class="muted">
        The wizard writes <span class="mono">clicketysplit.json</span> into this
        folder. The experiment's <span class="mono">output/</span> tree (and the
        per-experiment session JSON) live next to it.
      </p>
      <div class="form-row">
        <label class="lbl-wrap">
          <span class="lbl">Config directory (absolute)</span>
          <div style="display:flex; gap:0.4rem; align-items:stretch;">
            <input
              class="inp"
              type="text"
              bind:value={configDirInput}
              onblur={normalizeConfigDirOnBlur}
              placeholder="/abs/path/to/experiment"
              spellcheck={false}
              style="flex:1;"
            />
            <button class="btn" type="button" onclick={browseConfigDir}>
              Browse…
            </button>
          </div>
        </label>
        <label class="lbl-wrap">
          <span class="lbl">Output folder</span>
          <div style="display:flex; gap:0.4rem; align-items:stretch;">
            <input
              class="inp"
              type="text"
              bind:value={outputRootInput}
              onblur={normalizeOutputRootOnBlur}
              placeholder="output  (relative to config dir, or absolute)"
              spellcheck={false}
              style="flex:1;"
            />
            <button class="btn" type="button" onclick={browseOutputRoot}>
              Browse…
            </button>
          </div>
        </label>
        <label class="lbl-wrap">
          <span class="lbl">Experiment name (optional)</span>
          <input
            class="inp"
            type="text"
            bind:value={experimentName}
            placeholder="my fricative experiment"
          />
        </label>
      </div>
    </div>

    <div class="actions-row">
      <button class="btn" type="button" onclick={() => (sub = 2)}>Back</button>
      <button class="btn primary" type="button" onclick={goToParams}>
        Next: detection parameters
      </button>
    </div>
  {/if}

  <!-- Sub-step 4: detection params -->
  {#if sub === 4}
    <div class="section">
      <h2>Detection parameters</h2>
      <p class="muted">
        The defaults work for typical word-list recordings. Adjust if you're
        getting too many short noises or missing real words.
      </p>
      <div class="form-row">
        <label class="lbl-wrap">
          <span class="lbl">Backend</span>
          <select class="inp" bind:value={detection.backend}>
            {#if appState.capabilities}
              {#each appState.capabilities.detectors as d}
                <option value={d}>{d}</option>
              {/each}
            {:else}
              <option value="silero">silero</option>
            {/if}
          </select>
        </label>
        <label class="lbl-wrap">
          <span class="lbl">VAD threshold (Silero only)</span>
          <input
            class="inp"
            type="number"
            min="0.05"
            max="0.95"
            step="0.05"
            bind:value={detection.vad_threshold}
            disabled={detection.backend !== "silero"}
          />
        </label>
        <label class="lbl-wrap">
          <span class="lbl">Denoise</span>
          <select
            class="inp"
            bind:value={detection.denoise}
            disabled={!appState.capabilities?.denoise_available}
          >
            <option value={false}>Off</option>
            <option value={true}>On</option>
          </select>
        </label>
      </div>
      <div class="form-row">
        <label class="lbl-wrap">
          <span class="lbl">Min segment (ms)</span>
          <input
            class="inp"
            type="number"
            min="20"
            bind:value={detection.min_segment_ms}
          />
        </label>
        <label class="lbl-wrap">
          <span class="lbl">Min silence gap (ms)</span>
          <input
            class="inp"
            type="number"
            min="20"
            bind:value={detection.min_silence_ms}
          />
        </label>
        <label class="lbl-wrap">
          <span class="lbl">Silence margin (ms)</span>
          <input
            class="inp"
            type="number"
            min="0"
            bind:value={detection.silence_margin_ms}
          />
        </label>
      </div>
    </div>

    <div class="actions-row">
      <button class="btn" type="button" onclick={() => (sub = 3)}>Back</button>
      <button
        class="btn primary"
        type="button"
        onclick={saveAndDetect}
        disabled={savingDetecting}
      >
        {savingDetecting ? "Working…" : "Save and run detection"}
      </button>
    </div>
  {/if}

  <!-- Detection progress / results modal -->
  {#if savingDetecting || detectResults}
    <div class="modal-backdrop" role="dialog" aria-modal="true">
      <div class="modal">
        <h2>Detection</h2>
        {#if savingDetecting && !detectResults}
          <p>Writing config and running detection on every speaker × condition.</p>
          <p class="muted">
            This blocks until done. Energy is fast; Silero takes longer on
            first-load of the model.
          </p>
          <div class="spinner" aria-hidden="true"></div>
        {/if}
        {#if detectResults}
          <table class="simple">
            <thead>
              <tr>
                <th>Speaker</th>
                <th>Condition</th>
                <th>Status</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {#each detectResults.results as r}
                <tr>
                  <td class="mono">{r.speaker_id}</td>
                  <td class="mono">{r.condition}</td>
                  <td>
                    {#if r.status === "ok"}
                      <span style="color:var(--success)">ok</span>
                    {:else}
                      <span style="color:var(--danger)">error</span>
                    {/if}
                  </td>
                  <td>
                    {#if r.status === "ok"}
                      {r.n_words} words ({r.n_segments} segments,
                      {formatBytes(r.audio_duration_sec * 32000)} of audio)
                    {:else}
                      <span class="mono">{r.error.code}</span>:
                      {r.error.message}
                    {/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
          <div class="actions-row">
            <button class="btn primary" type="button" onclick={finishToReview}>
              Continue to Review
            </button>
          </div>
        {/if}
      </div>
    </div>
  {/if}
</section>

<style>
  .setup {
    padding: 20px;
    max-width: 960px;
    margin: 0 auto;
  }

  .setup-head {
    margin-bottom: 16px;
  }

  .sub-progress {
    list-style: none;
    margin: 8px 0 0;
    padding: 0;
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .sub-progress li {
    padding: 4px 10px;
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 12px;
    font-size: 11px;
    color: var(--text-dim);
  }
  .sub-progress li.active {
    background: var(--accent);
    color: white;
    border-color: var(--accent);
  }

  .back-row {
    margin-top: 8px;
  }

  .lbl-wrap {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-width: 0;
    margin: 0;
  }

  .actions-row {
    display: flex;
    gap: 8px;
    justify-content: flex-end;
    margin-top: 8px;
  }

  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.55);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 500;
  }

  .modal {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 20px;
    max-width: 720px;
    width: 90%;
    max-height: 80vh;
    overflow-y: auto;
  }

  .spinner {
    margin: 16px 0;
    width: 24px;
    height: 24px;
    border: 3px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.9s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>
