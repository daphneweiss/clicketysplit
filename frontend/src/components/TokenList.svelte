<script lang="ts">
  import {
    activeCondState,
    setCurrentTokenIndex,
    state,
  } from "../lib/store.svelte";
  import type { Segment } from "../lib/types";
  import { formatMs } from "../lib/format";

  // Re-evaluates whenever any field on the active condition changes.
  const cond = $derived(activeCondState());

  // Svelte action: when `current` toggles to true, scroll this row into
  // view. `block:"nearest"` is non-scrolling when the row is already in
  // view, so there's no jitter for currents in the middle of the visible
  // window. Implemented as an action (not a $effect/querySelector loop)
  // so it runs only on the row that actually becomes current, with no
  // global reactivity hooks.
  function autoScrollWhenCurrent(node: HTMLElement, current: boolean) {
    if (current) {
      node.scrollIntoView({ block: "nearest" });
    }
    return {
      update(nowCurrent: boolean) {
        if (nowCurrent) {
          node.scrollIntoView({ block: "nearest" });
        }
      },
    };
  }

  function isCurrent(idx: number): boolean {
    return !!cond && cond.currentTokenIndex === idx;
  }

  function rowFlash(wordIndex: number): boolean {
    return cond?.diffFlashWordIndices.includes(wordIndex) ?? false;
  }

  function statusIcon(seg: Segment): string {
    if (seg.segment_type !== "word") return "·";
    switch (seg.status) {
      case "accepted":
        return "✓";
      case "rejected":
        return "✕";
      case "intro":
        return "i";
      default:
        return "○";
    }
  }

  /** word_index of a segment among word-typed segments (or -1). */
  function wordIndexOf(idx: number): number {
    if (!cond) return -1;
    let n = 0;
    for (let i = 0; i < idx; i++) {
      if (cond.segments[i].segment_type === "word") n++;
    }
    return cond.segments[idx]?.segment_type === "word" ? n : -1;
  }

  function jumpTo(idx: number): void {
    setCurrentTokenIndex(idx);
  }
</script>

<aside class="token-list" aria-label="Token list">
  {#if !cond}
    <p class="muted">No condition loaded.</p>
  {:else}
    <header class="tl-header">
      <span class="tl-title">Tokens</span>
      <span class="tl-count">{cond.segments.length}</span>
    </header>
    <ol class="tl-rows" role="listbox">
      {#each cond.segments as seg, i (i)}
        {@const wi = wordIndexOf(i)}
        <li
          class="tl-row"
          class:current={isCurrent(i)}
          class:flash={rowFlash(wi)}
          class:rejected={seg.status === "rejected"}
          class:accepted={seg.status === "accepted"}
          class:noise={seg.segment_type === "short_noise" || seg.segment_type === "crosstalk"}
          class:intro={seg.segment_type === "intro" || seg.status === "intro"}
          use:autoScrollWhenCurrent={isCurrent(i)}
        >
          <button
            type="button"
            class="tl-row-btn"
            aria-current={isCurrent(i) ? "true" : undefined}
            onclick={() => jumpTo(i)}
          >
            <span class="tl-status" aria-hidden="true">{statusIcon(seg)}</span>
            <span class="tl-label">
              {#if seg.assigned_name}
                <span class="tl-name" class:strike={seg.status === "rejected"}>
                  {seg.assigned_name}
                </span>
              {:else}
                <span class="tl-empty">—</span>
              {/if}
              {#if seg.assigned_name && cond.stimulusList.length > 0 && !cond.stimulusList.includes(seg.assigned_name)}
                <span
                  class="tl-warn"
                  title="Label not in stimulus list"
                  aria-label="warning: not in stimulus list"
                >
                  ⚠
                </span>
              {/if}
            </span>
            <span class="tl-meta">
              {#if seg.segment_type !== "word"}
                <span class="tl-type">{seg.segment_type}</span>
              {/if}
              <span class="tl-dur">{formatMs(seg.duration_ms)}</span>
            </span>
          </button>
        </li>
      {/each}
    </ol>
  {/if}
</aside>

<style>
  .token-list {
    display: flex;
    flex-direction: column;
    background: var(--surface);
    border-left: 1px solid var(--border);
    width: 280px;
    min-width: 280px;
    overflow: hidden;
  }

  .tl-header {
    display: flex;
    justify-content: space-between;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    color: var(--text-dim);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .tl-count {
    font-family: var(--font-mono);
  }

  .tl-rows {
    margin: 0;
    padding: 0;
    overflow-y: auto;
    list-style: none;
    flex: 1;
  }

  .tl-row {
    border-bottom: 1px solid var(--border);
  }

  .tl-row.current {
    background: rgba(79, 140, 255, 0.18);
  }

  .tl-row.flash {
    animation: tl-flash 2s ease-out;
  }

  .tl-row.rejected .tl-name {
    text-decoration: line-through;
    color: var(--danger);
  }

  .tl-row.accepted .tl-status {
    color: var(--success);
  }

  .tl-row.noise {
    background: rgba(141, 149, 166, 0.08);
  }

  .tl-row.intro {
    background: rgba(217, 161, 74, 0.1);
  }

  .tl-row-btn {
    width: 100%;
    background: transparent;
    border: none;
    color: var(--text);
    padding: 6px 10px;
    text-align: left;
    cursor: pointer;
    font-family: inherit;
    font-size: 12px;
    display: grid;
    grid-template-columns: 18px 1fr auto;
    align-items: center;
    gap: 8px;
  }

  .tl-row-btn:hover {
    background: rgba(255, 255, 255, 0.04);
  }

  .tl-status {
    font-family: var(--font-mono);
    color: var(--text-dim);
    text-align: center;
  }

  .tl-label {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }

  .tl-name {
    color: var(--text-bright);
  }

  .tl-empty {
    color: var(--text-dim);
    font-style: italic;
  }

  .tl-warn {
    color: var(--warn);
    font-size: 12px;
  }

  .tl-meta {
    color: var(--text-dim);
    font-family: var(--font-mono);
    font-size: 10px;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }

  .tl-type {
    background: var(--surface-2);
    border-radius: 3px;
    padding: 1px 4px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  @keyframes tl-flash {
    0% {
      background: rgba(247, 201, 72, 0.6);
    }
    100% {
      background: transparent;
    }
  }
</style>
