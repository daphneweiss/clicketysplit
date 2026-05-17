<script lang="ts">
  import { state } from "../lib/store.svelte";
</script>

<section class="placeholder">
  <h1>Review</h1>
  <p class="muted">Review step (task 10).</p>
  {#if state.config}
    <p>
      Loaded experiment:
      <span class="mono">{state.config._experiment_path ?? "(unknown)"}</span>
    </p>
    <p>
      Speakers configured: {state.config.speakers.length} · Conditions configured:
      {state.config.conditions.length}
    </p>
  {:else}
    <p>No config loaded yet.</p>
  {/if}
</section>

<style>
  .placeholder {
    padding: 24px;
    max-width: 720px;
    margin: 0 auto;
  }
</style>
