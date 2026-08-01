/**
 * dragState — the pure, DOM-free decision logic behind node dragging.
 *
 * Two concerns live here, both exercisable in vitest without a Sigma instance or
 * a browser:
 *
 *  1. **The click-vs-drag threshold.** A press that never travels beyond a few
 *     viewport pixels is a click; once it crosses the threshold it is a drag and
 *     it *stays* a drag for the rest of the gesture — the flag is sticky, so a
 *     hand that jitters back toward the origin still suppresses the trailing
 *     click Sigma emits after a real drag.
 *  2. **The resume-layout policy.** After a drag ends, the force layout resumes
 *     only if it was running when the drag began — and only if there is a layout
 *     at all (the Semantic map has none). Pausing physics for the drag's
 *     duration then restoring exactly the prior state.
 *
 * `dragNodes.ts` wires these into Sigma's event choreography; keeping them pure
 * here means the interaction's trickiest edges are unit-tested directly.
 */

/** Movement (viewport px) a press must exceed before it counts as a drag. */
export const DRAG_THRESHOLD_PX = 3;

export interface DragGesture {
  /** True between press and release — a node is being held. */
  readonly holding: boolean;
  /** True once the press has crossed the threshold; sticky until release. */
  readonly moved: boolean;
  readonly originX: number;
  readonly originY: number;
}

/** The rest state: nothing held, nothing moved. */
export const IDLE_GESTURE: DragGesture = { holding: false, moved: false, originX: 0, originY: 0 };

/** Press on a node at a viewport point: begin holding, not yet moved. */
export function pressGesture(x: number, y: number): DragGesture {
  return { holding: true, moved: false, originX: x, originY: y };
}

/**
 * Advance the gesture as the pointer reaches (x, y). Once `moved` latches true
 * it stays true for the rest of the hold; a press that has not yet travelled
 * past `threshold` stays a candidate click. A no-op when nothing is held.
 */
export function moveGesture(
  gesture: DragGesture,
  x: number,
  y: number,
  threshold: number = DRAG_THRESHOLD_PX,
): DragGesture {
  if (!gesture.holding || gesture.moved) return gesture;
  const dx = x - gesture.originX;
  const dy = y - gesture.originY;
  if (Math.hypot(dx, dy) > threshold) return { ...gesture, moved: true };
  return gesture;
}

/** Release the hold, returning to idle. The caller latches `moved` separately. */
export function releaseGesture(): DragGesture {
  return IDLE_GESTURE;
}

/**
 * Whether the force layout should resume when a drag ends: only when it was
 * running as the drag began AND a layout controller actually exists — a view
 * with no layout (the Semantic map) never resumes anything, and a layout the
 * reader had already paused stays paused.
 */
export function shouldResumeLayout(wasRunning: boolean, hasLayout: boolean): boolean {
  return hasLayout && wasRunning;
}
