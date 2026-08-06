// Parity contract: the TS auto-label port MUST match every vector in
// tests/labeling_test_vectors.json byte-for-byte. The Python implementation
// is the reference; if a vector fails here, the bug is in our TS code.

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { autoLabel } from "../src/lib/labeling";
import type { LabelAnchor, PresentationOrder, Segment } from "../src/lib/types";

interface Vector {
  name: string;
  presentation_order: PresentationOrder;
  expected_reps_per_stimulus: number;
  stimulus_list: string[];
  /** Explicit real timings — present on blocked vectors, whose labeling
   * clusters on inter-token gaps. */
  segments?: Array<{ start: number; end: number }>;
  /** Synthetic uniform stride — used by cycled/random vectors, whose walks
   * ignore timing. */
  word_segment_count?: number;
  anchors: LabelAnchor[];
  expected_labels: string[];
}

interface VectorFile {
  schema_version: number;
  vectors: Vector[];
}

const here = dirname(fileURLToPath(import.meta.url));
// frontend/tests/labeling.test.ts → repo root → tests/labeling_test_vectors.json
const vectorsPath = resolve(here, "../../tests/labeling_test_vectors.json");
const vectors = JSON.parse(readFileSync(vectorsPath, "utf-8")) as VectorFile;

function wordSegment(start: number, end: number): Segment {
  return {
    start,
    end,
    duration_ms: Math.round((end - start) * 10000) / 10,
    segment_type: "word",
    assigned_name: "",
    label_source: "",
    status: "pending",
    token_index: 0,
    cluster_size: 0,
  };
}

/**
 * Build the vector's word segments. Blocked vectors carry explicit real
 * timings (gap clustering inspects them); cycled/random vectors give a
 * count and get a uniform 1 s stride.
 */
function buildVectorSegments(v: Vector): Segment[] {
  if (v.segments) {
    return v.segments.map((s) => wordSegment(s.start, s.end));
  }
  const segments: Segment[] = [];
  for (let i = 0; i < (v.word_segment_count ?? 0); i++) {
    segments.push(wordSegment(i, i + 0.5));
  }
  return segments;
}

describe("autoLabel parity with Python", () => {
  for (const v of vectors.vectors) {
    it(v.name, () => {
      const segments = buildVectorSegments(v);
      const result = autoLabel(segments, v.stimulus_list, {
        presentationOrder: v.presentation_order,
        expectedRepsPerStimulus: v.expected_reps_per_stimulus,
        anchors: v.anchors,
      });
      const labels = result.map((s) => s.assigned_name);
      expect(labels).toEqual(v.expected_labels);
    });
  }
});

describe("autoLabel side-effects", () => {
  it("does not mutate the input segments array", () => {
    const segments = buildVectorSegments({ word_segment_count: 4 } as Vector);
    const snapshot = JSON.parse(JSON.stringify(segments));
    autoLabel(segments, ["a", "b"], {
      presentationOrder: "cycled",
      anchors: [{ word_index: 0, label: "a", source: "initial" }],
    });
    expect(segments).toEqual(snapshot);
  });

  it("leaves non-word segments alone", () => {
    const segments: Segment[] = [
      { ...wordSegment(0, 0.5), segment_type: "short_noise" },
      wordSegment(1, 1.5),
      wordSegment(2, 2.5),
    ];
    const result = autoLabel(segments, ["a", "b"], {
      presentationOrder: "cycled",
      anchors: [{ word_index: 0, label: "a", source: "initial" }],
    });
    expect(result[0].assigned_name).toBe("");
    expect(result[0].label_source).toBe("");
    expect(result[1].assigned_name).toBe("a");
    expect(result[2].assigned_name).toBe("b");
  });
});
