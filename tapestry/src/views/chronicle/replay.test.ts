import Graph from "graphology";
import { describe, expect, it } from "vitest";
import { buildTimeline, diffStates, stateAt } from "./replay";
import type { TapestryBundleRaw } from "../../lib/data";

// Three instants: create a & b + edge (t=1000), update a (t=2000),
// deprecate b (t=3000).
const bundle = {
  schemaVersion: 1,
  meta: { graph: "g", scope: "full", generatedAt: "", entityCount: 2, relationCount: 1, sections: ["temporal"] },
  entities: [
    { id: "a", name: "A", entityType: "concept", status: "active" },
    { id: "b", name: "B", entityType: "claim", status: "deprecated" },
  ],
  relations: [{ id: "e1", from: "a", to: "b", relationType: "supports" }],
  temporal: {
    events: [
      { id: "1000-0", at: "1970-01-01T00:00:01.000Z", type: "entity_created", payload: { entity: { id: "a" } } },
      { id: "1000-1", at: "1970-01-01T00:00:01.000Z", type: "entity_created", payload: { entity: { id: "b" } } },
      { id: "1000-2", at: "1970-01-01T00:00:01.000Z", type: "relation_created", payload: { relation: { id: "e1", from: "a", to: "b" } } },
      { id: "2000-0", at: "1970-01-01T00:00:02.000Z", type: "entity_updated", payload: { entity: { id: "a" } } },
      { id: "3000-0", at: "1970-01-01T00:00:03.000Z", type: "entity_status_changed", payload: { entity: { id: "b", status: "deprecated" } } },
    ],
  },
} as unknown as TapestryBundleRaw;

function currentGraph(): Graph {
  const g = new Graph({ multi: true, type: "directed" });
  g.addNode("a", {}); g.addNode("b", {});
  g.addEdgeWithKey("e1", "a", "b", {});
  return g;
}

describe("stateAt", () => {
  it("shows nothing before the first event", () => {
    const t = buildTimeline(bundle);
    const s = stateAt(t, currentGraph(), 500);
    expect(s.visibleNodes.size).toBe(0);
    expect(s.visibleEdges.size).toBe(0);
  });
  it("at the end equals the current graph and statuses", () => {
    const t = buildTimeline(bundle);
    const s = stateAt(t, currentGraph(), t.end);
    expect([...s.visibleNodes].sort()).toEqual(["a", "b"]);
    expect(s.visibleEdges.has("e1")).toBe(true);
    expect(s.statusById.get("b")).toBe("deprecated");
    expect(s.statusById.get("a") ?? "active").toBe("active");
  });
  it("b is still active at t=2500, deprecated at t=3000", () => {
    const t = buildTimeline(bundle);
    expect(stateAt(t, currentGraph(), 2500).statusById.get("b") ?? "active").toBe("active");
    expect(stateAt(t, currentGraph(), 3000).statusById.get("b")).toBe("deprecated");
  });
});

describe("diffStates", () => {
  it("classifies added / changed / invalidated across a window", () => {
    const t = buildTimeline(bundle);
    const d = diffStates(t, 1500, 3000);
    expect([...d.added]).toEqual([]);        // nothing created after 1500
    expect([...d.changed]).toEqual(["a"]);   // a updated at 2000
    expect([...d.invalidated]).toEqual(["b"]); // b deprecated at 3000
  });
  it("counts creations as added", () => {
    const t = buildTimeline(bundle);
    expect([...diffStates(t, 0, 1500).added].sort()).toEqual(["a", "b"]);
  });
});
