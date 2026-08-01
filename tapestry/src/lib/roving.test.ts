import { describe, expect, it } from "vitest";
import { nextRovingIndex } from "./roving";

describe("nextRovingIndex", () => {
  it("wraps forward and backward", () => {
    expect(nextRovingIndex(4, 5, "ArrowRight")).toBe(0);
    expect(nextRovingIndex(0, 5, "ArrowLeft")).toBe(4);
  });
  it("honors Home/End and ignores other keys", () => {
    expect(nextRovingIndex(3, 5, "Home")).toBe(0);
    expect(nextRovingIndex(3, 5, "End")).toBe(4);
    expect(nextRovingIndex(3, 5, "a")).toBeNull();
  });
});
