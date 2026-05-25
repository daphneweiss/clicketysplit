// Central Svelte 5 runes-based store.
//
// Per 06_FRONTEND.md §Central store, everything that needs to be reactive
// across components lives in `state`. Components import `state`,
// `activeCondState`, `currentSegment` and mutate via the action functions
// below — no other source of truth.
//
// This file uses runes (`$state`, `$derived`) and therefore must be named
// `.svelte.ts` so the Svelte compiler picks it up.
//
// Task 10 extension: per-condition audio buffers + envelopes are cached
// here in a small LRU keyed by "spk\x1Fcond". setLabel/setStatus/addToken/
// removeToken/setBoundary all flow through this module, which also runs
// the 1-second debounced /api/segments save.

import {
  exportAll as apiExportAll,
  getSegments as apiGetSegments,
  getConfig as apiGetConfig,
  getCapabilities as apiGetCapabilities,
  getSession as apiGetSession,
  getStimulusList as apiGetStimulusList,
  saveSegments as apiSaveSegments,
  saveSession as apiSaveSession,
} from "./api";
import { autoLabel, wordIndexOf } from "./labeling";
import { computeEnvelope, type Envelope } from "./waveform";
import { decode as decodeAudio } from "./audio";
import type {
  Capabilities,
  Condition,
  DiscoveryResult,
  ExperimentConfig,
  ExportResponse,
  LabelAnchor,
  PresentationOrder,
  Segment,
  SegmentStatus,
  SegmentsFile,
  Toast,
  ToastKind,
} from "./types";
import { ApiError } from "./types";

// ---- Per-condition review state -------------------------------------------

export interface ConditionReviewState {
  segments: Segment[];
  labelAnchors: LabelAnchor[];
  presentationOrder: PresentationOrder;
  expectedRepsPerStimulus: number;
  stimulusList: string[];
  currentTokenIndex: number;
  zoom: { startSec: number; endSec: number };
  dirty: boolean;
  /** Indices of segments whose tentative label changed in the last
   * recompute — used to flash a diff highlight per 06 doc. Cleared after
   * the timeout fires. */
  diffFlashWordIndices: number[];
  /** The full segments-file payload as last loaded from the backend so we
   * can preserve unknown fields (source_files, denoised_audio, ...) on
   * round-trip. */
  rawFile: SegmentsFile;
  /** True while loading audio/segments; UI shows a skeleton. */
  loading: boolean;
  loadError: string | null;
}

export interface AudioCacheEntry {
  buffer: AudioBuffer;
  envelope: Envelope;
  /** Monotonic counter for LRU. */
  lastUsed: number;
}

export interface AppState {
  // Loaded config
  config: ExperimentConfig | null;
  configPath: string | null;

  // Wizard step (1..4)
  step: 1 | 2 | 3 | 4;

  // Per (speaker, condition) review state
  reviewProgress: Record<string, Record<string, ConditionReviewState>>;
  activeSpeakerId: string | null;
  activeCondition: string | null;

  // Detection capabilities (from /api/capabilities)
  capabilities: Capabilities | null;

  // Most recent discovery scan (Setup step uses this)
  discovery: DiscoveryResult | null;

  // Misc cached UI things
  lastRecordingsRoot: string | null;
  lastConfigPath: string | null;

  // Toasts
  toasts: Toast[];

  // Booted flag — App.svelte's mount effect sets this true once
  // capabilities/config/session have been (attempted to be) loaded.
  booted: boolean;

  /** Generation counter; bumped on every condition load. Components can
   * watch this to force re-runs of $effect blocks tied to the active
   * audio. */
  audioGeneration: number;

  /**
   * Export step (task 11): which tokens the user has marked for export,
   * indexed spk → cond → assigned_name → tokenIndex-within-label (1-based) → bool.
   *
   * Per CONTRACT_NOTES C4, defaults derive lazily the first time a
   * condition is entered in Select: every word-typed segment with
   * `status == "accepted"` AND non-empty `assigned_name` starts selected.
   * Other word-typed segments (pending/rejected) are visible and
   * deselected by default — the user can opt them in.
   *
   * Persisted via /api/session so selections survive a reload.
   */
  selectedTokens: Record<
    string,
    Record<string, Record<string, Record<number, boolean>>>
  >;

  /** Export step status. */
  exportState: "idle" | "running" | "done";

  /**
   * Per-pair results from the most recent /api/export_all call. Populated
   * after the request returns; rendered by Export.svelte.
   */
  perPairResult: PerPairResult[];

  /** The output directory shown in the success summary (extracted from
   * the first result's `output_dir`; trimmed of the trailing `tokens/`). */
  exportOutputDir: string | null;
}

export interface PerPairResult {
  spk: string;
  cond: string;
  status: "ok" | "error" | "running";
  /** Filename count and tokens_per_word from the export response. */
  counts?: {
    n_exported: number;
    tokens_per_word: Record<string, number>;
    skipped?: Record<string, number>;
  };
  /** Output directory of this pair (relative to experiment root). */
  outputDir?: string;
  /** Error code+message when status==="error". */
  error?: { code: string; message: string };
}

export const state: AppState = $state({
  config: null,
  configPath: null,
  step: 1,
  reviewProgress: {},
  activeSpeakerId: null,
  activeCondition: null,
  capabilities: null,
  discovery: null,
  lastRecordingsRoot: null,
  lastConfigPath: null,
  toasts: [],
  booted: false,
  audioGeneration: 0,
  selectedTokens: {},
  exportState: "idle",
  perPairResult: [],
  exportOutputDir: null,
});

// ---- Audio cache (LRU, max 5 buffers) -------------------------------------
//
// Outside of `state` because AudioBuffers shouldn't participate in Svelte's
// proxy reactivity (huge object, expensive cloning). Components observe
// `state.audioGeneration` to know when the entry for the active condition
// is fresh.

const AUDIO_CACHE_MAX = 5;
const _audioCache = new Map<string, AudioCacheEntry>();
let _audioClock = 0;

function cacheKey(spk: string, cond: string): string {
  return `${spk}\x1F${cond}`;
}

/** Look up cached audio for the given speaker/condition, or null. */
export function getAudioCache(
  spk: string,
  cond: string,
): AudioCacheEntry | null {
  const entry = _audioCache.get(cacheKey(spk, cond));
  if (entry) entry.lastUsed = ++_audioClock;
  return entry ?? null;
}

