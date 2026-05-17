<script lang="ts">
  import StepNav from "./components/StepNav.svelte";
  import Toast from "./components/Toast.svelte";
  import Setup from "./routes/Setup.svelte";
  import Review from "./routes/Review.svelte";
  import Select from "./routes/Select.svelte";
  import Export from "./routes/Export.svelte";
  import { boot, state } from "./lib/store.svelte";

  // Boot once on mount: pull capabilities, then config (if the server was
  // launched with --experiment), then the per-experiment session JSON.
  // See store.svelte.ts boot() for the exact sequence.
  $effect(() => {
    void boot();
  });
</script>

<div class="app-shell">
  <header class="app-header">
    <span class="brand">clicketysplit</span>
    <span class="header-divider">·</span>
    <span class="muted">
      {#if state.config && (state.config.name || state.configPath)}
        {state.config.name || state.configPath}
      {:else}
        no experiment loaded
      {/if}
    </span>
  </header>

  <StepNav />

  <main class="app-body">
    {#if state.step === 1}
      <Setup />
    {:else if state.step === 2}
      <Review />
    {:else if state.step === 3}
      <Select />
    {:else if state.step === 4}
      <Export />
    {/if}
  </main>

  <footer class="status-bar">
    <span>
      {#if state.configPath}
        Experiment: <span class="mono">{state.configPath}</span>
      {:else}
        Experiment: (none)
      {/if}
    </span>
    <span>
      {#if state.capabilities}
        Detectors: {state.capabilities.detectors.join(", ") || "(none)"} ·
        denoise: {state.capabilities.denoise_available ? "yes" : "no"} ·
        textgrid: {state.capabilities.textgrid_available ? "yes" : "no"}
      {:else}
        Capabilities loading…
      {/if}
    </span>
  </footer>

  <Toast />
</div>

<style>
  .app-header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 10px 16px;
    display: flex;
    gap: 8px;
    align-items: baseline;
  }

  .brand {
    color: var(--text-bright);
    font-weight: 600;
    letter-spacing: 0.02em;
  }

  .header-divider {
    color: var(--text-dim);
  }

  .app-body {
    flex: 1;
    overflow-y: auto;
  }
</style>
