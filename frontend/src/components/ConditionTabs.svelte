<script lang="ts">
  import { state, setActive, loadCondition } from "../lib/store.svelte";

  interface Pair {
    speakerId: string;
    condition: string;
  }

  // Flatten config's speakers × conditions into one list. Per 06 doc, the
  // tabs are speaker×condition pairs; with one speaker this collapses to
  // just the condition name.
  const pairs = $derived.by((): Pair[] => {
    if (!state.config) return [];
    const out: Pair[] = [];
    for (const spk of state.config.speakers) {
      for (const c of state.config.conditions) {
        out.push({ speakerId: spk.id, condition: c.name });
      }
    }
    return out;
  });

  function isActive(p: Pair): boolean {
    return (
      state.activeSpeakerId === p.speakerId &&
      state.activeCondition === p.condition
    );
  }

  function pickPair(p: Pair): void {
    if (isActive(p)) return;
    setActive(p.speakerId, p.condition);
    void loadCondition(p.speakerId, p.condition);
  }

  function progressLabel(p: Pair): string {
    const cond = state.reviewProgress[p.speakerId]?.[p.condition];
    if (!cond) return "";
    const total = cond.segments.filter((s) => s.segment_type === "word").length;
    if (total === 0) return "";
    const done = cond.segments.filter(
      (s) =>
        s.segment_type === "word" &&
        (s.status === "accepted" || s.status === "rejected"),
    ).length;
    return `${done}/${total}`;
  }
</script>

<nav class="cond-tabs" aria-label="Speaker and condition picker">
  {#if pairs.length === 0}
    <span class="muted">No conditions configured.</span>
  {:else}
    {#each pairs as p (`${p.speakerId}${p.condition}`)}
      <button
        type="button"
        class="cond-tab"
        class:active={isActive(p)}
        onclick={() => pickPair(p)}
        aria-current={isActive(p) ? "true" : undefined}
      >
        <span class="cond-tab-spk">{p.speakerId}</span>
        <span class="cond-tab-sep">·</span>
        <span class="cond-tab-cnd">{p.condition}</span>
        {#if progressLabel(p)}
          <span class="cond-tab-prog">{progressLabel(p)}</span>
        {/if}
      </button>
    {/each}
  {/if}
</nav>

<style>
  .cond-tabs {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    padding: 8px 12px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
  }

  .cond-tab {
    background: var(--surface-2);
    color: var(--text);
    border: 1px solid var(--border);
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 12px;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-family: inherit;
  }

  .cond-tab:hover {
    border-color: var(--accent);
    color: var(--text-bright);
  }

  .cond-tab.active {
    background: var(--accent);
    color: white;
    border-color: var(--accent);
  }

  .cond-tab-spk {
    color: var(--text-dim);
    font-size: 11px;
  }
  .cond-tab.active .cond-tab-spk {
    color: rgba(255, 255, 255, 0.8);
  }

  .cond-tab-sep {
    color: var(--text-dim);
  }
  .cond-tab.active .cond-tab-sep {
    color: rgba(255, 255, 255, 0.55);
  }

  .cond-tab-prog {
    background: rgba(255, 255, 255, 0.1);
    padding: 1px 6px;
    border-radius: 999px;
    font-family: var(--font-mono);
    font-size: 10px;
  }
</style>
