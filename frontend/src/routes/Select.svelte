<script lang="ts">
  // Step 3 of the wizard: pick which tokens to export.
  //
  // Layout:
  //   - ConditionTabs at top.
  //   - Active condition body:
  //       1. "Unlabeled" group (read-only, gray; lets the user jump back to
  //          Review to fix them).
  //       2. Per-stimulus groups in stimulus_list order, with free-text
  //          labels at the end. Each group has a bulk select-all /
  //          deselect-all and a per-row checkbox.
  //   - Bottom bar: condition summary + "Next: Export" button.
  //
  // Defaults per CONTRACT_NOTES C4: when a condition first enters Select,
  // every word-typed segment with status=='accepted' AND non-empty
  // assigned_name starts selected.

  import { onDestroy, onMount } from "svelte";
  import ConditionTabs from "../components/ConditionTabs.svelte";
  import TokenSelectRow from "../components/TokenSelectRow.svelte";
  import {
    activeCondState,
    ensureSelectionInitialized,
    getAudioCache,
    groupTokensByLabel,
    loadCondition,
    selectionSummary,
    setActive,
    setAllSelection,
    setCurrentTokenIndex,
    setStep,
    setWordSelection,
    state as appState,
    toggleTokenSelection,
  } from "../lib/store.svelte";
  import { play, stop, subscribePlayback } from "../lib/audio";

  const cond = $derived(activeCondState());
  const spk = $derived(appState.activeSpeakerId);
  const cnd = $derived(appState.activeCondition);

  // Audio buffer for the play buttons.
  const cache = $derived.by(() => {
    void appState.audioGeneration;
    if (!spk || !cnd) return null;
    return getAudioCache(spk, cnd);
  });

  // Lazily seed selection defaults when the active condition is loaded.
  $effect(() => {
    if (!spk || !cnd || !cond || cond.segments.length === 0) return;
    ensureSelectionInitialized(spk, cnd);
  });

  // On enter: if there's no active condition picked, default to the first
  // configured pair so the page isn't empty.
  $effect(() => {
    if (!appState.config) return;
    if (spk && cnd) {
      const c = appState.reviewProgress[spk]?.[cnd];
      if (!c) {
        void loadCondition(spk, cnd);
      }
      return;
    }
    if (appState.config.speakers.length && appState.config.conditions.length) {
      const s = appState.config.speakers[0].id;
      const c = appState.config.conditions[0].name;
      setActive(s, c);
      void loadCondition(s, c);
    }
  });

  // Playback state (for the play buttons' icon).
  let isPlayingSegmentIdx = $state<number | null>(null);
  let unsub: (() => void) | null = null;
  onMount(() => {
    unsub = subscribePlayback((s) => {
      if (!s.isPlaying) isPlayingSegmentIdx = null;
    });
  });
  onDestroy(() => {
    unsub?.();
    unsub = null;
    stop();
  });

  // ---- Derived structures ------------------------------------------------

  const grouping = $derived.by(() => {
    if (!cond) return null;
    return groupTokensByLabel(cond.segments);
  });

  /** Order groups by stimulus_list order; any free-text labels go at the
   *  end in first-seen order. */
  const orderedLabels = $derived.by((): string[] => {
    if (!grouping || !cond) return [];
    const stimList = cond.stimulusList ?? [];
    const knownInList: string[] = [];
    const freeText: string[] = [];
    const present = new Set(grouping.labelOrder);
    for (const name of stimList) {
      if (present.has(name)) knownInList.push(name);
    }
    for (const name of grouping.labelOrder) {
      if (!stimList.includes(name)) freeText.push(name);
    }
    return [...knownInList, ...freeText];
  });

  const summary = $derived.by(() => {
    if (!spk || !cnd) {
      return { selectedCount: 0, totalEligible: 0, unlabeledCount: 0, perWord: {} };
    }
    // Touch selectedTokens proxy so this derivation re-runs when toggles
    // happen.
    void appState.selectedTokens;
    return selectionSummary(spk, cnd);
  });

  function labelInStimulusList(name: string): boolean {
    if (!cond || cond.stimulusList.length === 0) return true;
    return cond.stimulusList.includes(name);
  }

  function isSelected(name: string, tokenIndex: number): boolean {
    if (!spk || !cnd) return false;
    return !!appState.selectedTokens[spk]?.[cnd]?.[name]?.[tokenIndex];
  }

  function playSegment(segmentIdx: number): void {
    if (!cond || !cache) return;
    const seg = cond.segments[segmentIdx];
    if (!seg) return;
    isPlayingSegmentIdx = segmentIdx;
    play(cache.buffer, seg.start, seg.end);
  }

  function goToReview(segmentIdx: number): void {
    setCurrentTokenIndex(segmentIdx);
    setStep(2);
  }

  function onToggle(name: string, tokenIndex: number): void {
    if (!spk || !cnd) return;
    toggleTokenSelection(spk, cnd, name, tokenIndex);
  }

  function onSelectAll(name: string): void {
    if (!spk || !cnd) return;
    setWordSelection(spk, cnd, name, true);
  }

  function onDeselectAll(name: string): void {
    if (!spk || !cnd) return;
    setWordSelection(spk, cnd, name, false);
  }

  function onSelectAllCondition(): void {
    if (!spk || !cnd) return;
    setAllSelection(spk, cnd, true);
  }

  function onDeselectAllCondition(): void {
    if (!spk || !cnd) return;
    setAllSelection(spk, cnd, false);
  }

  function goNext(): void {
    setStep(4);
  }