function putAudioCache(
  spk: string,
  cond: string,
  buffer: AudioBuffer,
  envelope: Envelope,
): AudioCacheEntry {
  const key = cacheKey(spk, cond);
  const entry: AudioCacheEntry = {
    buffer,
    envelope,
    lastUsed: ++_audioClock,
  };
  _audioCache.set(key, entry);
  // Evict LRU until we're under the cap.
  while (_audioCache.size > AUDIO_CACHE_MAX) {
    let oldestKey: string | null = null;
    let oldestUsed = Infinity;
    for (const [k, v] of _audioCache) {
      if (v.lastUsed < oldestUsed) {
        oldestUsed = v.lastUsed;
        oldestKey = k;
      }
    }
    if (oldestKey === null) break;
    _audioCache.delete(oldestKey);
  }
  return entry;
}

// ---- Derived values -------------------------------------------------------
//
// Svelte 5 forbids exporting a `$derived` directly from a module — the rune
// is only meaningful inside reactive contexts. Expose the current value via
// a getter function instead; components call `activeCondState()` to read.
// Inside a `.svelte` script that needs reactivity, wrap the call in a local
// `$derived(activeCondState())` so the component re-renders on change.

export function activeCondState(): ConditionReviewState | null {
  if (!state.activeSpeakerId || !state.activeCondition) return null;
  return (
    state.reviewProgress[state.activeSpeakerId]?.[state.activeCondition] ??
    null
  );
}

export function currentSegment(): Segment | null {
  const cond = activeCondState();
  if (!cond) return null;
  return cond.segments[cond.currentTokenIndex] ?? null;
}

/**
 * Indices of word-typed segments, in order. Useful for the keyboard
 * navigation (arrow keys) which only ever advance between word segments.
 */
export function wordSegmentIndices(
  cond: ConditionReviewState | null = activeCondState(),
): number[] {
  if (!cond) return [];
  const out: number[] = [];
  for (let i = 0; i < cond.segments.length; i++) {
    if (cond.segments[i].segment_type === "word") out.push(i);
  }
  return out;
}

// ---- Toast actions --------------------------------------------------------

let _toastId = 0;
const DEFAULT_TOAST_MS = 4000;

export function pushToast(
  message: string,
  kind: ToastKind = "info",
  ttlMs: number = DEFAULT_TOAST_MS,
): number {
  const id = ++_toastId;
  state.toasts.push({ id, message, kind });
  if (ttlMs > 0) {
    setTimeout(() => dismissToast(id), ttlMs);
  }
  return id;
}

export function dismissToast(id: number): void {
  const idx = state.toasts.findIndex((t) => t.id === id);
  if (idx >= 0) state.toasts.splice(idx, 1);
}

/** Convenience helper: surface an unknown error as a toast. */
export function toastError(err: unknown, fallback = "Unexpected error"): void {
  if (err instanceof ApiError) {
    pushToast(`${err.code}: ${err.message}`, "error");
    return;
  }
  if (err instanceof Error) {
    pushToast(err.message || fallback, "error");
    return;
  }
  pushToast(fallback, "error");
}

// ---- Step / active condition actions --------------------------------------

export function setStep(step: 1 | 2 | 3 | 4): void {
  state.step = step;
  scheduleSessionSave();
}

export function setActive(
  speakerId: string | null,
  condition: string | null,
): void {
  state.activeSpeakerId = speakerId;
  state.activeCondition = condition;
  scheduleSessionSave();
}

export function markDirty(speakerId: string, condition: string): void {
  const cond = state.reviewProgress[speakerId]?.[condition];
  if (cond) cond.dirty = true;
}

export function clearDirty(speakerId: string, condition: string): void {
  const cond = state.reviewProgress[speakerId]?.[condition];
  if (cond) cond.dirty = false;
}

// ---- Condition loading ----------------------------------------------------

function findConditionDefn(name: string): Condition | null {
  if (!state.config) return null;
  return state.config.conditions.find((c) => c.name === name) ?? null;
}

/**
 * Read the basename of a stimulus_list path (config-relative or absolute).
 * The backend's GET /api/stimulus_lists/<name> route only accepts a
 * filename, not a path (path traversal guard).
 */
