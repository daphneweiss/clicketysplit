<script lang="ts">
  import { setStep, state } from "../lib/store.svelte";

  type StepNum = 1 | 2 | 3 | 4;

  interface StepInfo {
    n: StepNum;
    label: string;
  }

  const steps: StepInfo[] = [
    { n: 1, label: "Setup" },
    { n: 2, label: "Review" },
    { n: 3, label: "Select" },
    { n: 4, label: "Export" },
  ];

  function jump(step: StepNum): void {
    // Jumping forward without a loaded config doesn't make sense; the later
    // steps need detection output. Stay put with a hint.
    if (step > 1 && !state.config) return;
    setStep(step);
  }
</script>

<nav class="step-nav" aria-label="Wizard progress">
  {#each steps as s}
    <button
      type="button"
      class="step-tab"
      class:active={state.step === s.n}
      class:disabled={s.n > 1 && !state.config}
      disabled={s.n > 1 && !state.config}
      onclick={() => jump(s.n)}
      aria-current={state.step === s.n ? "step" : undefined}
    >
      <span class="step-num">{s.n}</span>
      <span class="step-label">{s.label}</span>
    </button>
  {/each}
</nav>

<style>
  .step-nav {
    display: flex;
    gap: 0;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 0 12px;
  }

  .step-tab {
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: var(--text-dim);
    padding: 12px 16px;
    cursor: pointer;
    font-size: 13px;
    font-family: inherit;
    display: flex;
    gap: 8px;
    align-items: center;
  }

  .step-tab:hover {
    color: var(--text-bright);
  }

  .step-tab.active {
    color: var(--text-bright);
    border-bottom-color: var(--accent);
  }

  .step-tab.disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .step-num {
    background: var(--surface-2);
    border-radius: 50%;
    width: 22px;
    height: 22px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 11px;
  }

  .step-tab.active .step-num {
    background: var(--accent);
    color: white;
  }
</style>
