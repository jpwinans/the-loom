import Graph from "graphology";
import { describe, expect, it } from "vitest";
import type { TapestryBundleRaw } from "../../lib/data";
import { buildGraph } from "./buildGraph";

const bundle = {
  schemaVersion: 1,
  meta: { graph: "g", scope: "full", generatedAt: "", entityCount: 2, relationCount: 1, sections: [] },
  entities: [
    { id: "a", name: "A", entityType: "concept" },
    { id: "b", name: "B", entityType: "claim", confidence: { score: 0.9 } },
  ],
  relations: [
    { id: "r1", from: "a", to: "b", relationType: "supports", strength: "strong" },
  ],
} as unknown as TapestryBundleRaw;

describe("buildGraph", () => {
  it("builds nodes and edges with visual attributes", () => {
    const graph: Graph = buildGraph(bundle);
    expect(graph.order).toBe(2);
    expect(graph.size).toBe(1);
    expect(graph.getNodeAttribute("a", "entityType")).toBe("concept");
    expect(graph.getNodeAttribute("b", "confidence")).toBe(0.9);
    expect(graph.getNodeAttribute("a", "size")).toBeGreaterThan(0);
    expect(graph.getEdgeAttribute("r1", "relationType")).toBe("supports");
    expect(typeof graph.getNodeAttribute("a", "community")).toBe("number");
  });
  it("skips dangling relations", () => {
    const broken = { ...bundle, relations: [{ id: "r2", from: "a", to: "zzz", relationType: "causes" }] };
    expect(buildGraph(broken as never).size).toBe(0);
  });
});
