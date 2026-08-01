import Graph from "graphology";
import { describe, expect, it } from "vitest";
import { findPath } from "./pathMode";

describe("findPath", () => {
  it("finds a shortest path and its edge keys", () => {
    const g = new Graph({ multi: true, type: "directed" });
    ["a", "b", "c"].forEach((n) => g.addNode(n));
    g.addEdgeWithKey("r1", "a", "b", {});
    g.addEdgeWithKey("r2", "b", "c", {});
    expect(findPath(g, "a", "c")).toEqual({ nodes: ["a", "b", "c"], edges: ["r1", "r2"] });
  });
  it("returns null when no path exists", () => {
    const g = new Graph();
    g.addNode("a");
    g.addNode("z");
    expect(findPath(g, "a", "z")).toBeNull();
  });
});
