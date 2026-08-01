import { describe, expect, it } from "vitest";
import {
  buildScatter,
  clusterPolygons,
  convexHull,
  pointInPolygon,
  pointsInLasso,
  type Point,
} from "./semanticMap";
import type { TapestryBundleRaw } from "../../lib/data";

const bundle = {
  schemaVersion: 1,
  meta: { graph: "g", scope: "full", generatedAt: "", entityCount: 3, relationCount: 0, sections: ["semantic"] },
  entities: [
    { id: "a", name: "A", entityType: "concept" },
    { id: "b", name: "B", entityType: "claim" },
    { id: "c", name: "C", entityType: "concept" },
    { id: "novec", name: "NoVec", entityType: "concept" },
  ],
  relations: [],
  semantic: {
    method: "pca",
    projection: { a: [0, 0], b: [10, 0], c: [5, 8] }, // novec has no projection
    clusters: [{ id: 0, label: "concept", entityIds: ["a", "c"], size: 2 }],
  },
} as unknown as TapestryBundleRaw;

describe("buildScatter", () => {
  it("keeps only entities that have a projection", () => {
    const points = buildScatter(bundle);
    expect(points.map((p) => p.id).sort()).toEqual(["a", "b", "c"]);
    expect(points.find((p) => p.id === "a")).toMatchObject({ x: 0, y: 0, entityType: "concept", label: "A" });
  });
});

describe("convexHull", () => {
  it("returns the outer vertices of a point set", () => {
    const square: Point[] = [
      { x: 0, y: 0 }, { x: 4, y: 0 }, { x: 4, y: 4 }, { x: 0, y: 4 }, { x: 2, y: 2 },
    ];
    expect(convexHull(square)).toHaveLength(4); // the interior point is dropped
  });
});

describe("pointInPolygon", () => {
  const square: Point[] = [{ x: 0, y: 0 }, { x: 4, y: 0 }, { x: 4, y: 4 }, { x: 0, y: 4 }];
  it("is true inside and false outside", () => {
    expect(pointInPolygon({ x: 2, y: 2 }, square)).toBe(true);
    expect(pointInPolygon({ x: 9, y: 9 }, square)).toBe(false);
  });
});

describe("pointsInLasso", () => {
  it("returns the enclosed ids", () => {
    const polygon: Point[] = [{ x: -1, y: -1 }, { x: 6, y: -1 }, { x: 6, y: 3 }, { x: -1, y: 3 }];
    const points = [
      { id: "a", x: 0, y: 0 },
      { id: "b", x: 10, y: 0 },
      { id: "c", x: 5, y: 8 },
    ];
    expect(pointsInLasso(polygon, points)).toEqual(["a"]);
  });
});

describe("clusterPolygons", () => {
  it("hulls each cluster's positioned members", () => {
    const positions = new Map<string, Point>([
      ["a", { x: 0, y: 0 }],
      ["c", { x: 5, y: 8 }],
    ]);
    const polys = clusterPolygons(bundle.semantic!.clusters!, positions);
    expect(polys).toHaveLength(1);
    expect(polys[0]).toMatchObject({ id: 0, label: "concept" });
    expect(polys[0].hull.length).toBeGreaterThanOrEqual(2);
  });
});
