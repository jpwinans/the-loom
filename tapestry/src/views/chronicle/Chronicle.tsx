/**
 * Chronicle — the bi-temporal time-travel view.
 *
 * A Sigma diagram over the *same* graphology model the Explorer renders (the
 * shared `useGraph()` instance), driven per-instant by the pure replay engine
 * (`buildTimeline` / `stateAt`). The scrubber sets a time `t`; `stateAt` answers
 * which nodes and edges existed then and each node's effective status, and the
 * reducers turn that into visibility and styling — nothing in the graph is
 * mutated. Three things distinguish it from the Explorer:
 *
 *  1. A node or edge not yet born at `t` is `hidden`, so dragging the scrubber
 *     from start to end replays the weave assembling itself.
 *  2. A node whose effective status is non-`active` at `t` reads as invalidated —
 *     dimmed on the canvas with a labelled status badge riding it (a state
 *     channel on the reserved --color-serious token, never colour alone), so
 *     replaying past a deprecation visibly restyles the node at that instant.
 *  3. A node created within a trailing slice of the timeline gets a brief
 *     "just appeared" highlight, so births catch the eye during playback.
 *
 * Play advances `t` on a single rAF loop (reduced-motion steps discretely, event
 * to event); canvas colours re-resolve from the token layer on a theme change,
 * mirroring the Explorer's rAF discipline.
 */
import { useCallback, useEffect, useMemo, useRef } from "react";
import Sigma from "sigma";
import { useBundle, useGraph } from "../../lib/BundleContext";
import { createNodeDrag } from "../../lib/dragNodes";
import { useTapestry } from "../../state/store";
import { exportFilename, exportPngFile, exportSvgFile } from "../../lib/exportSvg";
import { resolveGraphColors } from "../explorer/buildGraph";
import { createLayout, type LayoutController } from "../explorer/layout";
import { buildTimeline, diffStates, stateAt, type ChronicleState, type Diff, type Timeline } from "./replay";
import { Scrubber } from "./Scrubber";
import { EventList } from "./EventList";
import { formatInstant } from "./format";
import "./Chronicle.css";

/** How long FA2 runs on mount before the layout freezes. */
const LAYOUT_MS = 2000;
/**
 * Label level-of-detail: above this node count, raise sigma's
 * `labelRenderedSizeThreshold` so only the largest nodes keep a label at scale.
 * Gated on order so the fixture is untouched (sigma's default is 6).
 */
const LABEL_LOD_MIN_ORDER = 2000;
const LABEL_LOD_SIZE = 14;
/** One full play-through of the remaining span, at full span. */
const PLAY_MS = 6000;
/** Reduced-motion playback dwell per event instant. */
const REDUCED_STEP_MS = 650;
/** A birth glows for this fraction of the whole timeline after it happens. */
const JUST_APPEARED_FRACTION = 0.03;
/** Viewport nudge seating a status badge above its node. */
const STATUS_OFFSET = 15;
const LABEL_FONT = "system-ui, sans-serif";
/** Fallbacks mirror tokens.css (light) for headless/pre-resolve contexts. */
const TEXT_FALLBACK = "#16181f";
const DIM_FALLBACK = "#767b88";
/** Diff channel — the reserved status tokens (added / changed / invalidated). */
const GOOD_FALLBACK = "#0ca30c";
const WARN_FALLBACK = "#fab219";
const CRIT_FALLBACK = "#d03b3b";
const CANVAS_FALLBACK = "#f7f6f3";
const SERIOUS_FALLBACK = "#ec835a";