function stimulusListBasename(p: string): string {
  const parts = p.split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

/**
 * Compute a sensible initial zoom around segment `idx`: the segment itself
 * plus 0.5s context on each side, clamped to the audio bounds.
 */
function defaultZoomForSegment(
  seg: Segment | null,
  audioDuration: number,
): { startSec: number; endSec: number } {
  if (!seg) return { startSec: 0, endSec: Math.min(2, audioDuration) };
  const pad = 0.5;
  const s = Math.max(0, seg.start - pad);
  const e = Math.min(audioDuration, seg.end + pad);
  return { startSec: s, endSec: Math.max(s + 0.05, e) };
}

/**
 * Load (or re-load) the review state and audio for a speaker × condition.
 *
 * Idempotent-ish: if the same (spk, cond) is already loaded AND its audio
 * is cached AND `force` is false, this is a no-op. Otherwise it:
 *
 *   1. Fetches /api/segments/<spk>/<cond>
 *   2. Fetches the stimulus list referenced by the config for that
 *      condition (so the autocomplete dropdown can render)
 *   3. Fetches /api/audio/working/<spk>/<cond>, decodes into AudioBuffer,
 *      computes the envelope, and stuffs both into the audio LRU cache
 *
 * Sets state.activeSpeakerId/activeCondition on success.
 */
export async function loadCondition(
  spk: string,
  cond: string,
  force = false,
): Promise<void> {
  if (!state.config) {
    pushToast("Load an experiment first", "warn");
    return;
  }

  // Ensure the slot exists so the UI can show a loading state.
  //
  // Subtle Svelte-5 trap: assigning a plain object into a $state proxy
  // wraps it, but a local variable holding the pre-assignment reference is
  // still the *unwrapped* original. Any subsequent mutation via that local
  // bypasses the proxy's SET trap and reactivity never fires. So we must
  // always re-read through `state.reviewProgress[spk]` after the assignment
  // to grab the proxy, and the same for `condState` once we slot it in.
  if (!state.reviewProgress[spk]) {
    state.reviewProgress[spk] = {};
  }
  let condState = state.reviewProgress[spk]![cond];
  const alreadyLoaded =
    condState && condState.segments.length > 0 && !condState.loading;
  const haveAudio = !!getAudioCache(spk, cond);
  if (alreadyLoaded && haveAudio && !force) {
    state.activeSpeakerId = spk;
    state.activeCondition = cond;
    scheduleSessionSave();
    return;
  }

  // Initialize an empty loading state if we don't already have one.
  if (!condState) {
    state.reviewProgress[spk]![cond] = {
      segments: [],
      labelAnchors: [],
      presentationOrder: "random",
      expectedRepsPerStimulus: 3,
      stimulusList: [],
      currentTokenIndex: 0,
      zoom: { startSec: 0, endSec: 1 },
      dirty: false,
      diffFlashWordIndices: [],
      rawFile: { segments: [] },
      loading: true,
      loadError: null,
    };
    condState = state.reviewProgress[spk]![cond]!;
  } else {
    condState.loading = true;
    condState.loadError = null;
  }
  state.activeSpeakerId = spk;
  state.activeCondition = cond;

  let segFile: SegmentsFile;
  try {
    segFile = await apiGetSegments(spk, cond);
  } catch (err) {
    condState.loading = false;
    if (err instanceof ApiError && err.status === 404) {
      condState.loadError =
        "No segments found — run detection in the Setup step first.";
    } else {
      condState.loadError =
        err instanceof Error ? err.message : "Failed to load segments";
    }
    toastError(err, "Failed to load segments");
    return;
  }

  // Pull stimulus list from either the segments file or the config.
  const condDefn = findConditionDefn(cond);
  let stimulusList: string[] = Array.isArray(segFile.stimulus_list)
    ? [...segFile.stimulus_list]
    : [];
  if (stimulusList.length === 0 && condDefn) {
    try {
      const sl = await apiGetStimulusList(stimulusListBasename(condDefn.stimulus_list));
      stimulusList = sl.lines;
    } catch (err) {
      // Non-fatal — autocomplete just won't have suggestions.
      // eslint-disable-next-line no-console
      console.warn("Failed to load stimulus list:", err);
    }
  }

  const segments: Segment[] = (segFile.segments ?? []).map((s) => ({ ...s }));
  const anchors: LabelAnchor[] = Array.isArray(segFile.anchors)
    ? segFile.anchors.map((a) => ({ ...a }))
    : Array.isArray((segFile as { label_anchors?: LabelAnchor[] }).label_anchors)
      ? (segFile as { label_anchors?: LabelAnchor[] }).label_anchors!.map(
          (a) => ({ ...a }),
        )
      : [];
  const presentationOrder: PresentationOrder =
    condDefn?.presentation_order ?? "random";
  const expectedReps = condDefn?.expected_reps_per_stimulus ?? 3;

  // Project anchors onto segments via autoLabel so the initial render
  // matches what export will see.
  const projected = autoLabel(segments, stimulusList, {
    presentationOrder,
    expectedRepsPerStimulus: expectedReps,
    anchors,
  });
  // Honour any pre-existing per-segment label_source="anchor" labels: if
  // a segment was anchored in the saved file but not represented as a
  // LabelAnchor, preserve that label. The Python backend already keeps
  // these in sync; this is a belt-and-braces guard.
  for (let i = 0; i < projected.length; i++) {
    if (segments[i].label_source === "anchor" && segments[i].assigned_name) {
      // We trust the backend.
    } else {
      segments[i].assigned_name = projected[i].assigned_name;
      segments[i].label_source = projected[i].label_source;
    }
  }

  condState.segments = segments;
  condState.labelAnchors = anchors;
  condState.presentationOrder = presentationOrder;
  condState.expectedRepsPerStimulus = expectedReps;
  condState.stimulusList = stimulusList;
  condState.rawFile = segFile;
  condState.diffFlashWordIndices = [];
  condState.dirty = false;

  // Restore current token: first word-typed segment if none saved.
  const words = wordSegmentIndices(condState);
  if (words.length > 0) {
    if (
      condState.currentTokenIndex < 0 ||
      condState.currentTokenIndex >= segments.length
    ) {
      condState.currentTokenIndex = words[0];
    } else if (segments[condState.currentTokenIndex]?.segment_type !== "word") {
      condState.currentTokenIndex = words[0];
    }
  } else {
    condState.currentTokenIndex = 0;
  }

  // Audio: load + decode + envelope. Cache hit = no work.
  // DIAGNOSTIC: log each phase so a hang's location is visible in console.
  const _t0 = performance.now();
  // eslint-disable-next-line no-console
  console.log(`[loadCondition] ${spk}/${cond} start`);
  try {
    if (!getAudioCache(spk, cond) || force) {
      const url = `/api/audio/working/${encodeURIComponent(spk)}/${encodeURIComponent(cond)}`;
      // eslint-disable-next-line no-console
      console.log(`[loadCondition] decode start ${Math.round(performance.now() - _t0)}ms`);
      const buffer = await decodeAudio(url);
      // eslint-disable-next-line no-console
      console.log(`[loadCondition] decode done ${Math.round(performance.now() - _t0)}ms (${buffer.duration.toFixed(2)}s, ${buffer.sampleRate}Hz)`);
      const envelope = computeEnvelope(buffer);
      // eslint-disable-next-line no-console
      console.log(`[loadCondition] envelope done ${Math.round(performance.now() - _t0)}ms`);
      putAudioCache(spk, cond, buffer, envelope);
      // eslint-disable-next-line no-console
      console.log(`[loadCondition] cache put ${Math.round(performance.now() - _t0)}ms`);
    } else {
      // eslint-disable-next-line no-console
      console.log(`[loadCondition] cache hit`);
    }
  } catch (err) {
    // eslint-disable-next-line no-console
    console.error(`[loadCondition] decode FAILED ${Math.round(performance.now() - _t0)}ms`, err);
    condState.loadError =
      err instanceof Error ? err.message : "Failed to load audio";
    toastError(err, "Failed to load working audio");
    condState.loading = false;
    state.audioGeneration++;
    return;
  }

  const cache = getAudioCache(spk, cond);
  if (cache) {
    const seg = segments[condState.currentTokenIndex] ?? null;
    condState.zoom = defaultZoomForSegment(seg, cache.buffer.duration);
  }

  condState.loading = false;
  state.audioGeneration++;
  scheduleSessionSave();
  // eslint-disable-next-line no-console
  console.log(`[loadCondition] DONE ${Math.round(performance.now() - _t0)}ms (loading=false, audioGen=${state.audioGeneration})`);
}

// ---- Segment edits --------------------------------------------------------

function projectAnchors(cond: ConditionReviewState): void {
  // Run the forward walk, then capture which word_indices changed labels
  // so the UI can flash a diff highlight.
  const before = cond.segments.map((s) => s.assigned_name);
  const projected = autoLabel(cond.segments, cond.stimulusList, {
    presentationOrder: cond.presentationOrder,
    expectedRepsPerStimulus: cond.expectedRepsPerStimulus,
    anchors: cond.labelAnchors,
  });
  const changed: number[] = [];
  let wi = -1;
  for (let i = 0; i < cond.segments.length; i++) {
    if (cond.segments[i].segment_type !== "word") {
      cond.segments[i].assigned_name = projected[i].assigned_name;
      cond.segments[i].label_source = projected[i].label_source;
      continue;
    }
    wi++;
    if (before[i] !== projected[i].assigned_name) changed.push(wi);
    cond.segments[i].assigned_name = projected[i].assigned_name;
    cond.segments[i].label_source = projected[i].label_source;
  }
  cond.diffFlashWordIndices = changed;
  if (changed.length > 0) {
    // Fade after 2s per 06 doc.
    const flashed = changed.slice();
    setTimeout(() => {
      const c = state.reviewProgress[state.activeSpeakerId ?? ""]?.[
        state.activeCondition ?? ""
      ];
      if (!c) return;
      // Only clear if the set is still the same — a second edit may have
      // replaced it with newer flashes.
      if (
        c.diffFlashWordIndices.length === flashed.length &&
        c.diffFlashWordIndices.every((x, k) => x === flashed[k])
      ) {
        c.diffFlashWordIndices = [];
      }
    }, 2000);
  }
}

function upsertAnchor(
  anchors: LabelAnchor[],
  word_index: number,
  label: string,
): LabelAnchor[] {
  const out = anchors.filter((a) => a.word_index !== word_index);
  out.push({ word_index, label, source: "user" });
  out.sort((a, b) => a.word_index - b.word_index);
  return out;
}

/**
 * Apply a user label edit at segment index `segmentIdx`.
 *
 * Inserts/replaces a user anchor at the corresponding word_index, then
 * re-runs the forward walk so every downstream tentative label updates.
 *
 * Empty `newLabel` is a halt anchor per 03 doc.
 */
export function setLabel(segmentIdx: number, newLabel: string): void {
  const cond = activeCondState();
  if (!cond) return;
  const wi = wordIndexOf(cond.segments, segmentIdx);
  if (wi < 0) return;
  cond.labelAnchors = upsertAnchor(cond.labelAnchors, wi, newLabel);
  projectAnchors(cond);
  cond.dirty = true;
  scheduleSegmentsSave();
}

/** Remove the user anchor at the same word_index, then re-project. */
export function clearAnchor(segmentIdx: number): void {
  const cond = activeCondState();
  if (!cond) return;
  const wi = wordIndexOf(cond.segments, segmentIdx);
  if (wi < 0) return;
  // Drop only USER anchors at this index; initial anchors stay.
  const next = cond.labelAnchors.filter(
    (a) => !(a.word_index === wi && a.source === "user"),
  );
  if (next.length === cond.labelAnchors.length) return;
  cond.labelAnchors = next;
  projectAnchors(cond);
  cond.dirty = true;
  scheduleSegmentsSave();
}

export function setStatus(segmentIdx: number, status: SegmentStatus): void {
  const cond = activeCondState();
  if (!cond) return;
  const seg = cond.segments[segmentIdx];
  if (!seg) return;
  seg.status = status;
  cond.dirty = true;
  scheduleSegmentsSave();
}

/**
 * Move the start or end boundary of segment `segmentIdx` to `sec`.
 * Clamps to ≥ 50ms duration; clamps to audio bounds.
 */
export function setBoundary(
  segmentIdx: number,
  side: "start" | "end",
  sec: number,
): void {
  const cond = activeCondState();
  if (!cond) return;
  const seg = cond.segments[segmentIdx];
  if (!seg) return;
  const cache =
    state.activeSpeakerId && state.activeCondition
      ? getAudioCache(state.activeSpeakerId, state.activeCondition)
      : null;
  const audioMax = cache ? cache.buffer.duration : Infinity;
  const clamped = Math.max(0, Math.min(audioMax, Math.round(sec * 1e4) / 1e4));
  const minDur = 0.05; // 50 ms
  if (side === "start") {
    if (clamped >= seg.end - minDur) return;
    seg.start = clamped;
  } else {
    if (clamped <= seg.start + minDur) return;
    seg.end = clamped;
  }
  seg.duration_ms = Math.round((seg.end - seg.start) * 1e3);
  cond.dirty = true;
  scheduleSegmentsSave();
}

/**
 * Insert a new word-typed segment spanning [startSec, endSec]. New segments
 * are placed in source order. After insertion all downstream word_indices
 * shift, so existing anchors at those positions still attach to the SAME
 * underlying token (anchors track word_index — which is recomputed after
 * the splice). To preserve that intent, shift any anchor whose word_index
 * sits at or after the insertion point by +1.
 */
export function addToken(startSec: number, endSec: number): number | null {
  const cond = activeCondState();
  if (!cond) return null;
  if (endSec - startSec < 0.05) {
    pushToast("Segment too short (min 50ms)", "warn");
    return null;
  }
  const newSeg: Segment = {
    start: Math.round(Math.min(startSec, endSec) * 1e4) / 1e4,
    end: Math.round(Math.max(startSec, endSec) * 1e4) / 1e4,
    duration_ms: Math.round(Math.abs(endSec - startSec) * 1e3),
    segment_type: "word",
    assigned_name: "",
    label_source: "",
    status: "pending",
    token_index: 0,
    cluster_size: 0,
  };
  let insertIdx = cond.segments.length;
  for (let i = 0; i < cond.segments.length; i++) {
    if (cond.segments[i].start > newSeg.start) {
      insertIdx = i;
      break;
    }
  }
  // Count word-typed segments BEFORE insertIdx — that's the new word_index.
  let newWi = 0;
  for (let i = 0; i < insertIdx; i++) {
    if (cond.segments[i].segment_type === "word") newWi++;
  }
  cond.segments.splice(insertIdx, 0, newSeg);
  // Shift any anchor whose word_index is at-or-after newWi by +1.
  cond.labelAnchors = cond.labelAnchors.map((a) =>
    a.word_index >= newWi
      ? { ...a, word_index: a.word_index + 1 }
      : a,
  );
  projectAnchors(cond);
  cond.currentTokenIndex = insertIdx;
  cond.dirty = true;
  scheduleSegmentsSave();
  return insertIdx;
}

/** Remove the segment at `segmentIdx`. Anchors at higher word_indices
 * shift down by 1; the anchor at this word_index (if any) is dropped. */
export function removeToken(segmentIdx: number): void {
  const cond = activeCondState();
  if (!cond) return;
  const seg = cond.segments[segmentIdx];
  if (!seg) return;
  const wi = wordIndexOf(cond.segments, segmentIdx);
  cond.segments.splice(segmentIdx, 1);
  if (wi >= 0) {
    cond.labelAnchors = cond.labelAnchors
      .filter((a) => a.word_index !== wi)
      .map((a) =>
        a.word_index > wi ? { ...a, word_index: a.word_index - 1 } : a,
      );
  }
  if (cond.currentTokenIndex >= cond.segments.length) {
    cond.currentTokenIndex = Math.max(0, cond.segments.length - 1);
  }
  projectAnchors(cond);
  cond.dirty = true;
  scheduleSegmentsSave();
}

export function setCurrentTokenIndex(segmentIdx: number): void {
  const cond = activeCondState();
  if (!cond) return;
  if (segmentIdx < 0 || segmentIdx >= cond.segments.length) return;
  cond.currentTokenIndex = segmentIdx;
  // Recompute default zoom so the new token is centered with context.
  const cache =
    state.activeSpeakerId && state.activeCondition
      ? getAudioCache(state.activeSpeakerId, state.activeCondition)
      : null;
  if (cache) {
    cond.zoom = defaultZoomForSegment(
      cond.segments[segmentIdx],
      cache.buffer.duration,
    );
  }
  scheduleSessionSave();
}

export function setZoom(startSec: number, endSec: number): void {
  const cond = activeCondState();
  if (!cond) return;
  cond.zoom = { startSec, endSec };
}

/** Advance to the next word-typed segment (wraps to end-of-list inert). */
export function nextWordSegment(): void {
  const cond = activeCondState();
  if (!cond) return;
  const words = wordSegmentIndices(cond);
  if (words.length === 0) return;
  const pos = words.indexOf(cond.currentTokenIndex);
  if (pos < 0) {
    setCurrentTokenIndex(words[0]);
    return;
  }
  if (pos + 1 < words.length) setCurrentTokenIndex(words[pos + 1]);
}

export function prevWordSegment(): void {
  const cond = activeCondState();
  if (!cond) return;
  const words = wordSegmentIndices(cond);
  if (words.length === 0) return;
  const pos = words.indexOf(cond.currentTokenIndex);
  if (pos <= 0) return;
  setCurrentTokenIndex(words[pos - 1]);
}

// ---- Segments autosave (1s debounced) -------------------------------------

const SEGMENTS_DEBOUNCE_MS = 1000;
let _segmentsTimer: ReturnType<typeof setTimeout> | null = null;
let _segmentsInflight = false;

export function scheduleSegmentsSave(): void {
  if (_segmentsTimer) clearTimeout(_segmentsTimer);
  _segmentsTimer = setTimeout(() => {
    _segmentsTimer = null;
    void flushSegmentsSave();
  }, SEGMENTS_DEBOUNCE_MS);
}

async function flushSegmentsSave(): Promise<void> {
  if (_segmentsInflight) {
    // Reschedule so we don't drop an edit made during an in-flight save.
    scheduleSegmentsSave();
    return;
  }
  const spk = state.activeSpeakerId;
  const cnd = state.activeCondition;
  if (!spk || !cnd) return;
  const cond = state.reviewProgress[spk]?.[cnd];
  if (!cond || !cond.dirty) return;
  _segmentsInflight = true;
  try {
    const payload: SegmentsFile = {
      ...cond.rawFile,
      schema_version: cond.rawFile.schema_version ?? 1,
      segments: cond.segments.map((s) => ({ ...s })),
      stimulus_list: cond.stimulusList,
      label_anchors: cond.labelAnchors.map((a) => ({ ...a })),
    } as SegmentsFile;
    // Backend writes reviewed_segments.json; never re-attach _source.
    delete payload._source;
    await apiSaveSegments(spk, cnd, payload);
    cond.dirty = false;
  } catch (err) {
    toastError(err, "Failed to save segments");
  } finally {
    _segmentsInflight = false;
  }
}

// ---- Session autosave -----------------------------------------------------

const SESSION_DEBOUNCE_MS = 5000;
let _sessionTimer: ReturnType<typeof setTimeout> | null = null;
let _sessionInflight = false;

/**
 * Schedule a debounced session save. Anything in `state` that should survive
 * a reload (step, active speaker/condition, last roots) is bundled into the
 * payload. Per CONTRACT_NOTES C1 / 02 doc, this writes to `.session.json`
 * on disk inside the experiment's output_root. No-op until a config is
 * loaded.
 */
export function scheduleSessionSave(): void {
  if (!state.config) return;
  if (_sessionTimer) clearTimeout(_sessionTimer);
  _sessionTimer = setTimeout(() => {
    _sessionTimer = null;
    void pushSessionToBackend(true);
  }, SESSION_DEBOUNCE_MS);
}

/**
 * Fire-and-forget session save. `autosave=true` writes to
 * `.session.autosave.json` (the timer-driven slot); call with `false` for
 * an explicit user-initiated save.
 */
export async function pushSessionToBackend(autosave: boolean): Promise<void> {
  if (!state.config) return;
  if (_sessionInflight) return;
  _sessionInflight = true;
  try {
    const cond = activeCondState();
    await apiSaveSession(
      {
        step: state.step,
        activeSpeakerId: state.activeSpeakerId,
        activeCondition: state.activeCondition,
        lastRecordingsRoot: state.lastRecordingsRoot,
        lastConfigPath: state.lastConfigPath,
        currentTokenIndex: cond ? cond.currentTokenIndex : 0,
        zoom: cond ? cond.zoom : null,
        selectedTokens: serializeSelections(),
      },
      autosave,
    );
  } catch (err) {
    // Don't toast — autosave failures shouldn't spam the user. The console
    // log is enough until the next manual save surfaces the error.
    // eslint-disable-next-line no-console
    console.warn("session autosave failed:", err);
  } finally {
    _sessionInflight = false;
  }
}

// ---- Selection serialization (session round-trip) -------------------------

/** Shape we serialize to /api/session: plain object indexed
 *  spk → cond → word → tokenIndex (number) → bool. */
type SerializedSelections = Record<
  string,
  Record<string, Record<string, Record<string, boolean>>>
>;

function serializeSelections(): SerializedSelections {
  // The proxy objects produced by $state survive JSON.stringify, but cloning
  // here keeps the payload predictable (and drops Symbol noise if any).
  const out: SerializedSelections = {};
  for (const [spk, spkSlot] of Object.entries(state.selectedTokens)) {
    out[spk] = {};
    for (const [cond, condSlot] of Object.entries(spkSlot)) {
      out[spk][cond] = {};
      for (const [name, indices] of Object.entries(condSlot)) {
        out[spk][cond][name] = {};
        for (const [tokenIdx, sel] of Object.entries(indices)) {
          out[spk][cond][name][String(tokenIdx)] = !!sel;
        }
      }
    }
  }
  return out;
}

function deserializeSelections(raw: unknown): void {
  if (!raw || typeof raw !== "object") return;
  const cleaned: AppState["selectedTokens"] = {};
  for (const [spk, spkSlot] of Object.entries(raw as Record<string, unknown>)) {
    if (!spkSlot || typeof spkSlot !== "object") continue;
    cleaned[spk] = {};
    for (const [cond, condSlot] of Object.entries(
      spkSlot as Record<string, unknown>,
    )) {
      if (!condSlot || typeof condSlot !== "object") continue;
      cleaned[spk][cond] = {};
      for (const [name, indices] of Object.entries(
        condSlot as Record<string, unknown>,
      )) {
        if (!indices || typeof indices !== "object") continue;
        cleaned[spk][cond][name] = {};
        for (const [tokenIdx, sel] of Object.entries(
          indices as Record<string, unknown>,
        )) {
          const n = Number(tokenIdx);
          if (!Number.isFinite(n)) continue;
          cleaned[spk][cond][name][n] = !!sel;
        }
      }
    }
  }
  state.selectedTokens = cleaned;
}

// ---- Selection (task 11, Select.svelte) -----------------------------------
//
// Selection lives in `state.selectedTokens` indexed by:
//
//   spk → cond → assigned_name → tokenIndex (1-based within that label) → bool
//
// Per CONTRACT_NOTES C4, the default-selected set is "every word-typed
// segment with status=='accepted' AND non-empty assigned_name". This is
// computed lazily: the first time a condition's segments are available AND
// the user enters Select for that pair, we walk the segments and populate
// the map. Once populated we never auto-overwrite; subsequent
// add/remove/relabel edits are pushed in by `syncSelectionAfterSegmentEdit`.

/** Return word-typed segments grouped by 1-based index within their
 *  `assigned_name`, in source order. Used by Select.svelte to render
 *  rows AND by the default-selection initializer. */
export interface TokenGrouping {
  /** assigned_name → list of {segmentIdx, tokenIndex (1-based)} */
  groups: Record<string, Array<{ segmentIdx: number; tokenIndex: number }>>;
  /** segment indices of every word-typed segment with EMPTY assigned_name */
  unlabeled: number[];
  /** assigned_names in the order encountered in source order */
  labelOrder: string[];
}

export function groupTokensByLabel(
  segments: readonly Segment[],
): TokenGrouping {
  const groups: Record<string, Array<{ segmentIdx: number; tokenIndex: number }>> = {};
  const labelOrder: string[] = [];
  const unlabeled: number[] = [];
  const counters: Record<string, number> = {};
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    if (seg.segment_type !== "word") continue;
    const name = seg.assigned_name?.trim() ?? "";
    if (!name) {
      unlabeled.push(i);
      continue;
    }
    if (!(name in groups)) {
      groups[name] = [];
      labelOrder.push(name);
    }
    counters[name] = (counters[name] ?? 0) + 1;
    groups[name].push({ segmentIdx: i, tokenIndex: counters[name] });
  }
  return { groups, unlabeled, labelOrder };
}

