import { describe, expect, it } from "vitest";
import Graph from "graphology";
import { applyFilters } from "./filters";

function tinyGraph(): Graph {
  const g = new Graph({ multi: true, type: "directed" });
  g.addNode("a", { entityType: "concept", confidence: 0.9, status: "active" });
  g.addNode("b", { entityType: "claim", confidence: 0.4, status: "active" });
  g.addEdgeWithKey("r1", "a", "b", { relationType: "supports" });
  return g;
}

describe("applyFilters", () => {
  it("empty filters keep everything", () => {
    const { visibleNodes, visibleEdges } = applyFilters(tinyGraph(), {
      entityTypes: [],
      relationTypes: [],
      confidenceMin: 0,
      statuses: [],
    });
    expect(visibleNodes.size).toBe(2);
    expect(visibleEdges.size).toBe(1);
  });
  it("confidence floor hides low-confidence nodes and their edges", () => {
    const { visibleNodes, visibleEdges } = applyFilters(tinyGraph(), {
      entityTypes: [],
      relationTypes: [],
      confidenceMin: 0.5,
      statuses: [],
    });
    expect(visibleNodes.has("b")).toBe(false);
    expect(visibleEdges.size).toBe(0);
  });
  it("relation type filter hides non-matching edges only", () => {
    const { visibleNodes, visibleEdges } = applyFilters(tinyGraph(), {
      entityTypes: [],
      relationTypes: ["causes"],
      confidenceMin: 0,
      statuses: [],
    });
    expect(visibleNodes.size).toBe(2);
    expect(visibleEdges.size).toBe(0);
  });
});
