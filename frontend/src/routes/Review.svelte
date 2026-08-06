<script lang="ts">
  import { onDestroy, onMount, tick } from "svelte";
  import ConditionTabs from "../components/ConditionTabs.svelte";
  import TokenList from "../components/TokenList.svelte";
  import WaveformView from "../components/WaveformView.svelte";
  import SpectrogramView from "../components/SpectrogramView.svelte";
  import {
    activeCondState,
    advanceCursorOnAccept,
    clearAnchor,
    loadCondition,
    nextWordSegment,
    prevWordSegment,
    pushToast,
    setActive,
    setExpectedReps,
    setLabel,
    setStatus,
    state as appState,
    stepCursorBackOnPrev,
  } from "../lib/store.svelte";
  import { play, stop, subscribePlayback } from "../lib/audio";
  import { getAudioCache } from "../lib/store.svelte";
  import {
    buildReviewHotkeys,
    REVIEW_TYPING_ALLOWLIST,
    registerHotkeys,
  } from "../lib/keybindings";
  import type { Segment } from "../lib/types";
  import { formatMs } from "../lib/format";

  // Local UI state ---------------------------------------------------------
  let addMode = $state(false);
  let labelInput: HTMLInputElement | undefined = $state();
  let labelDraft = $state("");
  let dropdownOpen = $state(false);
  let dropdownIndex = $state(-1);
  let waveformView: WaveformView | undefined = $state();
  let addStartSec = $state<number | null>(null);
  let isPlaying = $state(false);

  // Derived ----------------------------------------------------------------
  const cond = $derived(activeCondState());
  const currentSeg = $derived<Segment | null>(
    cond ? (cond.segments[cond.currentTokenIndex] ?? null) : null,
  );
  const cache = $derived.by(() => {
    void appState.audioGeneration;
    if (!appState.activeSpeakerId || !appState.activeCondition) return null;
    return getAudioCache(appState.activeSpeakerId, appState.activeCondition);
  });

  /** Filtered stimulus list for the autocomplete dropdown. */
  const suggestions = $derived.by((): string[] => {
    if (!cond) return [];
    const q = labelDraft.trim().toLowerCase();
    const list = cond.stimulusList;
    if (list.length === 0) return [];
    if (!q) return list.slice(0, 20);
    // Substring matches first, then small-edit-distance candidates.
    const subs = list.filter((s) => s.toLowerCase().includes(q));
    if (subs.length > 0) return subs.slice(0, 20);
    // Fall back to ≤2 edit-distance neighbours.
    const fuzzy = list.filter((s) => editDistanceCapped(s.toLowerCase(), q, 2) <= 2);
    return fuzzy.slice(0, 20);
  });

  function editDistanceCapped(a: string, b: string, cap: number): number {
    // Damerau-Levenshtein-ish with an early cap so we stay O(n*cap) in
    // practice on a tiny stimulus list.
    if (Math.abs(a.length - b.length) > cap) return cap + 1;
    const m = a.length;
    const n = b.length;
    if (m === 0) return n;
    if (n === 0) return m;
    let prev = new Array<number>(n + 1);
    let cur = new Array<number>(n + 1);
    for (let j = 0; j <= n; j++) prev[j] = j;
    for (let i = 1; i <= m; i++) {
      cur[0] = i;
      let rowMin = cur[0];
      for (let j = 1; j <= n; j++) {
        const cost = a.charCodeAt(i - 1) === b.charCodeAt(j - 1) ? 0 : 1;
        cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
        if (cur[j] < rowMin) rowMin = cur[j];
      }
      if (rowMin > cap) return cap + 1;
      [prev, cur] = [cur, prev];
    }
    return prev[n];
  }

  // Sync label draft with the current segment when navigation changes it.
  // Prefill precedence ports the original showCurrentToken
  // (index.html:720-733): a reviewed segment shows its own label; else a
  // label that's actually in the stimulus list stands; else the cursor's
  // predicted next stimulus; else whatever the segment has.
  $effect(() => {
    void currentSeg?.assigned_name;
    void cond?.currentTokenIndex;
    const name = currentSeg?.assigned_name ?? "";
    if (!cond || !currentSeg) {
      labelDraft = name;
    } else if (
      currentSeg.status === "accepted" ||
      currentSeg.status === "rejected"
    ) {
      labelDraft = name;
    } else if (
      name &&
      cond.stimulusList.some(
        (s) => s.toLowerCase() === name.trim().toLowerCase(),
      )
    ) {
      labelDraft = name;
    } else if (
      cond.stimulusList.length > 0 &&
      cond.stimCursor < cond.stimulusList.length
    ) {
      labelDraft = cond.stimulusList[cond.stimCursor];
    } else {
      labelDraft = name;
    }
    dropdownIndex = -1;
    dropdownOpen = false;
  });

  // Restore active speaker/condition on enter, kicking off the load if no
  // condition has been loaded for them yet. If nothing is active, pick the
  // first configured pair.
  $effect(() => {
    if (!appState.config) return;
    if (appState.activeSpeakerId && appState.activeCondition) {
      const c = appState.reviewProgress[appState.activeSpeakerId]?.[appState.activeCondition];
      if (!c) {
        void loadCondition(appState.activeSpeakerId, appState.activeCondition);
      }
      return;
    }
    if (appState.config.speakers.length && appState.config.conditions.length) {
      const spk = appState.config.speakers[0].id;
      const cnd = appState.config.conditions[0].name;
      setActive(spk, cnd);
      void loadCondition(spk, cnd);
    }
  });

  // Playback indicator -----------------------------------------------------
  let unsubPlayback: (() => void) | null = null;
  onMount(() => {
    unsubPlayback = subscribePlayback((s) => {
      isPlaying = s.isPlaying;
    });
  });
  onDestroy(() => {
    unsubPlayback?.();
    unsubPlayback = null;
    stop();
  });

  // Actions ----------------------------------------------------------------
  function playCurrent(): void {
    if (!cond || !cache) return;
    const seg = cond.segments[cond.currentTokenIndex];
    if (!seg) return;
    play(cache.buffer, seg.start, seg.end);
  }

  function playContext(): void {
    if (!cond || !cache) return;
    const seg = cond.segments[cond.currentTokenIndex];
    if (!seg) return;
    const s = Math.max(0, seg.start - 0.5);
    const e = Math.min(cache.buffer.duration, seg.end + 0.5);
    play(cache.buffer, s, e);
  }

  function commitLabelAndAccept(): void {
    if (!cond || !currentSeg) return;
    const text = labelDraft.trim();
    if (!text) {
      pushToast("Enter a label first", "warn");
      focusLabel();
      return;
    }
    // Validate against the stimulus list, case-insensitively — same rule
    // as the original rvAccept (index.html:1181-1186).
    if (
      cond.stimulusList.length > 0 &&
      !cond.stimulusList.some((w) => w.toLowerCase() === text.toLowerCase())
    ) {
      pushToast("Invalid name — must be from stimulus list", "warn");
      focusLabel();
      return;
    }
    // Persist label edit even if unchanged so the anchor is recorded.
    setLabel(cond.currentTokenIndex, text);
    setStatus(cond.currentTokenIndex, "accepted");
    advanceCursorOnAccept(text);
    nextWordSegment();
  }

  function rejectCurrent(): void {
    if (!cond) return;
    setStatus(cond.currentTokenIndex, "rejected");
    nextWordSegment();
  }

  function skipCurrent(): void {
    nextWordSegment();
  }

  function toggleAddMode(): void {
    addMode = !addMode;
  }

  function cancelAddOrModal(): void {
    addMode = false;
  }

  function focusLabel(): void {
    if (!labelInput) return;
    labelInput.focus();
    labelInput.select();
  }

  function blurLabelToWaveform(): void {
    if (!labelInput) return;
    if (document.activeElement === labelInput) {
      labelInput.blur();
    }
    // Hand focus back to the waveform so subsequent hotkeys fire.
    waveformView?.focus?.();
  }

  function pickSuggestion(name: string): void {
    labelDraft = name;
    dropdownOpen = false;
    dropdownIndex = -1;
    // Don't auto-accept; let the user press Enter.
    labelInput?.focus();
  }

  function onLabelInput(): void {
    dropdownOpen = suggestions.length > 0;
    dropdownIndex = -1;
  }

  function onLabelFocus(): void {
    // Auto-select on focus so typing replaces the current label without
    // requiring Ctrl+A. Applies whether focus came from L, click, or Tab.
    labelInput?.select();
    dropdownOpen = suggestions.length > 0;
    dropdownIndex = -1;
  }

  function onLabelKeyDown(e: KeyboardEvent): void {
    if (e.key === "ArrowDown") {
      if (suggestions.length === 0) return;
      e.preventDefault();
      e.stopPropagation();
      dropdownOpen = true;
      dropdownIndex = Math.min(dropdownIndex + 1, suggestions.length - 1);
    } else if (e.key === "ArrowUp") {
      if (suggestions.length === 0) return;
      e.preventDefault();
      e.stopPropagation();
      dropdownOpen = true;
      dropdownIndex = Math.max(dropdownIndex - 1, 0);
    } else if (e.key === "Escape") {
      if (dropdownOpen) {
        e.preventDefault();
        e.stopPropagation();
        dropdownOpen = false;
        dropdownIndex = -1;
      } else {
        // Leave edit mode: hand focus back to the waveform so single-letter
        // hotkeys (R/S/A/L) fire again.
        e.preventDefault();
        e.stopPropagation();
        blurLabelToWaveform();
      }
    } else if (e.key === "Enter") {
      if (dropdownOpen && dropdownIndex >= 0) {
        e.preventDefault();
        e.stopPropagation();
        labelDraft = suggestions[dropdownIndex];
        dropdownOpen = false;
        dropdownIndex = -1;
        return;
      }
      // First Enter while typing completes to the top fuzzy match if the
      // current draft doesn't already exactly match it. Second Enter (now
      // matching) bubbles to the global accept binding.
      if (
        suggestions.length > 0 &&
        labelDraft.trim() !== suggestions[0]
      ) {
        e.preventDefault();
        e.stopPropagation();
        labelDraft = suggestions[0];
        dropdownOpen = false;
        dropdownIndex = -1;
      }
      // Otherwise let Enter bubble — the global Enter binding accepts.
    } else if (e.key === "Tab") {
      // Tab in the label input plays the current segment per 06 doc.
      // The global Tab binding handles this; we just don't preventDefault
      // so it can fire.
    }
  }

  function onLabelBlur(): void {
    // Slight delay so a click on a dropdown item registers before close.
    setTimeout(() => {
      dropdownOpen = false;
    }, 150);
  }

  function onTokenContextMenu(e: MouseEvent): void {
    if (!cond) return;
    e.preventDefault();
    // Right-click on the current token to clear its anchor.
    clearAnchor(cond.currentTokenIndex);
    pushToast("Reverted to automatic label", "info", 1500);
  }

  // Hotkeys ----------------------------------------------------------------
  let offHotkeys: (() => void) | null = null;
  onMount(async () => {
    await tick();
    offHotkeys = registerHotkeys(
      buildReviewHotkeys({
        play: playCurrent,
        accept: () => {
          commitLabelAndAccept();
          blurLabelToWaveform();
        },
        reject: () => {
          rejectCurrent();
          blurLabelToWaveform();
        },
        skip: () => {
          skipCurrent();
          blurLabelToWaveform();
        },
        prev: () => {
          stepCursorBackOnPrev();
          prevWordSegment();
          blurLabelToWaveform();
        },
        next: () => {
          nextWordSegment();
          blurLabelToWaveform();
        },
        toggleAddMode,
        cancelAddOrModal,
        focusLabel,
      }),
      { typingFieldAllowlist: REVIEW_TYPING_ALLOWLIST },
    );
  });
  onDestroy(() => {
    offHotkeys?.();
    offHotkeys = null;
  });

  // Visual helpers ---------------------------------------------------------
  function tokenCounter(): string {
    if (!cond) return "";
    const words: number[] = [];
    for (let i = 0; i < cond.segments.length; i++) {
      if (cond.segments[i].segment_type === "word") words.push(i);
    }
    const pos = words.indexOf(cond.currentTokenIndex);
    if (pos < 0) return "—";
    return `${pos + 1} / ${words.length}`;
  }

  function notInList(): boolean {
    if (!cond) return false;
    const t = labelDraft.trim();
    if (!t) return false;
    if (cond.stimulusList.length === 0) return false;
    return !cond.stimulusList.includes(t);
  }
