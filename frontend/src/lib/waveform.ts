// Pure waveform drawing — no DOM listeners, no reactive state. The
// component (WaveformView.svelte) owns the canvas, mouse handlers, and
// per-buffer cache; this module exposes the math.
//
// Per 06_FRONTEND.md: split canvas drawing from event handling so the
// drawing layer can be unit-tested and replaced. Spectrogram is deferred
// to a later task; this v1 ships waveform-only rendering.

import type { Segment } from "./types";

// ---------------------------------------------------------------------------
// Envelope (downsampled min/max per pixel-bucket)
// ---------------------------------------------------------------------------

/**
 * One pre-computed envelope at a given bucket size. `buckets[2k]` and
 * `buckets[2k+1]` are the min and max of bucket k in the original samples.
 *
 * The envelope is sized for the worst-case zoom-out at a canvas width of
 * ~4000 px (more than enough for the largest 4K monitor). At max zoom
 * we'll have ~`samples_total / canvasMaxWidth` samples per pixel; we use
 * a bucket size that gives 8x oversampling at that target so a single
 * envelope serves every zoom level above min-bucket.
 */
export interface Envelope {
  bucketSize: number; // samples per bucket
  buckets: Float32Array; // 2 floats per bucket: [min, max]
  sampleRate: number;
  totalSamples: number;
}

const CANVAS_MAX_WIDTH = 4000;
const OVERSAMPLE = 8;

/**
 * Compute a min/max envelope of the first channel of `buffer`. Operations
 * later in `drawWaveform` use either this envelope (when zoomed out) or
 * the raw samples (when zoomed in tightly enough that bucketSize > 1
 * would alias).
 */
export function computeEnvelope(buffer: AudioBuffer): Envelope {
  const samples = buffer.getChannelData(0);
  const total = samples.length;
  const targetBucket = Math.max(
    1,
    Math.floor(total / (CANVAS_MAX_WIDTH * OVERSAMPLE)),
  );
  const nBuckets = Math.ceil(total / targetBucket);
  const buckets = new Float32Array(nBuckets * 2);
  for (let b = 0; b < nBuckets; b++) {
    const start = b * targetBucket;
    const end = Math.min(start + targetBucket, total);
    let mn = Infinity;
    let mx = -Infinity;
    for (let i = start; i < end; i++) {
      const v = samples[i];
      if (v < mn) mn = v;
      if (v > mx) mx = v;
    }
    if (!isFinite(mn)) mn = 0;
    if (!isFinite(mx)) mx = 0;
    buckets[2 * b] = mn;
    buckets[2 * b + 1] = mx;
  }
  return {
    bucketSize: targetBucket,
    buckets,
    sampleRate: buffer.sampleRate,
    totalSamples: total,
  };
}

// ---------------------------------------------------------------------------
// Decoding helper (shared AudioContext)
// ---------------------------------------------------------------------------

/**
 * Fetch `url` and decode it into an AudioBuffer. Throws on network or
 * decode failure. Caller passes the AudioContext to use so the engine
 * keeps a single shared instance.
 */