function defaultSelectionForCondition(
  segments: readonly Segment[],
): Record<string, Record<number, boolean>> {
  const out: Record<string, Record<number, boolean>> = {};
  const counters: Record<string, number> = {};
  for (const seg of segments) {
    if (seg.segment_type !== "word") continue;
    const name = seg.assigned_name?.trim() ?? "";
    if (!name) continue;
    counters[name] = (counters[name] ?? 0) + 1;
    const tokenIdx = counters[name];
    if (!(name in out)) out[name] = {};
    out[name][tokenIdx] = seg.status === "accepted";
  }
  return out;
}

/**
 * Ensure `state.selectedTokens[spk][cond]` exists, deriving defaults from
 * the loaded segments if not. Safe to call repeatedly. No-op if no
 * segments are loaded yet (the caller is expected to await loadCondition).
 */
export function ensureSelectionInitialized(spk: string, cond: string): void {
  const cs = state.reviewProgress[spk]?.[cond];
  if (!cs || cs.segments.length === 0) return;
  const spkSlot = state.selectedTokens[spk] ?? {};
  state.selectedTokens[spk] = spkSlot;
  if (spkSlot[cond]) {
    // Already initialized; only fill in missing groups for labels that
    // appeared after the initial pass (e.g. user added a token in Review).
    const existing = spkSlot[cond];
    const grouping = groupTokensByLabel(cs.segments);
    for (const name of grouping.labelOrder) {
      if (!(name in existing)) existing[name] = {};
      for (const { tokenIndex } of grouping.groups[name]) {
        // Don't overwrite existing user choices, but newly-seen
        // (label, tokenIndex) pairs default to whatever the underlying
        // segment's accepted-status is.
        if (!(tokenIndex in existing[name])) {
          const segIdx = grouping.groups[name].find(
            (g) => g.tokenIndex === tokenIndex,
          )!.segmentIdx;
          existing[name][tokenIndex] = cs.segments[segIdx].status === "accepted";
        }
      }
    }
    return;
  }
  spkSlot[cond] = defaultSelectionForCondition(cs.segments);
}

