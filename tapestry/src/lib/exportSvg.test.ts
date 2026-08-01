import Graph from "graphology";
import { describe, expect, it } from "vitest";
import { allVisible, exportFilename, graphToSvg } from "./exportSvg";

describe("graphToSvg", () => {
  it("emits only visible elements with positions", () => {
    const g = new Graph({ multi: true, type: "directed" });
    g.addNode("a", { x: 0, y: 0, size: 5, color: "#123456", label: "A" });
    g.addNode("b", { x: 10, y: 10, size: 5, color: "#654321", label: "B" });
    g.addEdgeWithKey("r1", "a", "b", { color: "#999999", size: 1 });
    const svg = graphToSvg(
      g,
      { visibleNodes: new Set(["a"]), visibleEdges: new Set() },
      { x: 0, y: 0, ratio: 1 },
    );
    expect(svg).toContain("<svg");
    expect(svg).toContain("#123456");
    expect(svg).not.toContain("#654321");
  });

  it("draws a legend block when one is provided", () => {
    const g = new Graph({ multi: true, type: "directed" });
    g.addNode("a", { x: 0, y: 0, size: 5, color: "#123456", label: "A" });
    const svg = graphToSvg(
      g,
      { visibleNodes: new Set(["a"]), visibleEdges: new Set() },
      { x: 0, y: 0, ratio: 1 },
      { legend: [{ label: "concept", color: "#2f95e9" }] },
    );
    expect(svg).toContain("concept");
    expect(svg).toContain("#2f95e9");
  });

  it("omits the legend group entirely when none is provided", () => {
    const g = new Graph({ multi: true, type: "directed" });
    g.addNode("a", { x: 0, y: 0, size: 5, color: "#123456", label: "A" });
    const svg = graphToSvg(
      g,
      { visibleNodes: new Set(["a"]), visibleEdges: new Set() },
      { x: 0, y: 0, ratio: 1 },
    );
    expect(svg).not.toContain("export-legend");
  });
});

describe("exportFilename", () => {
  it("builds a dated, view- and extension-specific filename", () => {
    expect(exportFilename("g", "systems", "svg")).toMatch(/^g-systems-\d{4}-\d{2}-\d{2}\.svg$/);
    expect(exportFilename("tapestry-dev", "chronicle", "png")).toMatch(
      /^tapestry-dev-chronicle-\d{4}-\d{2}-\d{2}\.png$/,
    );
  });
});

describe("allVisible", () => {
  it("returns every node and edge in the graph", () => {
    const g = new Graph({ multi: true, type: "directed" });
    g.addNode("a");
    g.addNode("b");
    g.addEdgeWithKey("r1", "a", "b");
    const visible = allVisible(g);
    expect(visible.visibleNodes).toEqual(new Set(["a", "b"]));
    expect(visible.visibleEdges).toEqual(new Set(["r1"]));
  });
});
