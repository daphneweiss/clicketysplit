<script lang="ts">
  // Step 4 of the wizard: review the selection, kick off /api/export_all,
  // and display per-pair results.
  //
  // The flow is deliberately one-shot — /api/export_all returns a single
  // response with all per-pair results; we don't stream progress. While the
  // request is in flight, every queued pair shows "running"; once the
  // response lands we render ok/error per pair.

  import {
    runExportAll,
    selectedTokenMeanDuration,
    selectionSummary,
    setStep,
    state as appState,
    type PerPairResult,
  } from "../lib/store.svelte";
  import { formatBytes } from "../lib/format";

  // ---- Derived summary across all selected (spk, cond) pairs --------------

  interface Row {
    spk: string;
    cond: string;
    selectedCount: number;
    totalEligible: number;
    unlabeledCount: number;
  }

  const pairs = $derived.by((): Row[] => {
    // Touching `selectedTokens` and `reviewProgress` so $derived re-runs.
    void appState.selectedTokens;
    void appState.reviewProgress;
    const out: Row[] = [];
    for (const spk of Object.keys(appState.selectedTokens)) {
      const spkSlot = appState.selectedTokens[spk];
      for (const cnd of Object.keys(spkSlot)) {
        const s = selectionSummary(spk, cnd);
        if (s.selectedCount === 0) continue;
        out.push({
          spk,
          cond: cnd,
          selectedCount: s.selectedCount,
          totalEligible: s.totalEligible,
          unlabeledCount: s.unlabeledCount,
        });
      }
    }
    out.sort((a, b) =>
      a.spk === b.spk ? a.cond.localeCompare(b.cond) : a.spk.localeCompare(b.spk),
    );
    return out;
  });

  const totalSelected = $derived(
    pairs.reduce((acc, p) => acc + p.selectedCount, 0),
  );

  const totalUnlabeled = $derived(
    pairs.reduce((acc, p) => acc + p.unlabeledCount, 0),
  );

  /** Rough output-size estimate: count * mean_duration_sec * sr * 4 bytes
   *  per sample (float32 WAV is the upper bound; PCM16 is half). The 16 kHz
   *  default keeps the number modest. We show this as "approximate". */
  const estBytes = $derived.by((): number => {
    void appState.selectedTokens;
    const meanDur = selectedTokenMeanDuration();
    if (meanDur <= 0) return 0;
    // Use 16 kHz, 16-bit PCM as the default assumption (2 bytes/sample) —
    // soundfile defaults to PCM_16 for .wav writes. This errs slightly low
    // for float32 outputs.
    const sr = 16000;
    const bytesPerSample = 2;
    return Math.round(totalSelected * meanDur * sr * bytesPerSample);
  });

  // ---- Actions ------------------------------------------------------------

  async function onExport(): Promise<void> {
    await runExportAll();
  }

  function goBack(): void {
    setStep(3);
  }

  async function copyPath(): Promise<void> {
    const p = displayOutputDir();
    if (!p) return;
    try {
      await navigator.clipboard.writeText(p);
    } catch {
      // Fallback for browsers without async clipboard.
      const ta = document.createElement("textarea");
      ta.value = p;
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
      } catch {
        /* ignore */
      }
      document.body.removeChild(ta);
    }
  }

  // The per-pair output dirs end with "<spk>/<cond>/tokens"; the user
  // probably wants the parent output_root. Strip the last 3 path
  // segments when possible.
  function displayOutputDir(): string {
    const p = appState.exportOutputDir;
    if (!p) return "";
    const trimmed = p.replace(/[/\\]+$/, "");
    const parts = trimmed.split(/[/\\]+/);
    if (parts.length >= 3) {
      return parts.slice(0, parts.length - 3).join("/") || trimmed;
    }
    return trimmed;
  }

  function pairLabel(r: PerPairResult): string {
    return `${r.spk} / ${r.cond}`;
  }

  function statusGlyph(r: PerPairResult): string {
    switch (r.status) {
      case "ok":
        return "✓";
      case "error":
        return "✕";
      default:
        return "⏳";
    }
  }

  function countSkipped(r: PerPairResult): number {
    if (!r.counts?.skipped) return 0;
    let n = 0;
    for (const v of Object.values(r.counts.skipped)) n += v ?? 0;
    return n;
  }

  const totalsAfterExport = $derived.by(() => {
    if (appState.exportState !== "done") return null;
    let ok = 0;
    let err = 0;
    let exported = 0;
    let skipped = 0;
    for (const r of appState.perPairResult) {
      if (r.status === "ok") {
        ok++;
        exported += r.counts?.n_exported ?? 0;
        skipped += countSkipped(r);
      } else if (r.status === "error") {
        err++;
      }
    }
    return { ok, err, exported, skipped };
  });