/** Toggle a single token's selection state. */
export function toggleTokenSelection(
  spk: string,
  cond: string,
  assignedName: string,
  tokenIndex: number,
): void {
  ensureSelectionInitialized(spk, cond);
  const slot = state.selectedTokens[spk]?.[cond];
  if (!slot) return;
  if (!(assignedName in slot)) slot[assignedName] = {};
  const next = !slot[assignedName][tokenIndex];
  slot[assignedName][tokenIndex] = next;
  scheduleSessionSave();
}

/** Bulk select/deselect every token under a given assigned_name. */
export function setWordSelection(
  spk: string,
  cond: string,
  assignedName: string,
  selected: boolean,
): void {
  ensureSelectionInitialized(spk, cond);
  const cs = state.reviewProgress[spk]?.[cond];
  if (!cs) return;
  const slot = state.selectedTokens[spk]?.[cond];
  if (!slot) return;
  if (!(assignedName in slot)) slot[assignedName] = {};
  const grouping = groupTokensByLabel(cs.segments);
  const group = grouping.groups[assignedName] ?? [];
  for (const { tokenIndex } of group) {
    slot[assignedName][tokenIndex] = selected;
  }
  scheduleSessionSave();
}

/** Bulk select/deselect every eligible token in the condition. */
export function setAllSelection(
  spk: string,
  cond: string,
  selected: boolean,
): void {
  ensureSelectionInitialized(spk, cond);
  const cs = state.reviewProgress[spk]?.[cond];
  if (!cs) return;
  const slot = state.selectedTokens[spk]?.[cond];
  if (!slot) return;
  const grouping = groupTokensByLabel(cs.segments);
  for (const name of grouping.labelOrder) {
    if (!(name in slot)) slot[name] = {};
    for (const { tokenIndex } of grouping.groups[name]) {
      slot[name][tokenIndex] = selected;
    }
  }
  scheduleSessionSave();
}

