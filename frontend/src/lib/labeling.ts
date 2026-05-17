// TypeScript port of clicketysplit.detection.labeling.auto_label.
//
// This module is the contract-bound counterpart to the Python labeling
// engine in src/clicketysplit/detection/labeling.py. The shared test corpus
// lives at tests/labeling_test_vectors.json at the repo root; both
// implementations must produce identical output for every vector.
//
// Anchors are keyed by `word_index` — the zero-based position among
// WORD-TYPED segments. Non-word segments (`short_noise`, `crosstalk`,
// `intro`) are never re-typed or relabeled by this routine.
//
// See _design/03_AUDIO_AND_DETECTION.md §Labeling for the algorithm and
// worked examples. If the algorithm here ever disagrees with the JSON
// vector file, the bug is in this port — match the Python output.
//
// This file is intentionally pure (no DOM, no fetch, no Svelte runes) so
// it can run in vitest, in a Web Worker, or in the main UI without setup.

import type { LabelAnchor, PresentationOrder, Segment } from "./types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Return the absolute segment indices of all word-typed segments, in
 * document order. The list's length is the count of word-typed segments,
 * and `word_index` anchors index into it.
 */
function wordIndices(segments: readonly Segment[]): number[] {
  const out: number[] = [];
  for (let i = 0; i < segments.length; i++) {
    if (segments[i].segment_type === "word") out.push(i);
  }
  return out;
}

/**
 * Sort, range-check, dedupe, and stimulus-validate anchors.
 *
 * - Anchors with `word_index` outside [0, n_words) are silently dropped.
 * - Anchors with a non-empty label that is not in `stimulus_list` are
 *   silently dropped (match the Python implementation; a typo doesn't
 *   poison the walk).
 * - Duplicate word_index entries: later ones win, matching the Python
 *   `deduped: dict[int, LabelAnchor]` pass.
 */
function validateAnchors(
  anchors: readonly LabelAnchor[],
  nWords: number,
  stimulusList: readonly string[],
): LabelAnchor[] {
  const stim = new Set(stimulusList);
  const dedup = new Map<number, LabelAnchor>();
  for (const a of anchors) {
    if (a.word_index < 0 || a.word_index >= nWords) continue;
    if (a.label !== "" && !stim.has(a.label)) continue;
    dedup.set(a.word_index, a);
  }
  return Array.from(dedup.values()).sort((a, b) => a.word_index - b.word_index);
}

// ---------------------------------------------------------------------------
// Public entry point
// ---------------------------------------------------------------------------

export interface AutoLabelOptions {
  presentationOrder: PresentationOrder;
  expectedRepsPerStimulus?: number;
  anchors?: readonly LabelAnchor[];
}

/**
 * Re-label word-typed segments using a forward walk from anchors.
 *
 * Returns a NEW array of segment objects; the input is not mutated. Only
 * `assigned_name` and `label_source` are touched on each output segment;
 * other fields (start/end/status/...) are copied through.
 *
 * Mirrors Python `auto_label(segments, stimulus_list, *,
 * presentation_order, expected_reps_per_stimulus=3, anchors=None)`.
 */
export function autoLabel(
  segments: readonly Segment[],
  stimulusList: readonly string[],
  options: AutoLabelOptions,
): Segment[] {
  // Shallow-clone every segment so callers' inputs stay untouched.
  const out: Segment[] = segments.map((s) => ({ ...s }));

  const wIdx = wordIndices(out);
  const nWords = wIdx.length;

  // Reset auto-label state on every word-typed segment before walking.
  for (const i of wIdx) {
    out[i].assigned_name = "";
    out[i].label_source = "";
  }

  if (nWords === 0) return out;

  const validatedAnchors = validateAnchors(
    options.anchors ?? [],
    nWords,
    stimulusList,
  );

  // Project anchors onto their segments first; they always win regardless
  // of presentation order.
  const anchorByWi = new Map<number, LabelAnchor>();
  for (const a of validatedAnchors) {
    anchorByWi.set(a.word_index, a);
    out[wIdx[a.word_index]].assigned_name = a.label;
    out[wIdx[a.word_index]].label_source = "anchor";
  }

  if (options.presentationOrder === "random") {
    return out;
  }

  if (stimulusList.length === 0) {
    return out;
  }

  // For cycled/blocked, add an implicit anchor at word_index 0 if none.
  let anchors = validatedAnchors;
  if (!anchorByWi.has(0)) {
    const implicit: LabelAnchor = {
      word_index: 0,
      label: stimulusList[0],
      source: "initial",
    };
    anchors = [implicit, ...anchors].sort(
      (a, b) => a.word_index - b.word_index,
    );
    anchorByWi.set(0, implicit);
    out[wIdx[0]].assigned_name = implicit.label;
    out[wIdx[0]].label_source = "anchor";
  }

  if (options.presentationOrder === "cycled") {
    walkCycled(out, wIdx, anchors, stimulusList);
  } else if (options.presentationOrder === "blocked") {
    walkBlocked(
      out,
      wIdx,
      anchors,
      stimulusList,
      options.expectedRepsPerStimulus ?? 3,
    );
  }

  return out;
}