</script>

<div class="exp-shell">
  {#if !appState.config}
    <section class="exp-empty">
      <h1>Export</h1>
      <p class="muted">Load an experiment in the Setup step first.</p>
    </section>
  {:else}
    <section class="exp-body">
      <header class="exp-top">
        <div>
          <h1>Export</h1>
          <p class="muted">
            Review the selection and write tokens to disk.
          </p>
        </div>
        <button type="button" class="btn" onclick={goBack} disabled={appState.exportState === "running"}>
          ← Back to Select
        </button>
      </header>

      <section class="exp-summary">
        <h2>Selection summary</h2>
        {#if pairs.length === 0}
          <p class="muted">
            No tokens selected. Go back to <button class="linklike" onclick={goBack}>Select</button>
            and pick at least one.
          </p>
        {:else}
          <table class="simple exp-table">
            <thead>
              <tr>
                <th>Speaker</th>
                <th>Condition</th>
                <th class="num">Selected</th>
                <th class="num">Eligible</th>
                <th class="num">Unlabeled</th>
              </tr>
            </thead>
            <tbody>
              {#each pairs as p (`${p.spk}\x1f${p.cond}`)}
                <tr>
                  <td class="mono">{p.spk}</td>
                  <td class="mono">{p.cond}</td>
                  <td class="num mono">{p.selectedCount}</td>
                  <td class="num mono">{p.totalEligible}</td>
                  <td class="num mono">
                    {#if p.unlabeledCount > 0}
                      <span class="warnnum">{p.unlabeledCount}</span>
                    {:else}
                      0
                    {/if}
                  </td>
                </tr>
              {/each}
              <tr class="exp-total">
                <td colspan="2"><strong>Total</strong></td>
                <td class="num mono"><strong>{totalSelected}</strong></td>
                <td class="num mono">—</td>
                <td class="num mono">
                  {#if totalUnlabeled > 0}
                    <span class="warnnum">{totalUnlabeled}</span>
                  {:else}
                    0
                  {/if}
                </td>
              </tr>
            </tbody>
          </table>
          <p class="muted small">
            Estimated output size: ~{formatBytes(estBytes)} (approximate, assuming 16 kHz PCM16 WAV).
          </p>
        {/if}
      </section>

      <section class="exp-action">
        <button
          type="button"
          class="btn primary big"
          onclick={onExport}
          disabled={appState.exportState === "running" || pairs.length === 0}
        >
          {#if appState.exportState === "running"}
            Exporting…
          {:else if appState.exportState === "done"}
            Export all again
          {:else}
            Export all
          {/if}
        </button>
        <span class="muted small">
          Writes <span class="mono">tokens/</span>,
          <span class="mono">token_manifest.json</span> and
          <span class="mono">tokens.csv</span> under each pair's output dir.
        </span>
      </section>

      {#if appState.perPairResult.length > 0}
        <section class="exp-results">
          <h2>Per-pair results</h2>
          <ul class="exp-result-list">
            {#each appState.perPairResult as r (`${r.spk}\x1f${r.cond}`)}
              <li class="exp-result-row exp-{r.status}">
                <span class="exp-glyph" aria-hidden="true">{statusGlyph(r)}</span>
                <span class="mono">{pairLabel(r)}</span>
                <span class="exp-result-detail">
                  {#if r.status === "running"}
                    running…
                  {:else if r.status === "ok"}
                    {r.counts?.n_exported ?? 0} tokens written
                    {#if countSkipped(r) > 0}
                      <span class="warn"> · {countSkipped(r)} skipped</span>
                    {/if}
                  {:else if r.status === "error"}
                    <span class="err">
                      {r.error?.code ?? "error"}: {r.error?.message ?? "unknown error"}
                    </span>
                  {/if}
                </span>
              </li>
            {/each}
          </ul>
        </section>
      {/if}

      {#if totalsAfterExport}
        <section class="exp-finished">
          <h2>Done</h2>
          <p>
            <strong>{totalsAfterExport.exported}</strong> tokens written across
            <strong>{totalsAfterExport.ok}</strong> pair{totalsAfterExport.ok === 1 ? "" : "s"}.
            {#if totalsAfterExport.skipped > 0}
              <span class="warn">{totalsAfterExport.skipped} skipped.</span>
            {/if}
            {#if totalsAfterExport.err > 0}
              <span class="err">{totalsAfterExport.err} pair{totalsAfterExport.err === 1 ? "" : "s"} failed.</span>
            {/if}
          </p>
          {#if displayOutputDir()}
            <div class="exp-path-row">
              <span class="muted small">Output directory:</span>
              <code class="mono exp-path">{displayOutputDir()}</code>
              <button type="button" class="btn small" onclick={copyPath}>Copy path</button>
            </div>
            <p class="muted small">
              Open this directory in your file manager — browsers can't open
              native folder windows from a web app.
            </p>
          {/if}
        </section>
      {/if}
    </section>
  {/if}
</div>

<style>
  .exp-shell {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
  }

  .exp-empty {
    padding: 24px;
    max-width: 720px;
    margin: 0 auto;
  }

  .exp-body {
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

  .exp-top {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
  }

  .exp-top h1 {
    margin: 0 0 4px;
  }

  .exp-summary,
  .exp-action,
  .exp-results,
  .exp-finished {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 14px 16px;
  }

  .exp-summary h2,
  .exp-results h2,
  .exp-finished h2 {
    margin: 0 0 8px;
    font-size: 13px;
  }

  .exp-table {
    width: 100%;
  }

  .exp-table .num {
    text-align: right;
  }

  .exp-total td {
    border-top: 2px solid var(--border);
    border-bottom: none;
    padding-top: 8px;
  }

  .warnnum {
    color: var(--warn);
  }

  .exp-action {
    display: flex;
    align-items: center;
    gap: 14px;
    flex-wrap: wrap;
  }

  .btn.big {
    padding: 10px 22px;
    font-size: 14px;
  }

  .exp-result-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .exp-result-row {
    display: grid;
    grid-template-columns: 18px 220px 1fr;
    align-items: center;
    gap: 10px;
    padding: 6px 8px;
    border-radius: 4px;
    background: var(--surface-2);
    font-size: 12px;
  }

  .exp-glyph {
    text-align: center;
    font-family: var(--font-mono);
  }

  .exp-ok .exp-glyph {
    color: var(--success);
  }

  .exp-error .exp-glyph {
    color: var(--danger);
  }

  .exp-running .exp-glyph {
    color: var(--warn);
  }

  .exp-result-detail {
    color: var(--text);
  }

  .warn {
    color: var(--warn);
  }
  .err {
    color: var(--danger);
  }

  .exp-path-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 6px 0;
    flex-wrap: wrap;
  }

  .exp-path {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 4px 8px;
    user-select: all;
    word-break: break-all;
  }

  .linklike {
    background: none;
    border: none;
    color: var(--accent-bright);
    cursor: pointer;
    text-decoration: underline;
    font: inherit;
    padding: 0;
  }

  .small {
    font-size: 11px;
  }

  .btn.small {
    padding: 4px 8px;
    font-size: 11px;
  }
</style>