export interface SelectionSummary {
  selectedCount: number;
  totalEligible: number;
  unlabeledCount: number;
  perWord: Record<string, { selected: number; total: number }>;
}

/** Summary of the current selection for a (spk, cond) pair. */
export function selectionSummary(spk: string, cond: string): SelectionSummary {
  const cs = state.reviewProgress[spk]?.[cond];
  if (!cs) {
    return { selectedCount: 0, totalEligible: 0, unlabeledCount: 0, perWord: {} };
  }
  const grouping = groupTokensByLabel(cs.segments);
  const slot = state.selectedTokens[spk]?.[cond] ?? {};
  let selectedCount = 0;
  let totalEligible = 0;
  const perWord: Record<string, { selected: number; total: number }> = {};
  for (const name of grouping.labelOrder) {
    const group = grouping.groups[name];
    let s = 0;
    for (const { tokenIndex } of group) {
      if (slot[name]?.[tokenIndex]) s++;
    }
    perWord[name] = { selected: s, total: group.length };
    selectedCount += s;
    totalEligible += group.length;
  }
  return {
    selectedCount,
    totalEligible,
    unlabeledCount: grouping.unlabeled.length,
    perWord,
  };
}

/** Convert the store's `selectedTokens` slot into the request shape that
 *  /api/export expects: { word: [tokenIndex, ...] } with empty lists
 *  dropped. */
