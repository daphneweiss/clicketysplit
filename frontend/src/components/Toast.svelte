<script lang="ts">
  import { dismissToast, state } from "../lib/store.svelte";
</script>

<div class="toast-stack" aria-live="polite">
  {#each state.toasts as t (t.id)}
    <div class="toast toast-{t.kind}" role="status">
      <span class="toast-msg">{t.message}</span>
      <button
        type="button"
        class="toast-close"
        aria-label="Dismiss"
        onclick={() => dismissToast(t.id)}
      >
        x
      </button>
    </div>
  {/each}
</div>

<style>
  .toast-stack {
    position: fixed;
    bottom: 16px;
    right: 16px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    z-index: 1000;
    max-width: 380px;
  }

  .toast {
    background: var(--surface);
    border: 1px solid var(--border);
    border-left-width: 4px;
    border-radius: 4px;
    padding: 10px 12px;
    display: flex;
    align-items: flex-start;
    gap: 10px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
    animation: slide-in 0.18s ease-out;
  }

  .toast-info {
    border-left-color: var(--accent);
  }
  .toast-success {
    border-left-color: var(--success);
  }
  .toast-warn {
    border-left-color: var(--warn);
  }
  .toast-error {
    border-left-color: var(--danger);
  }

  .toast-msg {
    flex: 1;
    font-size: 13px;
    color: var(--text-bright);
    word-break: break-word;
  }

  .toast-close {
    background: transparent;
    border: none;
    color: var(--text-dim);
    cursor: pointer;
    padding: 0 4px;
    font-size: 14px;
  }
  .toast-close:hover {
    color: var(--text-bright);
  }

  @keyframes slide-in {
    from {
      transform: translateY(10px);
      opacity: 0;
    }
    to {
      transform: translateY(0);
      opacity: 1;
    }
  }
</style>