function readVar(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

/** True when the viewer has asked the OS to minimise motion. */
function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

function humanize(value: string): string {
  return value.replace(/_/g, " ");
}

/** Distinct event instants in ascending order — the reduced-motion play stops. */
function eventInstants(timeline: Timeline): number[] {
  const seen = new Set<number>();
  const out: number[] = [];
  for (const event of timeline.events) {
    if (!seen.has(event.t)) {
      seen.add(event.t);
      out.push(event.t);
    }
  }
  return out;
}

export function Chronicle() {
  const bundle = useBundle();
  const graphKey = bundle.meta.graph;
  const graph = useGraph();
  const theme = useTapestry((s) => s.theme);
  const time = useTapestry((s) => s.time);
  const playing = useTapestry((s) => s.playing);
  const diffAnchor = useTapestry((s) => s.diffAnchor);
  const setTime = useTapestry((s) => s.setTime);
  const setPlaying = useTapestry((s) => s.setPlaying);

  const timeline = useMemo(() => buildTimeline(bundle), [bundle]);
  const effectiveTime = time ?? timeline.end;
  const hasEvents = timeline.events.length > 0;

  // Diff mode: two instants, A (the anchor) and B (the scrubber). The window is
  // order-independent — `diffStates` takes min/max internally.
  const diffActive = diffAnchor !== null;
  const diff = useMemo<Diff | null>(
    () => (diffAnchor === null ? null : diffStates(timeline, diffAnchor, effectiveTime)),
    [diffAnchor, effectiveTime, timeline],
  );

  const containerRef = useRef<HTMLDivElement | null>(null);
  const markerLayerRef = useRef<HTMLDivElement | null>(null);
  const diffMarkerLayerRef = useRef<HTMLDivElement | null>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const layoutRef = useRef<LayoutController | null>(null);

  // Live reducer inputs — held in refs so the render loop reads current values
  // without re-instantiating Sigma.
  const stateRef = useRef<ChronicleState | null>(null);
  const diffRef = useRef<Diff | null>(null);
  const timelineRef = useRef<Timeline>(timeline);
  const effectiveTimeRef = useRef<number>(effectiveTime);
  const dimRef = useRef<string>(DIM_FALLBACK);
  const goodRef = useRef<string>(GOOD_FALLBACK);
  const warnRef = useRef<string>(WARN_FALLBACK);
  const critRef = useRef<string>(CRIT_FALLBACK);
  // Diff icon badges (one per changed node), rebuilt when the diff window moves.
  const diffMarkersRef = useRef<Map<string, HTMLSpanElement>>(new Map());

  // Instantiate Sigma, wire the replay reducers + status-badge overlay, and run
  // the force layout once. Rebuilds only when the shared graph changes.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    resolveGraphColors(graph);
    dimRef.current = readVar("--color-text-3", DIM_FALLBACK);
    goodRef.current = readVar("--color-good", GOOD_FALLBACK);
    warnRef.current = readVar("--color-warning", WARN_FALLBACK);
    critRef.current = readVar("--color-critical", CRIT_FALLBACK);
    timelineRef.current = timeline;
    effectiveTimeRef.current = effectiveTime;
    stateRef.current = stateAt(timeline, graph, effectiveTime);

    const sigma = new Sigma(graph, container, {
      renderEdgeLabels: false,
      defaultEdgeType: "arrow",
      labelFont: LABEL_FONT,
      labelColor: { color: readVar("--color-text", TEXT_FALLBACK) },
      labelDensity: 0.6,
      ...(graph.order > LABEL_LOD_MIN_ORDER ? { labelRenderedSizeThreshold: LABEL_LOD_SIZE } : {}),
      nodeReducer: (node, data) => {
        const state = stateRef.current;
        if (!state) return data;
        if (!state.visibleNodes.has(node)) return { ...data, hidden: true };

        // Diff layer: colour by change category (added / invalidated / changed),
        // everything else receding so the delta reads. The per-node icon badge
        // carries the category so it is never colour-alone.
        const delta = diffRef.current;
        if (delta) {
          if (delta.added.has(node))
            return { ...data, color: goodRef.current, highlighted: true, zIndex: 2 };
          if (delta.invalidated.has(node))
            return { ...data, color: critRef.current, highlighted: true, zIndex: 2 };
          if (delta.changed.has(node))
            return { ...data, color: warnRef.current, highlighted: true, zIndex: 2 };
          return { ...data, color: dimRef.current, zIndex: 0 };
        }

        const status = state.statusById.get(node);
        if (status != null && status !== "active") {
          // Invalidated: recede on the canvas; the badge overlay carries the state.
          return { ...data, color: dimRef.current, zIndex: 0 };
        }

        const line = timelineRef.current;
        const created = line.nodeCreated.get(node);
        if (created != null) {
          const window = (line.end - line.start) * JUST_APPEARED_FRACTION;
          const age = effectiveTimeRef.current - created;
          if (age >= 0 && age <= window) return { ...data, highlighted: true, zIndex: 1 };
        }
        return data;
      },
      edgeReducer: (edge, data) => {
        const state = stateRef.current;
        if (!state) return data;
        if (!state.visibleEdges.has(edge)) return { ...data, hidden: true };
        // In diff mode, edges recede so the node categories carry the eye.
        if (diffRef.current) return { ...data, color: dimRef.current, zIndex: 0 };
        return data;
      },
    });
    sigmaRef.current = sigma;

    // Click-hold-drag node repositioning. The status/diff badges follow the
    // dragged node for free (they reposition on afterRender), and the bbox
    // freeze keeps play/scrub refreshes from rescaling the map mid-drag. The
    // layout here is a one-shot settling burst with no resume control, so a
    // manual drag pauses the burst but never resumes it — reporting
    // `running: false` keeps the controller from restarting an FA2 that nothing
    // would ever stop.
    const drag = createNodeDrag({
      sigma,
      graph,
      container,
      getLayout: () => {
        const layout = layoutRef.current;
        return layout ? { running: false, start: () => layout.start(), stop: () => layout.stop() } : null;
      },
    });

    // Status badges: one per node that ever changes status, shown only while its
    // effective status is non-active, repositioned each render to ride the node.
    const markerLayer = markerLayerRef.current;
    const statusEls = new Map<string, { badge: HTMLSpanElement; text: HTMLSpanElement; name: string }>();
    if (markerLayer) {
      markerLayer.replaceChildren();
      for (const nodeId of timeline.nodeStatus.keys()) {
        if (!graph.hasNode(nodeId)) continue;
        const badge = document.createElement("span");
        badge.className = "chronicle__badge";
        badge.setAttribute("role", "img");
        const dot = document.createElement("span");
        dot.className = "chronicle__badgedot";
        const text = document.createElement("span");
        text.className = "chronicle__badgetext";
        badge.append(dot, text);
        badge.style.display = "none";
        markerLayer.appendChild(badge);
        const name = (graph.getNodeAttribute(nodeId, "label") as string) ?? nodeId;
        statusEls.set(nodeId, { badge, text, name });
      }
    }

    const updateOverlays = () => {
      const sig = sigmaRef.current;
      if (!sig) return;
      const state = stateRef.current;
      const inDiff = diffRef.current != null;

      // Status badges (Task 7 replay) — suppressed while diff mode is on.
      for (const [nodeId, entry] of statusEls) {
        const status = state?.statusById.get(nodeId);
        const visible = state ? state.visibleNodes.has(nodeId) : true;
        if (inDiff || !visible || status == null || status === "active") {
          entry.badge.style.display = "none";
          continue;
        }
        const pos = sig.getNodeDisplayData(nodeId);
        if (!pos) {
          entry.badge.style.display = "none";
          continue;
        }
        if (entry.badge.dataset.status !== status) {
          entry.text.textContent = humanize(status);
          entry.badge.setAttribute("aria-label", `${entry.name}: ${humanize(status)}`);
          entry.badge.dataset.status = status;
        }
        const p = sig.framedGraphToViewport(pos);
        entry.badge.style.display = "";
        entry.badge.style.transform = `translate(-50%, -50%) translate(${p.x}px, ${p.y - STATUS_OFFSET}px)`;
      }

      // Diff icon badges — one per changed node while diff mode is on.
      for (const [nodeId, el] of diffMarkersRef.current) {
        const visible = state ? state.visibleNodes.has(nodeId) : true;
        if (!inDiff || !visible) {
          el.style.display = "none";
          continue;
        }
        const pos = sig.getNodeDisplayData(nodeId);
        if (!pos) {
          el.style.display = "none";
          continue;
        }
        const p = sig.framedGraphToViewport(pos);
        el.style.display = "";
        el.style.transform = `translate(-50%, -50%) translate(${p.x}px, ${p.y - STATUS_OFFSET}px)`;
      }
    };
    sigma.on("afterRender", updateOverlays);

    const layout = createLayout(graph);
    layoutRef.current = layout;
    layout.start();
    const timer = window.setTimeout(() => {
      layout.stop();
      void sigma.getCamera().animatedReset();
    }, LAYOUT_MS);

    return () => {
      window.clearTimeout(timer);
      drag.destroy();
      layout.kill();
      sigma.kill();
      sigmaRef.current = null;
      layoutRef.current = null;
      statusEls.clear();
      markerLayerRef.current?.replaceChildren();
    };
  }, [graph, timeline]);

  // Time layer: recompute the replay projection whenever the instant (or the
  // timeline) changes, then repaint. The reducers read the refreshed refs. In
  // diff mode, visibility is projected at the later of the two instants so every
  // node the window touches is on screen.
  useEffect(() => {
    const vizTime = diffAnchor === null ? effectiveTime : Math.max(diffAnchor, effectiveTime);
    timelineRef.current = timeline;
    effectiveTimeRef.current = effectiveTime;
    stateRef.current = stateAt(timeline, graph, vizTime);
    sigmaRef.current?.refresh();
  }, [timeline, graph, effectiveTime, diffAnchor]);

  // Diff layer: publish the current diff to the reducers and rebuild the per-node
  // icon badges for the changed set. Rebuilds only when the diff window moves.
  useEffect(() => {
    diffRef.current = diff;
    const layer = diffMarkerLayerRef.current;
    const markers = diffMarkersRef.current;
    markers.clear();
    layer?.replaceChildren();

    if (diff && layer) {
      const add = (nodeId: string, category: string, icon: string, word: string) => {
        if (!graph.hasNode(nodeId) || markers.has(nodeId)) return;
        const el = document.createElement("span");
        el.className = `chronicle__diffbadge chronicle__diffbadge--${category}`;
        el.textContent = icon;
        el.style.display = "none";
        el.setAttribute("role", "img");
        const name = (graph.getNodeAttribute(nodeId, "label") as string) ?? nodeId;
        el.setAttribute("aria-label", `${name}: ${word}`);
        layer.appendChild(el);
        markers.set(nodeId, el);
      };
      // Priority added ▸ invalidated ▸ changed, so a node wears one badge.
      for (const id of diff.added) add(id, "added", "+", "added");
      for (const id of diff.invalidated) add(id, "invalidated", "⊘", "invalidated");
      for (const id of diff.changed) add(id, "changed", "~", "changed");
    }
    sigmaRef.current?.refresh();

    return () => {
      diffRef.current = null;
      diffMarkersRef.current.clear();
      diffMarkerLayerRef.current?.replaceChildren();
    };
  }, [diff, graph]);

  // Play layer: while playing, advance `time` toward `end`. Smooth on a single
  // rAF; under reduced-motion, step discretely from one event instant to the
  // next. Reaching the end, or any pause, stops the loop.
  useEffect(() => {
    if (!playing || !hasEvents) return;
    const { start, end } = timeline;
    let from = useTapestry.getState().time ?? end;
    if (from >= end) from = start; // replay from the beginning when parked at the end
    const span = end - from;
    if (span <= 0) {
      setPlaying(false);
      return;
    }

    if (prefersReducedMotion()) {
      const stops = eventInstants(timeline).filter((t) => t > from);
      let i = 0;
      setTime(from);
      const id = window.setInterval(() => {
        if (i >= stops.length) {
          window.clearInterval(id);
          setPlaying(false);
          return;
        }
        setTime(stops[i]);
        i += 1;
      }, REDUCED_STEP_MS);
      return () => window.clearInterval(id);
    }

    const duration = PLAY_MS * (span / Math.max(1, end - start));
    let raf = 0;
    let t0 = 0;
    const tick = (now: number) => {
      if (t0 === 0) t0 = now;
      const progress = Math.min(1, (now - t0) / duration);
      setTime(from + span * progress);
      if (progress >= 1) {
        setPlaying(false);
        return;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, hasEvents, timeline, setTime, setPlaying]);

  // Re-resolve token colours when the resolved theme changes. rAF defers past
  // App's applyTheme so we read the freshly-stamped `data-theme`.
  useEffect(() => {
    let raf = 0;
    const reresolve = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        resolveGraphColors(graph);
        dimRef.current = readVar("--color-text-3", DIM_FALLBACK);
        goodRef.current = readVar("--color-good", GOOD_FALLBACK);
        warnRef.current = readVar("--color-warning", WARN_FALLBACK);
        critRef.current = readVar("--color-critical", CRIT_FALLBACK);
        const sigma = sigmaRef.current;
        if (sigma) {
          sigma.setSetting("labelColor", { color: readVar("--color-text", TEXT_FALLBACK) });
          sigma.refresh();
        }
      });
    };
    reresolve();

    let mq: MediaQueryList | null = null;
    if (theme === "auto" && typeof window !== "undefined" && window.matchMedia) {
      mq = window.matchMedia("(prefers-color-scheme: dark)");
      mq.addEventListener("change", reresolve);
    }
    return () => {
      cancelAnimationFrame(raf);
      if (mq) mq.removeEventListener("change", reresolve);
    };
  }, [theme, graph]);

  const diffLo = diffAnchor === null ? 0 : Math.min(diffAnchor, effectiveTime);
  const diffHi = diffAnchor === null ? 0 : Math.max(diffAnchor, effectiveTime);
  // Summary counts match the on-canvas badges: one category per node, priority
  // added ▸ invalidated ▸ changed (a node both updated and deprecated in the
  // window reads as invalidated, not double-counted).
  const diffCounts = {
    added: diff?.added.size ?? 0,
    invalidated: diff ? [...diff.invalidated].filter((id) => !diff.added.has(id)).length : 0,
    changed: diff
      ? [...diff.changed].filter((id) => !diff.added.has(id) && !diff.invalidated.has(id)).length
      : 0,
  };

  // Exports — a WYSIWYG snapshot of the replay at the *current* instant: the
  // visibility set is the same `state.visibleNodes`/`visibleEdges` the
  // reducers already project (recomputed here, not read off the ref, so the
  // export always reflects the instant the button was clicked), so a node not
  // yet born at this point on the scrubber is correctly absent from the
  // export too. Status badges are a DOM overlay the SVG cannot carry, so its
  // legend documents the live/invalidated swatches instead, and both
  // controls' titles say so plainly.
  const exportPng = useCallback(() => {
    const sigma = sigmaRef.current;
    if (!sigma) return;
    void exportPngFile(
      sigma,
      exportFilename(graphKey, "chronicle", "png"),
      readVar("--color-canvas", CANVAS_FALLBACK),
    );
  }, [graphKey]);

  const exportSvg = useCallback(() => {
    const sigma = sigmaRef.current;
    if (!sigma) return;
    const state = stateAt(timeline, graph, effectiveTime);
    exportSvgFile(
      sigma,
      graph,
      { visibleNodes: state.visibleNodes, visibleEdges: state.visibleEdges },
      {
        textColor: readVar("--color-text", TEXT_FALLBACK),
        background: readVar("--color-canvas", CANVAS_FALLBACK),
        legend: [
          { label: "live at this instant", color: readVar("--color-accent", "#4a44c4") },
          { label: "invalidated (status changed)", color: readVar("--color-serious", SERIOUS_FALLBACK) },
        ],
      },
      exportFilename(graphKey, "chronicle", "svg"),
    );
  }, [graph, timeline, effectiveTime, graphKey]);

  return (
    <section
      id="panel-chronicle"
      className="chronicle"
      role="tabpanel"
      aria-labelledby="tab-chronicle"
      tabIndex={0}
    >
      <div className="chronicle__canvas" ref={containerRef} />
      <div className="chronicle__markers" ref={markerLayerRef} />
      <div className="chronicle__diffmarkers" ref={diffMarkerLayerRef} />

      {!hasEvents && (
        <div className="chronicle__empty">
          <p className="chronicle__emptytitle">No history to replay in this scope.</p>
          <p className="chronicle__emptybody">
            The Chronicle needs a temporal event log — re-export with the temporal section to
            watch the graph assemble instant by instant.
          </p>
        </div>
      )}

      {hasEvents && diffActive && (
        <div className="chronicle__diffbar" role="group" aria-label="Diff summary">
          <span className="chronicle__diffwindow">
            <span className="chronicle__diffinstant">{formatInstant(diffLo)}</span>
            <span className="chronicle__diffarrow" aria-hidden="true">
              →
            </span>
            <span className="chronicle__diffinstant">{formatInstant(diffHi)}</span>
          </span>
          <span className="chronicle__diffchips">
            <span className="chronicle__diffchip chronicle__diffchip--added">
              <span className="chronicle__diffchipicon" aria-hidden="true">
                +
              </span>
              {diffCounts.added} added
            </span>
            <span className="chronicle__diffchip chronicle__diffchip--changed">
              <span className="chronicle__diffchipicon" aria-hidden="true">
                ~
              </span>
              {diffCounts.changed} changed
            </span>
            <span className="chronicle__diffchip chronicle__diffchip--invalidated">
              <span className="chronicle__diffchipicon" aria-hidden="true">
                ⊘
              </span>
              {diffCounts.invalidated} invalidated
            </span>
          </span>
        </div>
      )}

      {hasEvents && !diffActive && (
        <div
          className="chronicle__legend"
          role="img"
          aria-label="Legend: live nodes and invalidated nodes"
        >
          <span className="chronicle__legenditem">
            <span className="chronicle__legendswatch chronicle__legendswatch--live" aria-hidden="true" />
            live at this instant
          </span>
          <span className="chronicle__legenditem">
            <span
              className="chronicle__legendswatch chronicle__legendswatch--flagged"
              aria-hidden="true"
            />
            invalidated (status changed)
          </span>
        </div>
      )}

      {hasEvents && (
        <>
          <EventList timeline={timeline} onExportPng={exportPng} onExportSvg={exportSvg} />
          <Scrubber timeline={timeline} />
        </>
      )}
    </section>
  );
}
