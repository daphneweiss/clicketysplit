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
// If the algorithm here ever disagrees with the JSON vector file, the
// bug is in this port — match the Python output.
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
 * presentation_order, expected_reps_per_stimulus=2, anchors=None)`.
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
      options.expectedRepsPerStimulus ?? 2,
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

/**
 * Split word positions into `nStim` clusters at the largest gaps.
 *
 * Port of Python `_gap_clusters` (labeling.py), itself a transcription of the
 * original tool's classify_and_label: speakers pause briefly between
 * repetitions of the same word and longer when switching words, so the
 * `nStim - 1` widest gaps are the word-switch points. Falls back to fixed
 * `k`-sized chunks when there are too few gaps to split on.
 *
 * Returns clusters of word positions (indices into `wIdx`). Both sorts are
 * stable in JS and Python, so tie behavior on equal gaps matches too.
 */
function gapClusters(
  segments: readonly Segment[],
  wIdx: readonly number[],
  nStim: number,
  k: number,
): number[][] {
  const nWords = wIdx.length;
  const nSplits = nStim - 1;
  if (nWords === 0) return [];
  const range = (lo: number, hi: number): number[] =>
    Array.from({ length: hi - lo }, (_, i) => lo + i);
  if (nSplits <= 0) return [range(0, nWords)];

  const gaps: Array<[number, number]> = [];
  for (let wi = 1; wi < nWords; wi++) {
    gaps.push([segments[wIdx[wi]].start - segments[wIdx[wi - 1]].end, wi]);
  }

  if (gaps.length < nSplits) {
    const kk = Math.max(1, Math.floor(k));
    const chunks: number[][] = [];
    for (let i = 0; i < nWords; i += kk) {
      chunks.push(range(i, Math.min(i + kk, nWords)));
    }
    return chunks;
  }

  const widest = [...gaps].sort((a, b) => b[0] - a[0]).slice(0, nSplits);
  const splitPositions = widest.map((g) => g[1]).sort((a, b) => a - b);

  const clusters: number[][] = [];
  let prev = 0;
  for (const sp of splitPositions) {
    clusters.push(range(prev, sp));
    prev = sp;
  }
  clusters.push(range(prev, nWords));
  return clusters.filter((c) => c.length > 0);
}

/** Index of the cluster containing `wordPosition`, or null. */
function clusterOf(clusters: number[][], wordPosition: number): number | null {
  for (let cIdx = 0; cIdx < clusters.length; cIdx++) {
    if (clusters[cIdx].includes(wordPosition)) return cIdx;
  }
  return null;
}

/**
 * Label blocked repetitions by clustering on inter-token gaps.
 *
 * Line-for-line port of Python `_walk_blocked` (labeling.py). Consuming
 * exactly `k` tokens per stimulus drifts irrecoverably when a speaker
 * produces more or fewer repetitions than scripted, so cluster boundaries
 * come from the recording's own timing and `k` is only the fallback hint.
 * Anchors pin their whole cluster's stimulus; later clusters continue
 * forward from there. Empty-label anchors blank their range through to the
 * next anchor.
 */
function walkBlocked(
  segments: Segment[],
  wIdx: readonly number[],
  anchors: readonly LabelAnchor[],
  stimulusList: readonly string[],
  k: number,
): void {
  const nWords = wIdx.length;
  const nStim = stimulusList.length;
  if (nWords === 0 || nStim === 0) return;

  const clusters = gapClusters(segments, wIdx, nStim, k);
  if (clusters.length === 0) return;

  const anchoredPositions = new Set(
    anchors.filter((a) => a.label !== "").map((a) => a.word_index),
  );

  // An anchor re-pins the stimulus for its cluster; clusters after it walk
  // forward from that new position.
  const anchorByCluster = new Map<number, number>();
  for (const a of anchors) {
    if (a.label === "" || !stimulusList.includes(a.label)) continue;
    const cIdx = clusterOf(clusters, a.word_index);
    if (cIdx !== null) anchorByCluster.set(cIdx, stimulusList.indexOf(a.label));
  }

  let stimIdx = anchorByCluster.get(0) ?? 0;
  for (let cIdx = 0; cIdx < clusters.length; cIdx++) {
    const pinned = anchorByCluster.get(cIdx);
    if (pinned !== undefined) stimIdx = pinned;

    const name = stimIdx >= 0 && stimIdx < nStim ? stimulusList[stimIdx] : "";
    const cluster = clusters[cIdx];
    for (let tokenPos = 0; tokenPos < cluster.length; tokenPos++) {
      const seg = segments[wIdx[cluster[tokenPos]]];
      seg.assigned_name = name;
      seg.label_source = anchoredPositions.has(cluster[tokenPos])
        ? "anchor"
        : "auto";
      seg.token_index = tokenPos + 1;
      seg.cluster_size = cluster.length;
    }

    stimIdx++;
  }

  // Empty-label anchors halt forward labeling: every word from the anchor's
  // index up to the next anchor stays blank. Applied last so the cluster
  // assignment above cannot overwrite the halted range.
  for (let aPos = 0; aPos < anchors.length; aPos++) {
    const a = anchors[aPos];
    if (a.label !== "") continue;
    const haltEnd =
      aPos + 1 < anchors.length ? anchors[aPos + 1].word_index : nWords;
    for (let wp = a.word_index; wp < haltEnd; wp++) {
      const seg = segments[wIdx[wp]];
      seg.assigned_name = "";
      seg.label_source = wp === a.word_index ? "anchor" : "";
      seg.token_index = 0;
      seg.cluster_size = 0;
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
