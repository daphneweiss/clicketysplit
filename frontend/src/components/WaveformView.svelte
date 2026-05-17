<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import {
    activeCondState,
    addToken as storeAddToken,
    getAudioCache,
    setBoundary,
    setZoom,
    state as appState,
  } from "../lib/store.svelte";
  import { drawWaveform, hitTestHandles, nearerBoundary } from "../lib/waveform";
  import type { HandleSide } from "../lib/waveform";
  import { play, stop } from "../lib/audio";

  // Props -------------------------------------------------------------------
  let {
    addMode = $bindable(false),
  }: { addMode?: boolean } = $props();

  // Refs --------------------------------------------------------------------
  let canvas = $state<HTMLCanvasElement | undefined>(undefined);
  let wrap = $state<HTMLDivElement | undefined>(undefined);

  // Add-token mode local state ---------------------------------------------
  let addStartSec = $state<number | null>(null);

  // Interaction state -------------------------------------------------------
  type Dragging = { which: HandleSide } | null;
  let dragging: Dragging = null;
  let panning = false;
  let panStartX = 0;
  let panViewStart = 0;

  // Derived helpers ---------------------------------------------------------
  const cond = $derived(activeCondState());
  const cache = $derived.by(() => {
    // Re-look-up whenever audioGeneration bumps or the active condition
    // changes; the LRU itself is plain JS.
    void appState.audioGeneration;
    if (!appState.activeSpeakerId || !appState.activeCondition) return null;
    return getAudioCache(appState.activeSpeakerId, appState.activeCondition);
  });

  function currentSeg(): { idx: number; seg: import("../lib/types").Segment } | null {
    if (!cond) return null;
    const seg = cond.segments[cond.currentTokenIndex];
    if (!seg) return null;
    return { idx: cond.currentTokenIndex, seg };
  }

  function xToTime(x: number): number {
    if (!cond || !canvas) return 0;
    const w = canvas.clientWidth;
    const dur = cond.zoom.endSec - cond.zoom.startSec;
    return cond.zoom.startSec + (x / w) * dur;
  }

  // Drawing -----------------------------------------------------------------
  let dpr = 1;

  function syncCanvasSize(): void {
    if (!canvas || !wrap) return;
    const cssW = wrap.clientWidth;
    const cssH = wrap.clientHeight;
    if (cssW === 0 || cssH === 0) return;
    dpr = window.devicePixelRatio || 1;
    const targetW = Math.floor(cssW * dpr);
    const targetH = Math.floor(cssH * dpr);
    if (canvas.width !== targetW || canvas.height !== targetH) {
      canvas.width = targetW;
      canvas.height = targetH;
      canvas.style.width = `${cssW}px`;
      canvas.style.height = `${cssH}px`;
    }
  }

  function redraw(): void {
    if (!canvas || !cond || !cache) return;
    syncCanvasSize();
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    // Drawing math is in CSS pixels; scale once at the start.
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const widthCss = canvas.width / dpr;
    const heightCss = canvas.height / dpr;
    drawWaveform(
      ctx,
      widthCss,
      heightCss,
      cache.buffer,
      cache.envelope,
      cond.zoom.startSec,
      cond.zoom.endSec,
      cond.segments,
      {
        currentSegmentIdx: cond.currentTokenIndex,
        addStartSec,
      },
    );
  }

  // Re-render on any reactive dependency change.
  $effect(() => {
    void cond?.zoom.startSec;
    void cond?.zoom.endSec;
    void cond?.currentTokenIndex;
    void cond?.segments;
    void cond?.segments.length;
    void cache;
    void addStartSec;
    redraw();
  });

  // Resize observer for fluid canvas sizing.
  let resizeObs: ResizeObserver | null = null;
  onMount(() => {
    if (wrap) {
      resizeObs = new ResizeObserver(() => redraw());
      resizeObs.observe(wrap);
    }
    // Focus the wrap on mount so wheel/arrow keys work immediately.
    wrap?.focus();
  });
  onDestroy(() => {
    resizeObs?.disconnect();
    resizeObs = null;
  });

  // Mouse handlers ---------------------------------------------------------
  function onMouseDown(e: MouseEvent): void {
    if (!canvas || !cond || !cache) return;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const t = clamp(xToTime(x), 0, cache.buffer.duration);

    // Add-token mode: two clicks set start, then end.
    if (addMode) {
      if (addStartSec === null) {
        addStartSec = t;
      } else {
        const s = Math.min(addStartSec, t);
        const en = Math.max(addStartSec, t);
        storeAddToken(s, en);
        addStartSec = null;
        addMode = false;
      }
      return;
    }

    // Shift+drag (or middle-click) = pan.
    if (e.shiftKey || e.button === 1) {
      panning = true;
      panStartX = x;
      panViewStart = cond.zoom.startSec;
      e.preventDefault();
      return;
    }

    const cur = currentSeg();
    if (!cur) return;
    // First, see if the click is on a handle.
    const widthCss = canvas.clientWidth;
    const hit = hitTestHandles(
      cur.seg,
      x,
      cond.zoom.startSec,
      cond.zoom.endSec,
      widthCss,
    );
    if (hit) {
      dragging = { which: hit.side };
      return;
    }
    // Otherwise, snap the nearer boundary to the click and enter drag.
    const which = nearerBoundary(cur.seg, t);
    setBoundary(cur.idx, which, t);
    dragging = { which };
  }

  function onMouseMove(e: MouseEvent): void {
    if (!canvas || !cond || !cache) return;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    if (dragging) {
      const t = clamp(xToTime(x), 0, cache.buffer.duration);
      const cur = currentSeg();
      if (!cur) return;
      setBoundary(cur.idx, dragging.which, t);
      return;
    }
    if (panning) {
      const w = canvas.clientWidth;
      const dur = cond.zoom.endSec - cond.zoom.startSec;
      const dx = ((x - panStartX) / w) * dur;
      let newStart = Math.max(0, panViewStart - dx);
      const duration = cache.buffer.duration;
      if (newStart + dur > duration) newStart = Math.max(0, duration - dur);
      setZoom(newStart, newStart + dur);
      return;
    }
    // Hover affordance.
    const cur = currentSeg();
    if (cur && wrap) {
      const widthCss = canvas.clientWidth;
      const hit = hitTestHandles(
        cur.seg,
        x,
        cond.zoom.startSec,
        cond.zoom.endSec,
        widthCss,
      );
      wrap.style.cursor = addMode ? "crosshair" : hit ? "ew-resize" : "pointer";
    }
  }

  function endDrags(): void {
    dragging = null;
    panning = false;
  }

  function onWheel(e: WheelEvent): void {
    if (!canvas || !cond || !cache) return;
    e.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const w = canvas.clientWidth;
    const ratio = x / w;
    const mouseT = cond.zoom.startSec + ratio * (cond.zoom.endSec - cond.zoom.startSec);
    const factor = e.deltaY > 0 ? 1 / 1.2 : 1.2;
    const oldDur = cond.zoom.endSec - cond.zoom.startSec;
    const audioDur = cache.buffer.duration;
    // 20 ms min, full audio max.
    let newDur = Math.max(0.02, Math.min(audioDur, oldDur / factor));
    let newStart = Math.max(0, mouseT - ratio * newDur);
    if (newStart + newDur > audioDur) newStart = Math.max(0, audioDur - newDur);
    setZoom(newStart, newStart + newDur);
  }

  function cancelAdd(): void {
    addMode = false;
    addStartSec = null;
  }

  // Expose a play helper consumers can call (Review owns the keybindings).
  export function playCurrentSegment(): void {
    if (!cond || !cache) return;
    const seg = cond.segments[cond.currentTokenIndex];
    if (!seg) return;
    play(cache.buffer, seg.start, seg.end);
  }

  export function playContext(): void {
    if (!cond || !cache) return;
    const seg = cond.segments[cond.currentTokenIndex];
    if (!seg) return;
    const s = Math.max(0, seg.start - 0.5);
    const e = Math.min(cache.buffer.duration, seg.end + 0.5);
    play(cache.buffer, s, e);
  }

  export function stopPlayback(): void {
    stop();
  }

  function onKeyDown(e: KeyboardEvent): void {
    // Only handle Escape locally — the Review component owns the wider
    // hotkey map; we just need to ESC out of add mode.
    if (e.key === "Escape" && addMode) {
      e.preventDefault();
      cancelAdd();
    }
  }

  function clamp(v: number, lo: number, hi: number): number {
    return Math.min(hi, Math.max(lo, v));
  }
