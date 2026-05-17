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
  word_segment_count: number;
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

/**
 * Build a synthetic list of N word-typed segments. Start/end times are
 * arbitrary; the labeling algorithm only inspects segment_type and order.
 */
function buildWordSegments(n: number): Segment[] {
  const segments: Segment[] = [];
  for (let i = 0; i < n; i++) {
    segments.push({
      start: i,
      end: i + 0.5,
      duration_ms: 500,
      segment_type: "word",
      assigned_name: "",
      label_source: "",
      status: "pending",
      token_index: 0,
      cluster_size: 0,
    });
  }
  return segments;
}

describe("autoLabel parity with Python", () => {
  for (const v of vectors.vectors) {
    it(v.name, () => {
      const segments = buildWordSegments(v.word_segment_count);
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
    const segments = buildWordSegments(4);
    const snapshot = JSON.parse(JSON.stringify(segments));
    autoLabel(segments, ["a", "b"], {
      presentationOrder: "cycled",
      anchors: [{ word_index: 0, label: "a", source: "initial" }],
    });
    expect(segments).toEqual(snapshot);
  });

  it("leaves non-word segments alone", () => {
    const segments: Segment[] = [
      { ...buildWordSegments(1)[0], segment_type: "short_noise" },
      ...buildWordSegments(2),
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
