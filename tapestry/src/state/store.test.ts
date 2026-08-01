import { describe, expect, it } from "vitest";
import { useTapestry } from "./store";

describe("tapestry store", () => {
  it("defaults to explorer view with empty filters", () => {
    const s = useTapestry.getState();
    expect(s.view).toBe("explorer");
    expect(s.filters.entityTypes).toEqual([]);
    expect(s.filters.confidenceMin).toBe(0);
  });
  it("selects and filters", () => {
    useTapestry.getState().select("e1");
    useTapestry.getState().setFilters({ entityTypes: ["concept"] });
    expect(useTapestry.getState().selection).toBe("e1");
    expect(useTapestry.getState().filters.entityTypes).toEqual(["concept"]);
  });
  it("isolates a loop and clears it", () => {
    expect(useTapestry.getState().selectedLoop).toBe(null);
    useTapestry.getState().selectLoop(2);
    expect(useTapestry.getState().selectedLoop).toBe(2);
    useTapestry.getState().selectLoop(null);
    expect(useTapestry.getState().selectedLoop).toBe(null);
  });
  it("tracks the chronicle time position and play state", () => {
    expect(useTapestry.getState().time).toBe(null);
    expect(useTapestry.getState().playing).toBe(false);
    useTapestry.getState().setTime(1720000000000);
    useTapestry.getState().setPlaying(true);
    expect(useTapestry.getState().time).toBe(1720000000000);
    expect(useTapestry.getState().playing).toBe(true);
    useTapestry.getState().setTime(null);
    useTapestry.getState().setPlaying(false);
    expect(useTapestry.getState().time).toBe(null);
    expect(useTapestry.getState().playing).toBe(false);
  });
  it("sets and clears the diff anchor", () => {
    expect(useTapestry.getState().diffAnchor).toBe(null);
    useTapestry.getState().setDiffAnchor(1720000000000);
    expect(useTapestry.getState().diffAnchor).toBe(1720000000000);
    useTapestry.getState().setDiffAnchor(null);
    expect(useTapestry.getState().diffAnchor).toBe(null);
  });
  it("sets and clears the semantic-map brush", () => {
    expect(useTapestry.getState().brushedIds).toBe(null);
    useTapestry.getState().setBrushed(["x", "y"]);
    expect(useTapestry.getState().brushedIds).toEqual(["x", "y"]);
    useTapestry.getState().setBrushed(null);
    expect(useTapestry.getState().brushedIds).toBe(null);
  });
});