</script>

<div class="sel-shell">
  <ConditionTabs />

  {#if !appState.config}
    <section class="sel-empty">
      <h1>Select</h1>
      <p class="muted">Load an experiment in the Setup step first.</p>
    </section>
  {:else if !cond}
    <section class="sel-empty">
      <h1>Select</h1>
      <p class="muted">Pick a speaker × condition to begin.</p>
    </section>
  {:else if cond.loading}
    <section class="sel-empty">
      <h1>Select</h1>
      <p class="muted">Loading segments…</p>
    </section>
  {:else if cond.loadError}
    <section class="sel-empty">
      <h1>Select</h1>
      <p class="warn">{cond.loadError}</p>
    </section>
  {:else}
    <section class="sel-body">
      <header class="sel-top">
        <div class="sel-top-l">
          <h1>Select tokens to export</h1>
          <p class="muted">
            Default: every word with <em>accepted</em> status and a non-empty label.
            Adjust per-token or use the bulk buttons.
          </p>
        </div>
        <div class="sel-top-r">
          <button type="button" class="btn" onclick={onSelectAllCondition}>
            Select all
          </button>
          <button type="button" class="btn" onclick={onDeselectAllCondition}>
            Deselect all
          </button>
        </div>
      </header>

      {#if grouping && grouping.unlabeled.length > 0}
        <section class="sel-group sel-unlabeled">
          <header class="sel-group-hdr">
            <h2>Unlabeled <span class="muted">({grouping.unlabeled.length})</span></h2>
            <span class="muted small">
              Unlabeled tokens can't be exported. Use “Go to Review” to label them.
            </span>
          </header>
          <div class="sel-rows">
            {#each grouping.unlabeled as segIdx (segIdx)}
              <TokenSelectRow
                segment={cond.segments[segIdx]}
                segmentIdx={segIdx}
                tokenIndex={null}
                selected={false}
                disabled={true}
                onPlay={() => playSegment(segIdx)}
                onGoToReview={() => goToReview(segIdx)}
                isPlaying={isPlayingSegmentIdx === segIdx}
              />
            {/each}
          </div>
        </section>
      {/if}

      {#if grouping}
        {#each orderedLabels as name (name)}
          {@const group = grouping.groups[name]}
          {@const perWord = summary.perWord[name] ?? { selected: 0, total: group.length }}
          <section class="sel-group">
            <header class="sel-group-hdr">
              <h2>
                {name}
                {#if !labelInStimulusList(name)}
                  <span class="sel-warn" title="Not in stimulus list">⚠</span>
                {/if}
                <span class="muted">— {perWord.selected} selected / {perWord.total} total</span>
              </h2>
              <div class="sel-group-actions">
                <button
                  type="button"
                  class="btn small"
                  onclick={() => onSelectAll(name)}
                  disabled={perWord.selected === perWord.total}
                >
                  Select all
                </button>
                <button
                  type="button"
                  class="btn small"
                  onclick={() => onDeselectAll(name)}
                  disabled={perWord.selected === 0}
                >
                  Deselect all
                </button>
              </div>
            </header>
            <div class="sel-rows">
              {#each group as g (g.segmentIdx)}
                <TokenSelectRow
                  segment={cond.segments[g.segmentIdx]}
                  segmentIdx={g.segmentIdx}
                  tokenIndex={g.tokenIndex}
                  selected={isSelected(name, g.tokenIndex)}
                  notInList={!labelInStimulusList(name)}
                  onToggle={() => onToggle(name, g.tokenIndex)}
                  onPlay={() => playSegment(g.segmentIdx)}
                  isPlaying={isPlayingSegmentIdx === g.segmentIdx}
                />
              {/each}
            </div>
          </section>
        {/each}
      {/if}

      {#if grouping && grouping.unlabeled.length === 0 && orderedLabels.length === 0}
        <p class="muted">No word-typed segments found in this condition.</p>
      {/if}
    </section>

    <footer class="sel-bar">
      <div class="sel-bar-summary">
        <strong>{summary.selectedCount}</strong>
        tokens selected /
        <strong>{summary.totalEligible}</strong>
        eligible.
        {#if summary.unlabeledCount > 0}
          <span class="muted">
            ({summary.unlabeledCount} unlabeled hidden — won't be exported.)
          </span>
        {/if}
      </div>
      <button
        type="button"
        class="btn primary"
        onclick={goNext}
        disabled={summary.selectedCount === 0}
      >
        Next: Export →
      </button>
    </footer>
  {/if}
</div>

<style>
  .sel-shell {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
  }

  .sel-empty {
    padding: 24px;
    max-width: 720px;
    margin: 0 auto;
  }

  .sel-empty .warn {
    color: var(--warn);
  }

  .sel-body {
    flex: 1;
    overflow-y: auto;
    padding: 16px 20px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    max-width: 980px;
    width: 100%;
    margin: 0 auto;
  }

  .sel-top {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
  }

  .sel-top h1 {
    margin: 0 0 4px;
  }

  .sel-top-r {
    display: flex;
    gap: 8px;
    flex-shrink: 0;
  }

  .sel-group {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
  }

  .sel-group-hdr {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 12px;
    gap: 12px;
    background: var(--surface-2);
    border-bottom: 1px solid var(--border);
    flex-wrap: wrap;
  }

  .sel-group-hdr h2 {
    margin: 0;
    font-size: 14px;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }

  .sel-group-actions {
    display: flex;
    gap: 6px;
  }

  .sel-rows {
    display: flex;
    flex-direction: column;
  }

  .sel-unlabeled {
    border-color: rgba(217, 161, 74, 0.5);
  }
  .sel-unlabeled .sel-group-hdr {
    background: rgba(217, 161, 74, 0.08);
  }

  .sel-warn {
    color: var(--warn);
    font-size: 12px;
  }

  .small {
    font-size: 11px;
  }

  .btn.small {
    padding: 4px 8px;
    font-size: 11px;
  }

  .sel-bar {
    border-top: 1px solid var(--border);
    background: var(--surface);
    padding: 10px 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }

  .sel-bar-summary {
    color: var(--text);
    font-size: 13px;
  }
  .sel-bar-summary strong {
    color: var(--text-bright);
  }
</style>