</script>

<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<div
  class="wf-wrap"
  bind:this={wrap}
  tabindex="0"
  role="application"
  aria-label="Waveform — scroll to zoom, shift+drag to pan, click handles to adjust boundaries"
  onmousedown={onMouseDown}
  onmousemove={onMouseMove}
  onmouseup={endDrags}
  onmouseleave={endDrags}
  onwheel={onWheel}
  onkeydown={onKeyDown}
>
  {#if !cond}
    <div class="wf-empty">Pick a speaker × condition to begin.</div>
  {:else if cond.loading}
    <div class="wf-empty">Loading audio…</div>
  {:else if cond.loadError}
    <div class="wf-error">{cond.loadError}</div>
  {:else if !cache}
    <div class="wf-empty">Decoding audio…</div>
  {:else}
    <canvas bind:this={canvas} class="wf-canvas"></canvas>
    {#if addMode}
      <div class="wf-banner" role="status">
        Add token mode — click to set
        {addStartSec === null ? "start" : "end"} boundary, Esc to cancel
      </div>
    {/if}
  {/if}
</div>

<style>
  .wf-wrap {
    position: relative;
    width: 100%;
    height: 100%;
    min-height: 200px;
    background: #1e1f23;
    outline: none;
    overflow: hidden;
  }

  .wf-wrap:focus-visible {
    box-shadow: inset 0 0 0 2px var(--accent);
  }

  .wf-canvas {
    display: block;
    width: 100%;
    height: 100%;
  }

  .wf-empty,
  .wf-error {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--text-dim);
    font-size: 13px;
  }

  .wf-error {
    color: var(--danger);
  }

  .wf-banner {
    position: absolute;
    top: 8px;
    left: 50%;
    transform: translateX(-50%);
    background: rgba(74, 222, 128, 0.2);
    border: 1px solid #4ade80;
    color: #4ade80;
    padding: 4px 10px;
    border-radius: 4px;
    font-size: 12px;
    pointer-events: none;
  }
</style>
