import { describe, expect, it } from "vitest";

import {
  metricStandings,
  measurementsShareProtocol,
  updateCompareSelection,
  type LabComparableMeasurement,
} from "@/lib/lab-compare";

const base = (overrides: Partial<LabComparableMeasurement>): LabComparableMeasurement => ({
  model: "model-a",
  protocol_id: "lac.quick-latency.v1",
  num_ctx: 2048,
  tokens_per_second: 10,
  pre_generation_ms: 100,
  ...overrides,
});

describe("Lab comparison truth contract", () => {
  it("ranks higher throughput and lower latency with visible standings", () => {
    const rows = [
      base({ model: "model-a", tokens_per_second: 10, pre_generation_ms: 120 }),
      base({ model: "model-b", tokens_per_second: 20, pre_generation_ms: 80 }),
    ];

    expect(metricStandings(rows, "tokens_per_second", "higher")).toEqual({
      "model-a": "second",
      "model-b": "best",
    });
    expect(metricStandings(rows, "pre_generation_ms", "lower")).toEqual({
      "model-a": "second",
      "model-b": "best",
    });
  });

  it("marks equal measurements as ties", () => {
    const rows = [base({ model: "model-a" }), base({ model: "model-b" })];

    expect(metricStandings(rows, "tokens_per_second", "higher")).toEqual({
      "model-a": "tie",
      "model-b": "tie",
    });
  });

  it("does not rank missing, mismatched protocol, or mismatched context evidence", () => {
    expect(metricStandings([
      base({ model: "model-a" }),
      base({ model: "model-b", protocol_id: "legacy" }),
    ], "tokens_per_second", "higher")).toEqual({});

    expect(metricStandings([
      base({ model: "model-a" }),
      base({ model: "model-b", num_ctx: 4096 }),
    ], "tokens_per_second", "higher")).toEqual({});

    expect(metricStandings([
      base({ model: "model-a" }),
      base({ model: "model-b", tokens_per_second: undefined }),
    ], "tokens_per_second", "higher")).toEqual({});
  });

  it("separates protocol compatibility from individual missing metrics", () => {
    expect(measurementsShareProtocol([
      base({ model: "model-a", tokens_per_second: undefined }),
      base({ model: "model-b", tokens_per_second: undefined }),
    ])).toBe(true);
    expect(measurementsShareProtocol([
      base({ model: "model-a" }),
      base({ model: "model-b", num_ctx: null }),
    ])).toBe(false);
  });

  it("accepts zero-duration evidence but rejects non-positive throughput", () => {
    expect(metricStandings([
      base({ model: "model-a", pre_generation_ms: 0 }),
      base({ model: "model-b", pre_generation_ms: 2 }),
    ], "pre_generation_ms", "lower")).toEqual({
      "model-a": "best",
      "model-b": "second",
    });

    expect(metricStandings([
      base({ model: "model-a", tokens_per_second: 0 }),
      base({ model: "model-b", tokens_per_second: 2 }),
    ], "tokens_per_second", "higher")).toEqual({});
  });

  it("caps comparison selection at two distinct models", () => {
    expect(updateCompareSelection(["a", "b"], "a")).toEqual({ selection: ["b"], limitReached: false });
    expect(updateCompareSelection(["a", "b"], "c")).toEqual({ selection: ["a", "b"], limitReached: true });
    expect(updateCompareSelection(["a"], "a")).toEqual({ selection: [], limitReached: false });
    expect(updateCompareSelection(["a"], "b")).toEqual({ selection: ["a", "b"], limitReached: false });
  });
});