</script>

<div class="review-shell">
  <ConditionTabs />

  {#if !appState.config}
    <section class="rv-empty">
      <h1>Review</h1>
      <p class="muted">Load an experiment in the Setup step first.</p>
    </section>
  {:else if !cond}
    <section class="rv-empty">
      <h1>Review</h1>
      <p class="muted">Pick a speaker × condition to begin.</p>
    </section>
  {:else}
    <div class="rv-body">
      <div class="rv-main">
        <div class="rv-waveform">
          <WaveformView bind:this={waveformView} bind:addMode bind:addStartSec />
        </div>
        <div class="rv-spectrogram">
          <SpectrogramView bind:addMode bind:addStartSec />
        </div>

        <div class="rv-controls" role="toolbar" aria-label="Review controls">
          <div class="rv-row">
            <span class="lbl">Token</span>
            <span class="mono">{tokenCounter()}</span>
            {#if currentSeg}
              <span class="mono muted">
                {currentSeg.start.toFixed(3)}–{currentSeg.end.toFixed(3)}s
                ({formatMs(currentSeg.duration_ms)})
              </span>
            {/if}
            {#if cond && cond.presentationOrder === "blocked"}
              <span class="lbl" style="margin-left:auto;">Reps</span>
              <input
                class="inp"
                type="number"
                min="1"
                step="1"
                style="width:4rem;"
                value={cond.expectedRepsPerStimulus}
                oninput={(e) => {
                  const v = parseInt((e.currentTarget as HTMLInputElement).value, 10);
                  if (Number.isFinite(v) && v >= 1) setExpectedReps(v);
                }}
                title="How many times the speaker was asked to say each word. Changing it recomputes the automatic labels; labels you set yourself are kept."
              />
            {/if}
            {#if isPlaying}
              <span class="rv-playing" role="status" aria-label="playing">▶ playing</span>
            {/if}
          </div>

          <div class="rv-row">
            <span class="lbl" id="rv-label-label">Label</span>
            <div class="rv-label-wrap">
              <input
                bind:this={labelInput}
                bind:value={labelDraft}
                class="inp rv-label-input"
                type="text"
                aria-labelledby="rv-label-label"
                placeholder={cond.stimulusList.length
                  ? "Type or pick from list…"
                  : "Type label…"}
                oninput={onLabelInput}
                onkeydown={onLabelKeyDown}
                onfocus={onLabelFocus}
                onblur={onLabelBlur}
                oncontextmenu={onTokenContextMenu}
                autocomplete="off"
                spellcheck="false"
              />
              {#if dropdownOpen && suggestions.length > 0}
                <ul class="rv-dropdown" role="listbox">
                  {#each suggestions as s, i (s)}
                    <li
                      role="option"
                      aria-selected={i === dropdownIndex}
                      class="rv-drop-item"
                      class:hl={i === dropdownIndex}
                    >
                      <button
                        type="button"
                        class="rv-drop-btn"
                        onmousedown={(e) => {
                          e.preventDefault();
                          pickSuggestion(s);
                        }}
                      >
                        {s}
                      </button>
                    </li>
                  {/each}
                </ul>
              {/if}
            </div>
            {#if notInList()}
              <span class="rv-warn" title="Not in stimulus list">
                ⚠ not in list
              </span>
            {/if}
          </div>

          <div class="rv-row">
            <button
              type="button"
              class="btn"
              aria-label="Play current segment (Tab)"
              onclick={playCurrent}>▶ Play (Space)</button
            >
            <button
              type="button"
              class="btn"
              aria-label="Play with surrounding context"
              onclick={playContext}>▶ Context</button
            >
            <button
              type="button"
              class="btn"
              aria-label="Stop playback"
              onclick={() => stop()}>■ Stop</button
            >
            <button
              type="button"
              class="btn primary"
              aria-label="Accept current token (Enter)"
              onclick={commitLabelAndAccept}>✓ Accept (Enter)</button
            >
            <button
              type="button"
              class="btn danger"
              aria-label="Reject current token (R)"
              onclick={rejectCurrent}>✕ Reject (R)</button
            >
            <button
              type="button"
              class="btn"
              aria-label="Skip current token (S)"
              onclick={skipCurrent}>→ Skip (S)</button
            >
            <button
              type="button"
              class="btn"
              aria-label="Previous token (Left arrow)"
              onclick={prevWordSegment}>← Prev</button
            >
            <button
              type="button"
              class="btn"
              aria-label="Next token (Right arrow)"
              onclick={nextWordSegment}>Next →</button
            >
            <button
              type="button"
              class="btn"
              class:active={addMode}
              aria-pressed={addMode}
              aria-label="Toggle add token mode (A)"
              onclick={toggleAddMode}>+ Add (A)</button
            >
          </div>

          <div class="rv-hint">
            Space/Tab=play · Enter=accept · R=reject · S=skip · ←/→=prev/next
            · A=add token · L=edit label · Esc=cancel · Scroll=zoom ·
            Shift+drag=pan · right-click label=revert to automatic
          </div>
        </div>
      </div>
      <TokenList />
    </div>
  {/if}
</div>

<style>
  .review-shell {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
  }

  .rv-empty {
    padding: 24px;
    max-width: 720px;
    margin: 0 auto;
  }

  .rv-body {
    display: flex;
    flex: 1;
    min-height: 0;
  }

  .rv-main {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-width: 0;
    min-height: 0;
  }

  .rv-waveform {
    flex: 1 1 0;
    min-height: 140px;
    background: #1e1f23;
    border-bottom: 1px solid var(--border);
    overflow: hidden;
  }

  .rv-spectrogram {
    flex: 1 1 0;
    min-height: 140px;
    background: #12131a;
    border-bottom: 1px solid var(--border);
    overflow: hidden;
  }

  .rv-controls {
    /* Never shrinks, never scrolls off — always visible at the bottom. */
    flex: 0 0 auto;
    padding: 10px 12px;
    background: var(--surface);
    border-top: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .rv-row {
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
  }

  .rv-row .lbl {
    margin-bottom: 0;
  }

  .rv-label-wrap {
    position: relative;
    flex: 1;
    min-width: 200px;
    max-width: 360px;
  }

  .rv-label-input {
    width: 100%;
  }

  .rv-dropdown {
    position: absolute;
    top: 100%;
    left: 0;
    right: 0;
    margin: 2px 0 0;
    padding: 0;
    list-style: none;
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 4px;
    max-height: 180px;
    overflow-y: auto;
    z-index: 20;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
  }

  .rv-drop-item.hl {
    background: rgba(79, 140, 255, 0.2);
  }

  .rv-drop-btn {
    width: 100%;
    background: transparent;
    border: none;
    color: var(--text);
    text-align: left;
    padding: 6px 10px;
    font-family: inherit;
    font-size: 13px;
    cursor: pointer;
  }
  .rv-drop-btn:hover {
    background: rgba(255, 255, 255, 0.05);
    color: var(--text-bright);
  }

  .rv-warn {
    color: var(--warn);
    font-size: 12px;
  }

  .rv-playing {
    color: var(--success);
    font-family: var(--font-mono);
    font-size: 12px;
  }

  .rv-hint {
    color: var(--text-dim);
    font-size: 11px;
    font-family: var(--font-mono);
  }

  .btn.active {
    background: var(--warn);
    color: var(--bg);
    border-color: var(--warn);
  }
</style>
