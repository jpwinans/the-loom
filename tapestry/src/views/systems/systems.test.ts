import Graph from "graphology";
import { describe, expect, it } from "vitest";
import {
  buildCausalGraph,
  flowIntensity,
  leverageTargets,
  loopEdgeKeys,
  type LoopInfo,
} from "./systems";
import type { TapestryBundleRaw } from "../../lib/data";

const bundle = {
  schemaVersion: 1,
  meta: { graph: "g", scope: "full", generatedAt: "", entityCount: 4, relationCount: 4, sections: [] },
  entities: [
    { id: "a", name: "A", entityType: "variable" },
    { id: "b", name: "B", entityType: "variable" },
    { id: "c", name: "C", entityType: "variable" },
    { id: "lp", name: "Signal clarity", entityType: "leverage_point",
      _metadata: { level: 6, depthCategory: "shallow", meadowsName: "Information flows" } },
  ],
  relations: [
    { id: "e1", from: "a", to: "b", relationType: "causes", polarity: "+" },
    { id: "e2", from: "b", to: "c", relationType: "inhibits", polarity: "-" },
    { id: "e3", from: "c", to: "a", relationType: "causes", polarity: "+" },
    { id: "e4", from: "lp", to: "b", relationType: "part_of" }, // structural — not causal
  ],
} as unknown as TapestryBundleRaw;

const loop: LoopInfo = {
  id: null, name: "ABC Balancing Loop", classification: "balancing", netPolarity: "-",
  memberCount: 3, path: ["a", "b", "c", "a"], memberIds: ["a", "b", "c"],
};

describe("buildCausalGraph", () => {
  it("keeps only causal edges and their endpoints", () => {
    const g: Graph = buildCausalGraph(bundle);
    expect(g.order).toBe(3); // a, b, c — not the leverage point (no causal edge)
    expect(g.size).toBe(3); // e1, e2, e3 — not the part_of edge
    expect(g.getEdgeAttribute("e2", "polarity")).toBe("-");
    expect(g.hasNode("lp")).toBe(false);
  });
});

describe("loopEdgeKeys", () => {
  it("returns the loop's directed edge keys in path order", () => {
    expect(loopEdgeKeys(loop, buildCausalGraph(bundle))).toEqual(["e1", "e2", "e3"]);
  });
});

describe("leverageTargets", () => {
  it("maps each leverage point to its part_of target", () => {
    const marks = leverageTargets(bundle);
    expect(marks.get("b")).toMatchObject({ level: 6, pointName: "Signal clarity" });
    expect(marks.has("a")).toBe(false);
  });
});

describe("flowIntensity", () => {
  it("peaks on one edge and wraps around the cycle", () => {
    expect(flowIntensity(0, 3, 0)).toBeCloseTo(1, 5);
    expect(flowIntensity(0, 3, 1)).toBeCloseTo(1, 5); // phase 1 wraps back to edge 0
    expect(flowIntensity(1, 3, 0)).toBeLessThan(flowIntensity(0, 3, 0));
  });
});