// ---------------------------------------------------------------------------
// cycled / blocked walks
// ---------------------------------------------------------------------------

function walkCycled(
  segments: Segment[],
  wIdx: readonly number[],
  anchors: readonly LabelAnchor[],
  stimulusList: readonly string[],
): void {
  const nWords = wIdx.length;
  const nStim = stimulusList.length;

  for (let aPos = 0; aPos < anchors.length; aPos++) {
    const a = anchors[aPos];
    if (a.label === "") continue; // empty anchor halts forward labeling

    const baseStimIdx = stimulusList.indexOf(a.label);
    if (baseStimIdx < 0) continue;

    const nextAWi =
      aPos + 1 < anchors.length ? anchors[aPos + 1].word_index : null;

    for (let wi = a.word_index + 1; wi < nWords; wi++) {
      if (nextAWi !== null && wi >= nextAWi) break;
      const segIdx = wIdx[wi];
      if (nextAWi !== null) {
        // Between two anchors: hold the earlier anchor's label.
        segments[segIdx].assigned_name = a.label;
        segments[segIdx].label_source = "auto";
      } else {
        const stride = wi - a.word_index;
        segments[segIdx].assigned_name =
          stimulusList[((baseStimIdx + stride) % nStim + nStim) % nStim];
        segments[segIdx].label_source = "auto";
      }
    }
  }
}

function walkBlocked(
  segments: Segment[],
  wIdx: readonly number[],
  anchors: readonly LabelAnchor[],
  stimulusList: readonly string[],
  k: number,
): void {
  const nWords = wIdx.length;
  const nStim = stimulusList.length;
  const reps = Math.max(1, Math.floor(k));

  for (let aPos = 0; aPos < anchors.length; aPos++) {
    const a = anchors[aPos];
    if (a.label === "") continue;

    const baseStimIdx = stimulusList.indexOf(a.label);
    if (baseStimIdx < 0) continue;

    const nextAWi =
      aPos + 1 < anchors.length ? anchors[aPos + 1].word_index : null;

    // The anchored token is rep 1 of base_stim (per Python implementation).
    let currentStimIdx = baseStimIdx;
    let count = 1;

    for (let wi = a.word_index + 1; wi < nWords; wi++) {
      if (nextAWi !== null && wi >= nextAWi) break;
      const segIdx = wIdx[wi];
      if (nextAWi !== null) {
        // Between anchors: hold the anchor's stimulus the whole way.
        segments[segIdx].assigned_name = a.label;
        segments[segIdx].label_source = "auto";
        continue;
      }

      if (count >= reps) {
        currentStimIdx = (currentStimIdx + 1) % nStim;
        count = 0;
      }
      segments[segIdx].assigned_name = stimulusList[currentStimIdx];
      segments[segIdx].label_source = "auto";
      count++;
    }
  }
}

// ---------------------------------------------------------------------------
// Convenience: project anchors out of segments for round-tripping
// ---------------------------------------------------------------------------

/**
 * Helper for tests / round-trip checks: count word-typed segments before
 * the given absolute segment index. Returns the `word_index` you would
 * assign to a NEW anchor on this segment. Returns -1 if the segment is
 * not word-typed.
 */
export function wordIndexOf(
  segments: readonly Segment[],
  segmentIdx: number,
): number {
  if (segmentIdx < 0 || segmentIdx >= segments.length) return -1;
  if (segments[segmentIdx].segment_type !== "word") return -1;
  let count = 0;
  for (let i = 0; i < segmentIdx; i++) {
    if (segments[i].segment_type === "word") count++;
  }
  return count;
}
