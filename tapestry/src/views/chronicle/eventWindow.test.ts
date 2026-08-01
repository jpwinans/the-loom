import { describe, expect, it } from "vitest";
import { visibleRange } from "./eventWindow";

describe("visibleRange", () => {
  it("windows a large list with overscan and clamps to bounds", () => {
    expect(visibleRange(0, 20, 200, 100000, 5)).toEqual({ start: 0, end: 15 });
    const mid = visibleRange(2000, 20, 200, 100000, 5);
    expect(mid.start).toBe(95); // floor(2000/20) - 5
    expect(mid.end).toBe(115); // +10 rows +5 overscan
  });

  it("clamps the tail so the window never runs past the last row", () => {
    const tail = visibleRange(100000 * 20, 20, 200, 100000, 5);
    expect(tail.end).toBe(100000);
    expect(tail.start).toBeLessThanOrEqual(100000);
  });

  it("keeps a short list within a single window", () => {
    // 35-row fixture in a tall rail: start at 0, end covers every row.
    expect(visibleRange(0, 46, 4000, 35, 8)).toEqual({ start: 0, end: 35 });
  });
});
