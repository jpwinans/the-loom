import { describe, expect, it } from "vitest";
import {
  DRAG_THRESHOLD_PX,
  IDLE_GESTURE,
  moveGesture,
  pressGesture,
  releaseGesture,
  shouldResumeLayout,
} from "./dragState";

describe("drag threshold gesture", () => {
  it("a press starts holding but unmoved, anchored at the down point", () => {
    const g = pressGesture(120, 80);
    expect(g).toEqual({ holding: true, moved: false, originX: 120, originY: 80 });
  });

  it("stays a candidate click while movement is within the threshold", () => {
    const g = pressGesture(100, 100);
    // A 2px diagonal nudge (~2.83px) is under the 3px threshold.
    const nudged = moveGesture(g, 102, 102);
    expect(nudged.moved).toBe(false);
  });

  it("latches to a drag once movement exceeds the threshold", () => {
    const g = pressGesture(100, 100);
    const dragged = moveGesture(g, 100 + DRAG_THRESHOLD_PX + 1, 100);
    expect(dragged.moved).toBe(true);
    expect(dragged.holding).toBe(true);
  });

  it("keeps `moved` sticky even when the pointer returns toward the origin", () => {
    const g = pressGesture(100, 100);
    const dragged = moveGesture(g, 140, 140); // well past threshold
    expect(dragged.moved).toBe(true);
    // Coming back to the exact origin must NOT demote the gesture to a click.
    const back = moveGesture(dragged, 100, 100);
    expect(back.moved).toBe(true);
  });

  it("ignores movement when nothing is held", () => {
    const settled = moveGesture(IDLE_GESTURE, 999, 999);
    expect(settled).toBe(IDLE_GESTURE);
    expect(settled.moved).toBe(false);
  });

  it("honours a custom threshold", () => {
    const g = pressGesture(0, 0);
    expect(moveGesture(g, 5, 0, 10).moved).toBe(false);
    expect(moveGesture(g, 11, 0, 10).moved).toBe(true);
  });

  it("release returns to idle", () => {
    expect(releaseGesture()).toEqual(IDLE_GESTURE);
  });
});

describe("shouldResumeLayout", () => {
  it("resumes only when a layout exists and it was running at drag start", () => {
    expect(shouldResumeLayout(true, true)).toBe(true);
  });

  it("does not resume a layout the reader had already paused", () => {
    expect(shouldResumeLayout(false, true)).toBe(false);
  });

  it("never resumes when there is no layout, whatever the running flag says", () => {
    expect(shouldResumeLayout(true, false)).toBe(false);
    expect(shouldResumeLayout(false, false)).toBe(false);
  });
});
