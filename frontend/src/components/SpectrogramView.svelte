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
  import { drawSpectrogram } from "../lib/spectrogram";
  import { hitTestHandles, nearerBoundary } from "../lib/waveform";
  import type { HandleSide } from "../lib/waveform";

  let {
    addMode = $bindable(false),
    addStartSec = $bindable<number | null>(null),
  }: { addMode?: boolean; addStartSec?: number | null } = $props();

  let canvas = $state<HTMLCanvasElement | undefined>(undefined);
  let wrap = $state<HTMLDivElement | undefined>(undefined);

  type Dragging = { which: HandleSide } | null;
  let dragging: Dragging = null;
  let panning = false;
  let panStartX = 0;
  let panViewStart = 0;

  const cond = $derived(activeCondState());
  const cache = $derived.by(() => {
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
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const widthCss = canvas.width / dpr;
    const heightCss = canvas.height / dpr;
    const seg = cond.segments[cond.currentTokenIndex];
    drawSpectrogram(
      ctx,
      widthCss,
      heightCss,
      cache.buffer,
      cond.zoom.startSec,
      cond.zoom.endSec,
      {
        segStartSec: seg?.start ?? 0,
        segEndSec: seg?.end ?? 0,
        addStartSec,
      },
    );
  }

  $effect(() => {
    void cond?.zoom.startSec;
    void cond?.zoom.endSec;
    void cond?.currentTokenIndex;
    void cond?.segments;
    void cache;
    void addStartSec;
    redraw();
  });

  let resizeObs: ResizeObserver | null = null;
  onMount(() => {
    if (wrap) {
      resizeObs = new ResizeObserver(() => redraw());
      resizeObs.observe(wrap);
    }
  });
  onDestroy(() => {
    resizeObs?.disconnect();
    resizeObs = null;
  });

  // Mouse handlers — mirror WaveformView so a click on either canvas moves
  // boundaries / pans / zooms the same way. addMode + addStartSec are
  // shared via $bindable so a two-click add can start on one and end on
  // the other.
  function onMouseDown(e: MouseEvent): void {
    if (!canvas || !cond || !cache) return;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const t = clamp(xToTime(x), 0, cache.buffer.duration);

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

    if (e.shiftKey || e.button === 1) {
      panning = true;
      panStartX = x;
      panViewStart = cond.zoom.startSec;
      e.preventDefault();
      return;
    }

    const cur = currentSeg();
    if (!cur) return;
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
    let newDur = Math.max(0.02, Math.min(audioDur, oldDur / factor));
    let newStart = Math.max(0, mouseT - ratio * newDur);
    if (newStart + newDur > audioDur) newStart = Math.max(0, audioDur - newDur);
    setZoom(newStart, newStart + newDur);
  }

  function clamp(v: number, lo: number, hi: number): number {
    return Math.min(hi, Math.max(lo, v));
  }
</script>

<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<div
  class="spec-wrap"
  bind:this={wrap}
  tabindex="0"
  role="application"
  aria-label="Spectrogram — scroll to zoom, shift+drag to pan, click handles to adjust boundaries"
  onmousedown={onMouseDown}
  onmousemove={onMouseMove}
  onmouseup={endDrags}
  onmouseleave={endDrags}
  onwheel={onWheel}
>
  {#if cache && cond}
    <canvas bind:this={canvas} class="spec-canvas"></canvas>
  {/if}
</div>

<style>
  .spec-wrap {
    position: relative;
    width: 100%;
    height: 100%;
    min-height: 100px;
    background: #12131a;
    overflow: hidden;
    outline: none;
  }
  .spec-canvas {
    display: block;
    width: 100%;
    height: 100%;
  }
</style>
