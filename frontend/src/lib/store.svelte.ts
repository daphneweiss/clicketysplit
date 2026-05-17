// Central Svelte 5 runes-based store.
//
// Per 06_FRONTEND.md §Central store, everything that needs to be reactive
// across components lives in `state`. Components import `state`,
// `activeCondState`, `currentSegment` and mutate via the action functions
// below — no other source of truth.
//
// This file uses runes (`$state`, `$derived`) and therefore must be named
// `.svelte.ts` so the Svelte compiler picks it up.

import {
  getConfig as apiGetConfig,
  getCapabilities as apiGetCapabilities,
  getSession as apiGetSession,
  saveSession as apiSaveSession,
} from "./api";
import type {
  Capabilities,
  DiscoveryResult,
  ExperimentConfig,
  LabelAnchor,
  PresentationOrder,
  Segment,
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
});

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

/**
 * Stub for now — task 10 implements the full re-anchoring algorithm in
 * lib/labeling.ts. For task 9 this just updates the segment label in place
 * and marks the condition dirty so we can wire the store API end-to-end
 * without blocking on labeling-parity work.
 */
export function setLabel(segmentIdx: number, newLabel: string): void {
  if (!state.activeSpeakerId || !state.activeCondition) return;
  const cond =
    state.reviewProgress[state.activeSpeakerId]?.[state.activeCondition];
  if (!cond) return;
  const seg = cond.segments[segmentIdx];
  if (!seg) return;
  seg.assigned_name = newLabel;
  seg.label_source = "anchor";
  cond.dirty = true;
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
    await apiSaveSession(
      {
        step: state.step,
        activeSpeakerId: state.activeSpeakerId,
        activeCondition: state.activeCondition,
        lastRecordingsRoot: state.lastRecordingsRoot,
        lastConfigPath: state.lastConfigPath,
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