export async function decodeAudioBuffer(
  url: string,
  ctx: AudioContext,
): Promise<AudioBuffer> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch audio: HTTP ${response.status}`);
  }
  const arrayBuffer = await response.arrayBuffer();
  return await ctx.decodeAudioData(arrayBuffer);
}

// ---------------------------------------------------------------------------
// Draw
// ---------------------------------------------------------------------------

export interface DrawOptions {
  /** Current word-segment index (highlighted in yellow). */
  currentSegmentIdx: number | null;
  /** If true, paint the add-token start marker at `addStartSec`. */
  addStartSec: number | null;
  /** Optional color overrides; mostly intended for theming. */
  colors?: Partial<typeof DEFAULT_COLORS>;
}

export const DEFAULT_COLORS = {
  background: "#1e1f23",
  waveform: "#5c7a8a",
  currentBoundary: "#f7c948",
  currentDim: "rgba(0,0,0,0.32)",
  otherBoundary: "rgba(124, 158, 255, 0.55)",
  rejectedTint: "rgba(226, 92, 92, 0.18)",
  acceptedTint: "rgba(76, 175, 118, 0.14)",
  noiseTint: "rgba(141, 149, 166, 0.18)",
  introTint: "rgba(217, 161, 74, 0.18)",
  addMarker: "#4ade80",
  axisText: "rgba(255,255,255,0.32)",
  tick: "rgba(255,255,255,0.18)",
  handle: "#f7c948",
  handleLetter: "#1a1b1e",
} as const;

/**
 * Render the waveform plus segment overlays into `ctx`. Stateless.
 *
 * Drawing math is in CSS pixels — pass `width`/`height` in CSS pixels and
 * make sure the caller applied a `dpr` transform if the backing store is
 * sized in device pixels.
 *
 * @param ctx target 2D canvas context
 * @param widthCss canvas width in CSS pixels
 * @param heightCss canvas height in CSS pixels
 * @param buffer source audio
 * @param envelope precomputed envelope (cheaper than scanning raw samples
 *                 at every zoom level)
 * @param viewStartSec inclusive start time of the visible window
 * @param viewEndSec exclusive end time of the visible window
 * @param segments full segment list (NOT just word-typed)
 * @param options additional draw hints
 */
export function drawWaveform(
  ctx: CanvasRenderingContext2D,
  widthCss: number,
  heightCss: number,
  buffer: AudioBuffer,
  envelope: Envelope,
  viewStartSec: number,
  viewEndSec: number,
  segments: readonly Segment[],
  options: DrawOptions,
): void {
  const w = widthCss;
  const h = heightCss;
  const colors = { ...DEFAULT_COLORS, ...(options.colors ?? {}) };

  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = colors.background;
  ctx.fillRect(0, 0, w, h);

  if (viewEndSec <= viewStartSec) return;

  const sr = buffer.sampleRate;
  const totalSamples = buffer.length;
  const samplesPerPixel = Math.max(
    1,
    Math.floor(((viewEndSec - viewStartSec) * sr) / w),
  );

  // Pick the cheaper data source: raw samples if we're zoomed in beyond
  // the envelope's bucket size, envelope otherwise.
  if (samplesPerPixel < envelope.bucketSize) {
    drawFromRaw(
      ctx,
      buffer,
      viewStartSec,
      viewEndSec,
      w,
      h,
      samplesPerPixel,
      colors,
      totalSamples,
    );
  } else {
    drawFromEnvelope(
      ctx,
      envelope,
      viewStartSec,
      viewEndSec,
      w,
      h,
      colors,
    );
  }

  drawSegmentOverlays(
    ctx,
    segments,
    options.currentSegmentIdx,
    viewStartSec,
    viewEndSec,
    w,
    h,
    colors,
  );

  drawTimeAxis(ctx, viewStartSec, viewEndSec, w, h, colors);

  if (options.addStartSec !== null) {
    const x = ((options.addStartSec - viewStartSec) / (viewEndSec - viewStartSec)) * w;
    ctx.strokeStyle = colors.addMarker;
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h);
    ctx.stroke();
    ctx.setLineDash([]);
  }
}

function drawFromRaw(
  ctx: CanvasRenderingContext2D,
  buffer: AudioBuffer,
  viewStartSec: number,
  viewEndSec: number,
  w: number,
  h: number,
  spp: number,
  colors: typeof DEFAULT_COLORS,
  totalSamples: number,
): void {
  const samples = buffer.getChannelData(0);
  const sr = buffer.sampleRate;
  const s0 = Math.max(0, Math.floor(viewStartSec * sr));
  const mid = h / 2;
  ctx.strokeStyle = colors.waveform;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let px = 0; px < w; px++) {
    const ss = s0 + px * spp;
    let mn = 1;
    let mx = -1;
    const upper = Math.min(ss + spp, totalSamples);
    for (let s = ss; s < upper; s++) {
      const v = samples[s];
      if (v < mn) mn = v;
      if (v > mx) mx = v;
    }
    if (mn > mx) {
      // No samples in range (past end of buffer); paint a midline pixel.
      ctx.moveTo(px, mid);
      ctx.lineTo(px, mid);
    } else {
      ctx.moveTo(px, mid - mx * mid * 0.9);
      ctx.lineTo(px, mid - mn * mid * 0.9);
    }
  }
  ctx.stroke();
}

function drawFromEnvelope(
  ctx: CanvasRenderingContext2D,
  env: Envelope,
  viewStartSec: number,
  viewEndSec: number,
  w: number,
  h: number,
  colors: typeof DEFAULT_COLORS,
): void {
  const sr = env.sampleRate;
  const totalBuckets = env.buckets.length / 2;
  const startBucket = (viewStartSec * sr) / env.bucketSize;
  const endBucket = (viewEndSec * sr) / env.bucketSize;
  const bucketsPerPixel = (endBucket - startBucket) / w;
  const mid = h / 2;
  ctx.strokeStyle = colors.waveform;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let px = 0; px < w; px++) {
    const b0 = Math.floor(startBucket + px * bucketsPerPixel);
    const b1 = Math.min(
      totalBuckets,
      Math.max(b0 + 1, Math.floor(startBucket + (px + 1) * bucketsPerPixel)),
    );
    if (b0 < 0 || b0 >= totalBuckets) {
      ctx.moveTo(px, mid);
      ctx.lineTo(px, mid);
      continue;
    }
    let mn = 1;
    let mx = -1;
    for (let b = Math.max(0, b0); b < b1; b++) {
      const lo = env.buckets[2 * b];
      const hi = env.buckets[2 * b + 1];
      if (lo < mn) mn = lo;
      if (hi > mx) mx = hi;
    }
    if (mn > mx) {
      ctx.moveTo(px, mid);
      ctx.lineTo(px, mid);
    } else {
      ctx.moveTo(px, mid - mx * mid * 0.9);
      ctx.lineTo(px, mid - mn * mid * 0.9);
    }
  }
  ctx.stroke();
}

function drawSegmentOverlays(
  ctx: CanvasRenderingContext2D,
  segments: readonly Segment[],
  currentSegmentIdx: number | null,
  viewStartSec: number,
  viewEndSec: number,
  w: number,
  h: number,
  colors: typeof DEFAULT_COLORS,
): void {
  const dur = viewEndSec - viewStartSec;
  // Paint non-current segments first (lower opacity) so the current
  // segment's handles draw on top.
  for (let i = 0; i < segments.length; i++) {
    if (i === currentSegmentIdx) continue;
    const seg = segments[i];
    if (seg.end < viewStartSec || seg.start > viewEndSec) continue;
    const x1 = clamp(((seg.start - viewStartSec) / dur) * w, 0, w);
    const x2 = clamp(((seg.end - viewStartSec) / dur) * w, 0, w);
    if (x2 <= x1) continue;
    const tint = pickTint(seg, colors);
    if (tint) {
      ctx.fillStyle = tint;
      ctx.fillRect(x1, 0, x2 - x1, h);
    }
    ctx.strokeStyle = colors.otherBoundary;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x1, 0);
    ctx.lineTo(x1, h);
    ctx.moveTo(x2, 0);
    ctx.lineTo(x2, h);
    ctx.stroke();
  }

  if (currentSegmentIdx === null) return;
  const seg = segments[currentSegmentIdx];
  if (!seg) return;

  const x1 = ((seg.start - viewStartSec) / dur) * w;
  const x2 = ((seg.end - viewStartSec) / dur) * w;

  // Dim outside the current segment to draw the eye into it.
  ctx.fillStyle = colors.currentDim;
  if (x1 > 0) ctx.fillRect(0, 0, Math.min(x1, w), h);
  if (x2 < w) ctx.fillRect(Math.max(0, x2), 0, w - Math.max(0, x2), h);

  ctx.strokeStyle = colors.currentBoundary;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(x1, 0);
  ctx.lineTo(x1, h);
  ctx.moveTo(x2, 0);
  ctx.lineTo(x2, h);
  ctx.stroke();

  // L/R handle tabs at top and bottom for hit-testing affordance.
  ctx.fillStyle = colors.handle;
  ctx.fillRect(x1 - 4, 0, 8, 14);
  ctx.fillRect(x1 - 4, h - 14, 8, 14);
  ctx.fillRect(x2 - 4, 0, 8, 14);
  ctx.fillRect(x2 - 4, h - 14, 8, 14);

  ctx.fillStyle = colors.handleLetter;
  ctx.font = "bold 9px ui-monospace, monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  ctx.fillText("L", x1, 10);
  ctx.fillText("R", x2, 10);
  ctx.textAlign = "start";
}

function pickTint(seg: Segment, colors: typeof DEFAULT_COLORS): string | null {
  if (seg.segment_type === "short_noise" || seg.segment_type === "crosstalk") {
    return colors.noiseTint;
  }
  if (seg.segment_type === "intro" || seg.status === "intro") {
    return colors.introTint;
  }
  if (seg.status === "accepted") return colors.acceptedTint;
  if (seg.status === "rejected") return colors.rejectedTint;
  return null;
}

function drawTimeAxis(
  ctx: CanvasRenderingContext2D,
  viewStartSec: number,
  viewEndSec: number,
  w: number,
  h: number,
  colors: typeof DEFAULT_COLORS,
): void {
  const dur = viewEndSec - viewStartSec;
  ctx.fillStyle = colors.axisText;
  ctx.font = "9px ui-monospace, monospace";
  ctx.textBaseline = "alphabetic";
  const tStep =
    dur > 4 ? 1 : dur > 2 ? 0.5 : dur > 1 ? 0.2 : dur > 0.4 ? 0.1 : 0.05;
  for (
    let t = Math.ceil(viewStartSec / tStep) * tStep;
    t < viewEndSec;
    t += tStep
  ) {
    const px = ((t - viewStartSec) / dur) * w;
    ctx.fillText(t.toFixed(2) + "s", px + 2, h - 3);
    ctx.fillStyle = colors.tick;
    ctx.fillRect(px, h - 14, 1, 4);
    ctx.fillStyle = colors.axisText;
  }
}

// ---------------------------------------------------------------------------
// Hit testing helpers (used by WaveformView mouse handlers)
// ---------------------------------------------------------------------------

export type HandleSide = "start" | "end";

export interface HandleHit {
  side: HandleSide;
  distancePx: number;
}

/** Hit-test the L/R boundary handles. Returns null if neither is within
 * `tolerancePx`. */
export function hitTestHandles(
  segment: Segment,
  mouseX: number,
  viewStartSec: number,
  viewEndSec: number,
  widthPx: number,
  tolerancePx = 6,
): HandleHit | null {
  const dur = viewEndSec - viewStartSec;
  if (dur <= 0) return null;
  const sx = ((segment.start - viewStartSec) / dur) * widthPx;
  const ex = ((segment.end - viewStartSec) / dur) * widthPx;
  const ds = Math.abs(mouseX - sx);
  const de = Math.abs(mouseX - ex);
  if (Math.min(ds, de) > tolerancePx) return null;
  return ds <= de
    ? { side: "start", distancePx: ds }
    : { side: "end", distancePx: de };
}

/**
 * Return which boundary of `segment` is nearer to the click X (in
 * seconds). The legacy review tool snaps the nearer boundary to the click;
 * we preserve that behavior.
 */
export function nearerBoundary(segment: Segment, clickSec: number): HandleSide {
  return Math.abs(clickSec - segment.start) <= Math.abs(clickSec - segment.end)
    ? "start"
    : "end";
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}
