// WebAudio playback engine + small reactive store for "is playing" state.
//
// The engine owns exactly one shared AudioContext (created lazily on first
// playback to satisfy browser autoplay rules) and at most one active
// `AudioBufferSourceNode`. Starting a new playback stops any previous one.
//
// Components subscribe to `playbackState` (a runes-backed object) to render
// a "playing" indicator and react to natural end-of-segment events.

import { decodeAudioBuffer } from "./waveform";

export interface PlaybackState {
  isPlaying: boolean;
  startSec: number;
  endSec: number;
  // A bump-on-every-event counter so $effect blocks can react to
  // stop/end-of-playback without comparing booleans explicitly.
  generation: number;
}

// Plain reactive object — `$state(...)` wraps it on the consuming side.
// (We can't export a `$state` rune directly from a non-`.svelte.ts` module,
// and audio.ts is intentionally pure TS so it can run in tests.)
const _state: PlaybackState = {
  isPlaying: false,
  startSec: 0,
  endSec: 0,
  generation: 0,
};

/** Read-only view of the current playback state. */
export function getPlaybackState(): Readonly<PlaybackState> {
  return _state;
}

type Listener = (s: Readonly<PlaybackState>) => void;
const _listeners = new Set<Listener>();

/**
 * Subscribe to playback state changes. Returns an unsubscribe function.
 * Components typically combine this with $state for reactivity.
 */
export function subscribePlayback(listener: Listener): () => void {
  _listeners.add(listener);
  // Initial fire so the caller gets the current state immediately.
  listener(_state);
  return () => {
    _listeners.delete(listener);
  };
}

function notify(): void {
  _state.generation++;
  for (const l of _listeners) l(_state);
}

// ---------------------------------------------------------------------------
// AudioContext / source-node lifecycle
// ---------------------------------------------------------------------------

let _ctx: AudioContext | null = null;
let _source: AudioBufferSourceNode | null = null;

function ctx(): AudioContext {
  if (_ctx === null) {
    // Use the standard constructor; webkit prefix support is irrelevant for
    // the browsers we target (Chrome/Firefox/Safari current).
    _ctx = new AudioContext();
  }
  return _ctx;
}

/** Stop any in-flight playback. Idempotent. */
export function stop(): void {
  if (_source) {
    try {
      _source.onended = null;
      _source.stop();
    } catch {
      // Already stopped; ignore.
    }
    try {
      _source.disconnect();
    } catch {
      /* ignore */
    }
    _source = null;
  }
  if (_state.isPlaying) {
    _state.isPlaying = false;
    notify();
  }
}

/**
 * Play `buffer` from `startSec` to `endSec`. Cancels any prior playback.
 * Updates the reactive playback state; resets to not-playing on natural end.
 */
export function play(
  buffer: AudioBuffer,
  startSec: number,
  endSec: number,
): void {
  stop();
  const duration = Math.max(0, endSec - startSec);
  if (duration <= 0) return;
  const c = ctx();
  // Resume the context if the browser suspended it (autoplay policy).
  if (c.state === "suspended") {
    void c.resume();
  }
  const node = c.createBufferSource();
  node.buffer = buffer;
  node.connect(c.destination);
  node.onended = (): void => {
    if (_source === node) {
      _source = null;
      _state.isPlaying = false;
      notify();
    }
  };
  _source = node;
  _state.startSec = startSec;
  _state.endSec = endSec;
  _state.isPlaying = true;
  notify();
  node.start(0, startSec, duration);
}

// ---------------------------------------------------------------------------
// Decode helper (delegates to waveform.ts which owns the AudioContext for
// decoding too — keeps a single canonical instance).
// ---------------------------------------------------------------------------

/**
 * Convenience re-export: fetch and decode a URL into an AudioBuffer using
 * the shared AudioContext.
 */
export async function decode(url: string): Promise<AudioBuffer> {
  return decodeAudioBuffer(url, ctx());
}