function selectionRequestFor(
  spk: string,
  cond: string,
): Record<string, number[]> {
  const slot = state.selectedTokens[spk]?.[cond] ?? {};
  const out: Record<string, number[]> = {};
  for (const [name, indices] of Object.entries(slot)) {
    const picked: number[] = [];
    for (const [k, v] of Object.entries(indices)) {
      if (v) picked.push(Number(k));
    }
    if (picked.length > 0) {
      picked.sort((a, b) => a - b);
      out[name] = picked;
    }
  }
  return out;
}

/** Mean duration (sec) of word-typed segments that are currently selected.
 *  Used by Export.svelte for the rough output-size estimate. */
export function selectedTokenMeanDuration(): number {
  let totalMs = 0;
  let n = 0;
  for (const [spk, cspk] of Object.entries(state.selectedTokens)) {
    for (const [cond, csel] of Object.entries(cspk)) {
      const cs = state.reviewProgress[spk]?.[cond];
      if (!cs) continue;
      const grouping = groupTokensByLabel(cs.segments);
      for (const name of grouping.labelOrder) {
        for (const { segmentIdx, tokenIndex } of grouping.groups[name]) {
          if (csel[name]?.[tokenIndex]) {
            totalMs += cs.segments[segmentIdx].duration_ms;
            n++;
          }
        }
      }
    }
  }
  return n > 0 ? totalMs / n / 1000 : 0;
}

/**
 * Collect every (spk, cond) pair that has at least one selected token and
 * POST a single /api/export_all request. Result rows are pushed into
 * `state.perPairResult` as the response arrives (no streaming — the API is
 * a single response).
 *
 * Optional filters mirror the API's `speakers` / `conditions` arrays; when
 * omitted we iterate every pair currently held in the selection.
 */
