/**
 * dragNodes — click-hold-drag node repositioning for any Sigma canvas view.
 *
 * Obsidian-style: press a node, move it, and every incident edge stays attached
 * (Sigma re-renders edges from node positions on each `nodeAttributesUpdated`),
 * and the node stays where it is dropped. One controller wires the whole
 * choreography onto a live Sigma instance; the pure threshold/resume decisions
 * live in `dragState.ts` so they can be unit-tested without a browser.
 *
 * The choreography, per Sigma's event model (verified against sigma@3.0.3):
 *
 *  - `downNode` records the node, resets the click-vs-drag latch, freezes the
 *    normalization bbox (so a mid-drag `refresh` — a physics tick, a flow
 *    animation, a scrub — can't rescale the whole graph when the node leaves the
 *    old extent), pauses the force layout (remembering whether it was running),
 *    and sets the `grabbing` cursor.
 *  - `moveBody` (the unified mouse+touch body-move event) writes the node's new
 *    graph coordinates from the pointer, latching the gesture to a drag past a
 *    ~3px threshold, then prevents Sigma's default camera pan and stops the
 *    native event so nothing else reacts. No manual `refresh` — graphology's
 *    `nodeAttributesUpdated` drives Sigma's lightweight per-node update.
 *  - `upNode` / `upStage` end the drag: clear the custom bbox (a conservative
 *    return to auto-fit), resume the layout only if it was running at drag
 *    start, and restore the cursor.
 *
 * The click-vs-drag trap: because `preventSigmaDefault()` on `moveBody`
 * short-circuits the mouse captor's own dragged-events tolerance counting, Sigma
 * still emits `clickNode`/`clickStage` after a real drag. A view's click
 * handlers must therefore call `consumeDragMoved()` first and bail when it
 * returns true, so a drop never mutates selection or path state.
 */
import type Graph from "graphology";
import type Sigma from "sigma";
import type { SigmaNodeEventPayload, SigmaStageEventPayload } from "sigma/types";
import {
  IDLE_GESTURE,
  moveGesture,
  pressGesture,
  shouldResumeLayout,
  type DragGesture,
} from "./dragState";

/** The minimal force-layout surface the drag controller drives (pause/resume). */
export interface DragLayout {
  readonly running: boolean;
  start(): void;
  stop(): void;
}

export interface NodeDragOptions {
  sigma: Sigma;
  graph: Graph;
  /** The Sigma container element — its cursor reflects the hover/drag affordance. */
  container: HTMLElement;
  /**
   * The view's force layout, read fresh at drag start (so a layout created after
   * the controller, or absent entirely, is handled). Omit for a layout-free view.
   */
  getLayout?: () => DragLayout | null;
  /** Gate: return false to suppress dragging (defaults to always enabled). */
  isEnabled?: () => boolean;
}

export interface NodeDragController {
  /** Detach every listener and restore the container cursor. */
  destroy(): void;
  /**
   * Read-and-clear the "a real drag just ended" latch. A view's `clickNode` /
   * `clickStage` handler calls this first and bails when it returns true, so the
   * click Sigma emits after a drag never mutates selection or path state.
   */
  consumeDragMoved(): boolean;
}

export function createNodeDrag(options: NodeDragOptions): NodeDragController {
  const { sigma, graph, container, getLayout, isEnabled } = options;

  let gesture: DragGesture = IDLE_GESTURE;
  let draggedNode: string | null = null;
  let wasRunning = false;
  // The click-vs-drag latch: set when a real drag ends, read+cleared by
  // consumeDragMoved so the trailing click is swallowed exactly once. Reset on
  // every fresh press so a stale drag (released off-container, no trailing click)
  // can never suppress a later genuine click.
  let dragJustMoved = false;

  const enabled = (): boolean => (isEnabled ? isEnabled() : true);

  const onDownNode = (payload: SigmaNodeEventPayload): void => {
    if (!enabled()) return;
    draggedNode = payload.node;
    dragJustMoved = false;
    gesture = pressGesture(payload.event.x, payload.event.y);
    // Freeze the normalization extent for the drag's duration.
    if (!sigma.getCustomBBox()) sigma.setCustomBBox(sigma.getBBox());
    const layout = getLayout?.() ?? null;
    wasRunning = layout?.running ?? false;
    layout?.stop();
    container.style.cursor = "grabbing";
  };

  const onMoveBody = (payload: SigmaStageEventPayload): void => {
    if (draggedNode === null) return;
    const { event } = payload;
    gesture = moveGesture(gesture, event.x, event.y);
    if (gesture.moved) dragJustMoved = true;
    const pos = sigma.viewportToGraph(event);
    graph.setNodeAttribute(draggedNode, "x", pos.x);
    graph.setNodeAttribute(draggedNode, "y", pos.y);
    // Keep the body-move from also panning the camera or bubbling to the DOM.
    event.preventSigmaDefault();
    event.original.preventDefault();
    event.original.stopPropagation();
  };

  const endDrag = (): void => {
    if (draggedNode === null) return;
    draggedNode = null;
    gesture = IDLE_GESTURE;
    sigma.setCustomBBox(null);
    const layout = getLayout?.() ?? null;
    if (shouldResumeLayout(wasRunning, layout !== null)) layout!.start();
    wasRunning = false;
    // The pointer is still over the node just dropped — keep the grab affordance.
    container.style.cursor = "grab";
  };

  const onEnterNode = (): void => {
    if (draggedNode !== null || !enabled()) return;
    container.style.cursor = "grab";
  };

  const onLeaveNode = (): void => {
    if (draggedNode !== null) return;
    container.style.cursor = "";
  };

  sigma.on("downNode", onDownNode);
  sigma.on("moveBody", onMoveBody);
  sigma.on("upNode", endDrag);
  sigma.on("upStage", endDrag);
  sigma.on("enterNode", onEnterNode);
  sigma.on("leaveNode", onLeaveNode);

  return {
    destroy(): void {
      sigma.off("downNode", onDownNode);
      sigma.off("moveBody", onMoveBody);
      sigma.off("upNode", endDrag);
      sigma.off("upStage", endDrag);
      sigma.off("enterNode", onEnterNode);
      sigma.off("leaveNode", onLeaveNode);
      container.style.cursor = "";
    },
    consumeDragMoved(): boolean {
      const moved = dragJustMoved;
      dragJustMoved = false;
      return moved;
    },
  };
}
