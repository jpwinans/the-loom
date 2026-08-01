import { describe, expect, it } from "vitest";
import { computeOverviewStats } from "./stats";

const bundle = {
  schemaVersion: 1,
  meta: { graph: "g", scope: "full", generatedAt: "", entityCount: 3, relationCount: 2, sections: ["analytics"] },
  entities: [
    { id: "a", name: "A", entityType: "concept", confidence: { score: 0.95 } },
    { id: "b", name: "B", entityType: "claim", confidence: { score: 0.15 } },
    { id: "c", name: "C", entityType: "claim" },
  ],
  relations: [
    { id: "r1", from: "a", to: "b", relationType: "contradicts" },
    { id: "r2", from: "a", to: "ghost", relationType: "supports" },
  ],
  analytics: { centrality: { degree: {}, betweenness: {}, pagerank: { a: 0.5, b: 0.3, c: 0.2 } }, components: [["a", "b", "c"]], loops: [], leveragePoints: [], bridges: [] },
} as never;

describe("computeOverviewStats", () => {
  it("counts types, contradictions, and dangling relations", () => {
    const stats = computeOverviewStats(bundle);
    expect(stats.typeCounts).toEqual({ concept: 1, claim: 2 });
    expect(stats.contradictionCount).toBe(1);
    expect(stats.danglingRelationCount).toBe(1);
    expect(stats.confidenceHistogram[9]).toBe(1); // 0.95 → last bin
    expect(stats.confidenceHistogram[1]).toBe(1); // 0.15 → second bin
    expect(stats.topCentral[0]).toMatchObject({ id: "a", name: "A", score: 0.5 });
  });
});