export async function runExportAll(
  speakers?: string[],
  conditions?: string[],
): Promise<void> {
  if (!state.config) {
    pushToast("Load an experiment first", "warn");
    return;
  }
  if (state.exportState === "running") return;

  // Build selections shape and the pair list.
  const selections: Record<
    string,
    Record<string, Record<string, number[]>>
  > = {};
  const spkSet = speakers ? new Set(speakers) : null;
  const cndSet = conditions ? new Set(conditions) : null;
  const pairs: Array<{ spk: string; cond: string }> = [];
  for (const spk of Object.keys(state.selectedTokens)) {
    if (spkSet && !spkSet.has(spk)) continue;
    const spkSlot = state.selectedTokens[spk];
    for (const cond of Object.keys(spkSlot)) {
      if (cndSet && !cndSet.has(cond)) continue;
      const req = selectionRequestFor(spk, cond);
      if (Object.keys(req).length === 0) continue;
      if (!selections[spk]) selections[spk] = {};
      selections[spk][cond] = req;
      pairs.push({ spk, cond });
    }
  }

  if (pairs.length === 0) {
    pushToast("No tokens selected for export", "warn");
    return;
  }

  state.exportState = "running";
  // Seed perPairResult so the UI shows a "running" row for every pair we're
  // about to ask for. They'll be overwritten on response.
  state.perPairResult = pairs.map((p) => ({
    spk: p.spk,
    cond: p.cond,
    status: "running",
  }));
  state.exportOutputDir = null;

  try {
    const speakersArg = speakers ?? Array.from(new Set(pairs.map((p) => p.spk)));
    const conditionsArg =
      conditions ?? Array.from(new Set(pairs.map((p) => p.cond)));
    const resp = await apiExportAll(speakersArg, conditionsArg, selections);
    const results = resp.results ?? [];
    // Map each (spk, cond) row to its result.
    const byKey = new Map<string, unknown>();
    for (const r of results) {
      // Backend may emit ok or error rows; both carry speaker_id/condition.
      const rr = r as unknown as {
        speaker_id?: string;
        condition?: string;
      };
      if (rr.speaker_id && rr.condition) {
        byKey.set(`${rr.speaker_id}\x1F${rr.condition}`, r);
      }
    }
    const next: PerPairResult[] = pairs.map((p) => {
      const key = `${p.spk}\x1F${p.cond}`;
      const r = byKey.get(key);
      if (!r) {
        return {
          spk: p.spk,
          cond: p.cond,
          status: "error",
          error: { code: "no_result", message: "Backend returned no result for this pair." },
        };
      }
      const rr = r as unknown as {
        speaker_id?: string;
        condition?: string;
        status?: string;
        error?: { code: string; message: string };
        n_exported?: number;
        tokens_per_word?: Record<string, number>;
        skipped?: Record<string, number>;
        output_dir?: string;
      };
      if (rr.status === "error" && rr.error) {
        return {
          spk: p.spk,
          cond: p.cond,
          status: "error",
          error: rr.error,
        };
      }
      const ok = rr as unknown as ExportResponse;
      return {
        spk: p.spk,
        cond: p.cond,
        status: "ok",
        counts: {
          n_exported: ok.n_exported,
          tokens_per_word: ok.tokens_per_word,
          skipped: ok.skipped,
        },
        outputDir: ok.output_dir,
      };
    });
    state.perPairResult = next;
    // The summary panel shows a single "open output folder" path; use the
    // experiment's output_root parent (every result writes under
    // <output_root>/<spk>/<cond>/tokens). We extract by stripping the tail.
    const firstOk = next.find((p) => p.status === "ok" && p.outputDir);
    if (firstOk?.outputDir) {
      state.exportOutputDir = firstOk.outputDir;
    }
  } catch (err) {
    toastError(err, "Export failed");
    // Mark every running row as errored so the UI doesn't dangle.
    state.perPairResult = state.perPairResult.map((r) =>
      r.status === "running"
        ? {
            ...r,
            status: "error",
            error: {
              code: err instanceof ApiError ? err.code : "internal",
              message: err instanceof Error ? err.message : "Export failed",
            },
          }
        : r,
    );
  } finally {
    state.exportState = "done";
  }
}

/** Reset the export panel back to idle (called when the user navigates
 *  away from Export or wants to re-run cleanly). */
export function resetExportState(): void {
  state.exportState = "idle";
  state.perPairResult = [];
  state.exportOutputDir = null;
}

// ---- Boot sequence --------------------------------------------------------

const LS_LAST_ROOT = "clicketysplit.lastRecordingsRoot";
const LS_LAST_CONFIG = "clicketysplit.lastConfigPath";

/**
 * On boot:
 *   1. GET /api/capabilities → state.capabilities
 *   2. GET /api/config → if 200, set state.config + step jumps to 2
 *      (or whatever step the session JSON says).
 *   3. GET /api/session → restore step/active speaker/condition.
 *
 * Per 06 doc §Autosave & dirty state §On boot.
 */
export async function boot(): Promise<void> {
  if (state.booted) return;
  // Restore localStorage hints first so the Setup screen can pre-fill even
  // if /api/config returns 404.
  try {
    state.lastRecordingsRoot = localStorage.getItem(LS_LAST_ROOT);
    state.lastConfigPath = localStorage.getItem(LS_LAST_CONFIG);
  } catch {
    // SSR / private-mode: localStorage may throw. Ignore.
  }

  // Capabilities — always tried, used by Setup's detection-backend picker.
  try {
    state.capabilities = await apiGetCapabilities();
  } catch (err) {
    toastError(err, "Failed to load capabilities");
  }

  // Config — may 404 if the server was launched without --experiment AND no
  // config has been POSTed yet.
  try {
    const cfg = await apiGetConfig();
    state.config = cfg;
    state.configPath = cfg._experiment_path ?? null;
    // If a config is loaded, jump past Setup unless the session says
    // otherwise.
    state.step = 2;
  } catch (err) {
    if (!(err instanceof ApiError) || err.status !== 404) {
      toastError(err, "Failed to load config");
    }
    // No config yet — stay on step 1.
  }

  // Session — only meaningful once a config is loaded (the route requires
  // an active experiment).
  if (state.config) {
    try {
      const sess = await apiGetSession();
      if (sess && typeof sess === "object") {
        if (typeof sess.step === "number" && sess.step >= 1 && sess.step <= 4) {
          state.step = sess.step as 1 | 2 | 3 | 4;
        }
        if (typeof sess.activeSpeakerId === "string") {
          state.activeSpeakerId = sess.activeSpeakerId;
        }
        if (typeof sess.activeCondition === "string") {
          state.activeCondition = sess.activeCondition;
        }
        if (
          typeof sess.lastRecordingsRoot === "string" &&
          !state.lastRecordingsRoot
        ) {
          state.lastRecordingsRoot = sess.lastRecordingsRoot;
        }
        if (sess.selectedTokens) {
          deserializeSelections(sess.selectedTokens);
        }
      }
    } catch (err) {
      // Session is best-effort. Don't toast.
      // eslint-disable-next-line no-console
      console.warn("session load failed:", err);
    }
  }

  state.booted = true;
}

/** Persist the recordings-root hint locally so the next launch pre-fills it. */
export function rememberRecordingsRoot(root: string): void {
  state.lastRecordingsRoot = root;
  try {
    localStorage.setItem(LS_LAST_ROOT, root);
  } catch {
    /* ignore */
  }
}

/** Persist the most recently loaded config path. */
export function rememberConfigPath(path: string): void {
  state.lastConfigPath = path;
  try {
    localStorage.setItem(LS_LAST_CONFIG, path);
  } catch {
    /* ignore */
  }
}
