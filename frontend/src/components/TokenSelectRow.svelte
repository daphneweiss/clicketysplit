<script lang="ts">
  // A single row in Select.svelte. Either:
  //   - An "Unlabeled" row: checkbox disabled, "Go to Review" link present.
  //   - A per-stimulus row: checkbox bound to selection, 1-indexed token
  //     number within label, play button, duration, start time.
  //
  // Kept dumb on purpose — Select.svelte owns the store wiring and passes
  // callbacks in. Stimulus-list warnings are rendered when the segment's
  // assigned_name isn't in the loaded stimulus list.

  import type { Segment } from "../lib/types";
  import { formatMs } from "../lib/format";

  interface Props {
    segment: Segment;
    segmentIdx: number;
    /** 1-based index within this row's assigned_name, or null for unlabeled. */
    tokenIndex: number | null;
    /** Current selection state — irrelevant when `disabled`. */
    selected: boolean;
    /** True for the Unlabeled group (checkbox disabled). */
    disabled?: boolean;
    /** True when assigned_name is non-empty but not in the stimulus list. */
    notInList?: boolean;
    /** Click handlers. */
    onToggle?: () => void;
    onPlay?: () => void;
    onGoToReview?: () => void;
    isPlaying?: boolean;
  }

  const {
    segment,
    segmentIdx: _segmentIdx,
    tokenIndex,
    selected,
    disabled = false,
    notInList = false,
    onToggle,
    onPlay,
    onGoToReview,
    isPlaying = false,
  }: Props = $props();

  function statusBadge(): string {
    switch (segment.status) {
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
</script>

<div
  class="tsr"
  class:disabled
  class:rejected={segment.status === "rejected"}
  class:pending={segment.status === "pending"}
>
  <label class="tsr-check">
    <input
      type="checkbox"
      checked={selected}
      disabled={disabled}
      onchange={() => onToggle?.()}
      aria-label={disabled
        ? "Unlabeled token — cannot be exported"
        : `Select token ${tokenIndex}`}
    />
  </label>

  <span class="tsr-idx" aria-hidden="true">
    {#if tokenIndex !== null}
      #{tokenIndex}
    {:else}
      —
    {/if}
  </span>

  <button
    type="button"
    class="tsr-play"
    onclick={() => onPlay?.()}
    aria-label="Play token"
    title="Play"
  >
    {isPlaying ? "■" : "▶"}
  </button>

  <span class="tsr-meta">
    <span class="tsr-status" title="Status: {segment.status}">{statusBadge()}</span>
    <span class="tsr-dur">{formatMs(segment.duration_ms)}</span>
    <span class="tsr-start">@ {segment.start.toFixed(3)}s</span>
  </span>

  {#if notInList}
    <span class="tsr-warn" title="Label not in stimulus list">⚠ not in list</span>
  {/if}

  {#if disabled && onGoToReview}
    <button
      type="button"
      class="tsr-review"
      onclick={() => onGoToReview?.()}
      aria-label="Jump to this token in Review"
    >
      Go to Review →
    </button>
  {/if}
</div>

<style>
  .tsr {
    display: grid;
    grid-template-columns: auto 40px 32px 1fr auto auto;
    gap: 8px;
    align-items: center;
    padding: 4px 8px;
    border-bottom: 1px solid var(--border);
    font-size: 13px;
  }

  .tsr:hover {
    background: rgba(255, 255, 255, 0.03);
  }

  .tsr.disabled {
    color: var(--text-dim);
    background: rgba(217, 161, 74, 0.04);
  }

  .tsr.disabled .tsr-idx,
  .tsr.disabled .tsr-meta {
    opacity: 0.7;
  }

  .tsr.rejected .tsr-meta {
    text-decoration: line-through;
    opacity: 0.7;
  }

  .tsr-check {
    display: flex;
    align-items: center;
  }

  .tsr-check input {
    cursor: pointer;
    width: 16px;
    height: 16px;
    accent-color: var(--accent);
  }

  .tsr-check input:disabled {
    cursor: not-allowed;
  }

  .tsr-idx {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--text-dim);
    text-align: right;
  }

  .tsr-play {
    background: var(--surface-2);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 4px;
    padding: 2px 6px;
    font-family: inherit;
    cursor: pointer;
    font-size: 12px;
  }
  .tsr-play:hover {
    border-color: var(--accent);
    color: var(--text-bright);
  }

  .tsr-meta {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--text-dim);
  }

  .tsr-status {
    width: 14px;
    text-align: center;
  }

  .tsr.pending .tsr-status {
    color: var(--text-dim);
  }
  .tsr.rejected .tsr-status {
    color: var(--danger);
  }

  .tsr-warn {
    color: var(--warn);
    font-size: 11px;
  }

  .tsr-review {
    background: transparent;
    border: 1px solid var(--accent);
    color: var(--accent-bright);
    border-radius: 4px;
    padding: 2px 8px;
    font-family: inherit;
    font-size: 11px;
    cursor: pointer;
  }
  .tsr-review:hover {
    background: var(--accent);
    color: white;
  }
</style>
