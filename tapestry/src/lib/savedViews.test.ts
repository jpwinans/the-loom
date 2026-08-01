import { beforeEach, describe, expect, it } from "vitest";
import {
  deleteView,
  importViews,
  listViews,
  renameView,
  resolveViewHash,
  saveView,
  serializeViews,
} from "./savedViews";

describe("saved views", () => {
  beforeEach(() => localStorage.clear());
  it("saves, lists, and deletes named views", () => {
    saveView("g", "my-view", "#s=abc");
    expect(listViews("g")).toHaveLength(1);
    expect(listViews("g")[0]).toMatchObject({ name: "my-view", hash: "#s=abc" });
    deleteView("g", "my-view");
    expect(listViews("g")).toHaveLength(0);
  });
});

describe("saved-view management", () => {
  beforeEach(() => localStorage.clear());

  it("renames a view, preserving its hash, and refuses a name collision", () => {
    saveView("g", "a", "#s=1");
    saveView("g", "b", "#s=2");
    expect(renameView("g", "a", "c")).toBe(true);
    expect(resolveViewHash("g", "c")).toBe("#s=1");
    expect(resolveViewHash("g", "a")).toBeNull();
    expect(renameView("g", "c", "b")).toBe(false); // b exists
    expect(resolveViewHash("g", "c")).toBe("#s=1"); // unchanged
  });

  it("round-trips export → import across graphs", () => {
    saveView("g", "a", "#s=1");
    const json = serializeViews("g");
    expect(importViews("h", json).added).toBe(1);
    expect(resolveViewHash("h", "a")).toBe("#s=1");
  });

  it("reports an error for a malformed import without throwing", () => {
    expect(importViews("g", "not json").added).toBe(0);
    expect(importViews("g", "not json").error).toBeTruthy();
    expect(listViews("g")).toEqual([]);
  });
});
