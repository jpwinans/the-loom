import { describe, expect, it } from "vitest";

import { humanizeType, legendRows } from "./legendRows";

describe("legendRows", () => {
  it("lists only types present in the graph", () => {
    const rows = legendRows(new Map([["concept", 4], ["evidence", 29]]));
    expect(rows.map((r) => r.type)).toEqual(["concept", "evidence"]);
  });

  it("orders by the model enum, not by count or insertion", () => {
    // Inserted in reverse-model order with counts that would sort differently.
    const rows = legendRows(
      new Map([
        ["hypothesis", 5],
        ["evidence", 29],
        ["concept", 4],
      ]),
    );
    // ENTITY_TYPES order: concept … evidence … hypothesis
    expect(rows.map((r) => r.type)).toEqual(["concept", "evidence", "hypothesis"]);
  });

  it("carries counts through", () => {
    const rows = legendRows(new Map([["source", 27]]));
    expect(rows).toEqual([{ type: "source", count: 27 }]);
  });

  it("omits types with a zero count", () => {
    const rows = legendRows(new Map([["concept", 0], ["claim", 3]]));
    expect(rows.map((r) => r.type)).toEqual(["claim"]);
  });

  it("appends unrecognised types rather than dropping them", () => {
    // Legacy or forward-compatible data must still appear in the key, or the
    // legend under-reports what is on the canvas.
    const rows = legendRows(new Map([["evidence", 2], ["zeta_custom", 1], ["alpha_custom", 1]]));
    expect(rows.map((r) => r.type)).toEqual(["evidence", "alpha_custom", "zeta_custom"]);
  });

  it("is empty for an empty graph", () => {
    expect(legendRows(new Map())).toEqual([]);
  });
});

describe("humanizeType", () => {
  it("replaces underscores for display", () => {
    expect(humanizeType("leverage_point")).toBe("leverage point");
    expect(humanizeType("inference_trace")).toBe("inference trace");
  });

  it("leaves single-word types alone", () => {
    expect(humanizeType("concept")).toBe("concept");
  });
});
