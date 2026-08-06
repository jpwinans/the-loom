/**
 * Semantic Map — the embedding-space view.
 *
 * A Sigma scatter of `semantic.projection`, read as a map of the graph's
 * *meaning* rather than its links. It differs from the other Sigma views in
 * three deliberate ways:
 *
 *  1. **The projection is the layout.** Node positions come straight from the
 *     bundle's UMAP/PCA coordinates, so no ForceAtlas2 runs — the map mounts
 *     ready. There are no edges; distance on screen encodes semantic distance.
 *  2. **Cluster hulls** wrap each `find-clusters` group in a soft neutral
 *     region outline (an SVG overlay, repositioned on every render so it tracks
 *     the camera), each carrying its cluster label — the hull is a *region*
 *     channel, never one of the 19 entity-type hues, and never colour-alone.
 *  3. **A freehand lasso** brushes a selection: with the tool armed, an overlay
 *     captures the pointer path, and on release every projected point inside the
 *     loop lands in the shared `brushedIds` set — which the Explorer then reads
 *     as a highlight layer. Node fill still follows entity type and re-resolves
 *     from the token layer on a theme change, mirroring the Explorer's rAF
 *     discipline.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type Graph from "graphology";
import Sigma from "sigma";
import { useBundle } from "../../lib/BundleContext";
import { createNodeDrag } from "../../lib/dragNodes";
import { useTapestry } from "../../state/store";
import { allVisible, exportFilename, exportPngFile, exportSvgFile } from "../../lib/exportSvg";
import { resolveTypeColor } from "../explorer/buildGraph";
import {
  createWrappedHoverRenderer,
  createWrappedLabelRenderer,
  labelMaxWidth,
  maxLinesFor,
  revealLabel,
} from "../../lib/nodeLabels";
import {
  buildSemanticGraph,
  clusterPolygons,
  pointsInLasso,
  type Point,
} from "./semanticMap";
import "./SemanticMap.css";

const SVG_NS = "http://www.w3.org/2000/svg";
const LABEL_FONT = "system-ui, sans-serif";
/** Fallbacks mirror tokens.css (light) for headless/pre-resolve contexts. */
const TEXT_FALLBACK = "#16181f";
const DIM_FALLBACK = "#dfdedb";
const CANVAS_FALLBACK = "#f7f6f3";
const HULL_STROKE_FALLBACK = "#6c7385";
/** A lasso under this many points is ignored (a stray click, not a loop). */
const MIN_LASSO_POINTS = 3;
/**
 * Label level-of-detail: above this node count, raise sigma's
 * `labelRenderedSizeThreshold` so a 50k-point projection is not buried under a
 * label per point. Gated on order so the fixture is untouched (default is 6);
 * every projected point shares one base size, so at scale the labels thin out
 * uniformly and the reader leans on the cluster hulls instead.
 */
const LABEL_LOD_MIN_ORDER = 2000;
const LABEL_LOD_SIZE = 14;

interface SemanticCluster {
  id: number;
  label: string;
  entityIds: string[];
  size: number;
}

/** Padding (viewport px) added around a cluster hull so a region is legible. */
const HULL_PAD = 18;

function readVar(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

/**
 * Push each hull vertex outward from the hull centroid by `pad` px. A raw convex
 * hull of a tight cluster collapses to a near-invisible speck; the pad gives
 * every cluster a legible minimum footprint and spread clusters a gentle margin,
 * without touching the tested pure-geometry `convexHull`.
 */
function inflateHull(hull: Point[], pad: number): Point[] {
  if (hull.length < 2) return hull;
  const cx = hull.reduce((sum, p) => sum + p.x, 0) / hull.length;
  const cy = hull.reduce((sum, p) => sum + p.y, 0) / hull.length;
  return hull.map((p) => {
    const dx = p.x - cx;
    const dy = p.y - cy;
    const len = Math.hypot(dx, dy) || 1;
    return { x: p.x + (dx / len) * pad, y: p.y + (dy / len) * pad };
  });
}

/** Re-derive node fill (by entity type) from the current token values. */
function resolveSemanticColors(graph: Graph): void {
  graph.forEachNode((node, attr) => {
    graph.setNodeAttribute(node, "color", resolveTypeColor(attr.entityType as string));
  });
}

export function SemanticMap() {
  const bundle = useBundle();
  const graphKey = bundle.meta.graph;
  const theme = useTapestry((s) => s.theme);
  const selection = useTapestry((s) => s.selection);
  const brushedIds = useTapestry((s) => s.brushedIds);
  const setBrushed = useTapestry((s) => s.setBrushed);
  const setView = useTapestry((s) => s.setView);

  const graph = useMemo(() => buildSemanticGraph(bundle), [bundle]);
  const clusters = useMemo(
    () => (bundle.semantic?.clusters ?? []) as SemanticCluster[],
    [bundle],
  );
  const method = bundle.semantic?.method ?? null;
  // The embedding projection is never recomputed as-of a historical bundle;
  // see Overview's analyticsIsCurrentOnly for the same signal on the
  // analytics section.
  const semanticIsCurrentOnly =
    bundle.meta.asOf != null && bundle.semantic?.temporalScope === "current";

  const containerRef = useRef<HTMLDivElement | null>(null);
  const hullLayerRef = useRef<SVGSVGElement | null>(null);
  const lassoPathRef = useRef<SVGPolylineElement | null>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  // Cluster-brush menu button: focus moves into the list on open (to the first
  // cluster) and back to the trigger on close, the menu-button pattern.
  const brushToggleRef = useRef<HTMLButtonElement | null>(null);
  const firstClusterRef = useRef<HTMLButtonElement | null>(null);

  // Live reducer inputs, held in refs so the render loop reads current values
  // without re-instantiating Sigma.
  const selectionRef = useRef<string | null>(null);
  const brushSetRef = useRef<Set<string> | null>(null);
  const hoveredRef = useRef<string | null>(null);
  /** Interaction state a node's label is decided from. Reads refs, so it is
   * safe to call from sigma's reducer and label renderer. */
  const labelStateFor = useCallback(
    (node: string) => ({
      hovered: node === hoveredRef.current,
      selected: node === selectionRef.current,
      neighbor: false,
    }),
    [],
  );
  const dimRef = useRef<string>(DIM_FALLBACK);
  const showHullsRef = useRef<boolean>(true);

  // Lasso drag state — the in-progress path (viewport px, relative to the
  // canvas) and whether a drag is live. Kept in refs so the pointer handlers
  // stay cheap and never re-instantiate Sigma.
  const lassoPointsRef = useRef<Point[]>([]);
  const drawingRef = useRef(false);

  const [showHulls, setShowHulls] = useState(true);
  const [lassoActive, setLassoActive] = useState(false);
  const [brushMenuOpen, setBrushMenuOpen] = useState(false);

  const hasClusters = clusters.length > 0;

  // Instantiate Sigma, wire the selection/brush reducer + hull overlay, and fit
  // the camera to the projection (no layout — the projection is the layout).
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    resolveSemanticColors(graph);
    dimRef.current = readVar("--color-graph-dim", DIM_FALLBACK);

    const sigma = new Sigma(graph, container, {
      renderEdgeLabels: false,
      labelFont: LABEL_FONT,
      labelColor: { color: readVar("--color-text", TEXT_FALLBACK) },
      labelDensity: 0.7,
      ...(graph.order > LABEL_LOD_MIN_ORDER ? { labelRenderedSizeThreshold: LABEL_LOD_SIZE } : {}),
      defaultDrawNodeLabel: createWrappedLabelRenderer(
        () => labelMaxWidth(containerRef.current),
        (node) => maxLinesFor(labelStateFor(node)),
      ),
      // Required: sigma's own hover renderer would otherwise draw a second,
      // unwrapped label over this one.
      defaultDrawNodeHover: createWrappedHoverRenderer(
        () => labelMaxWidth(containerRef.current),
        (node) => maxLinesFor(labelStateFor(node)),
      ),
      // Extra breathing room so a tight cluster hugging the projection's edge —
      // and its labels — stay inside the viewport rather than clipping.
      stagePadding: 90,
      nodeReducer: (node, data) => {
        let res = data;
        // Reveal-on-interaction. Unlike the Explorer there is no neighbour
        // layer: adjacency is a graph property, and points that are adjacent in
        // the graph can sit anywhere in a semantic projection, so labelling them
        // would scatter text across unrelated regions.
        res = { ...res, label: revealLabel(String(data.label ?? ""), labelStateFor(node)) };
        const brush = brushSetRef.current;
        if (brush && !brush.has(node)) {
          res = { ...res, color: dimRef.current, label: "", zIndex: 0 };
        } else if (brush) {
          res = { ...res, zIndex: 1 };
        }
        if (node === hoveredRef.current) res = { ...res, highlighted: true, zIndex: 2 };
        if (node === selectionRef.current) res = { ...res, highlighted: true, zIndex: 2 };
        return res;
      },
    });
    sigmaRef.current = sigma;

    // Click-hold-drag node repositioning. There is no force layout (the
    // projection *is* the layout), so no getLayout — dragging a point moves it
    // off its true embedding position, cosmetically, until the next reload. While
    // the lasso is armed its overlay captures the pointer (pointer-events: auto),
    // so drag and lasso are mutually exclusive at the DOM level; no extra gate
    // needed here. Cluster hulls reposition on afterRender and follow for free.
    const drag = createNodeDrag({ sigma, graph, container });

    sigma.on("enterNode", ({ node }) => {
      hoveredRef.current = node;
      sigma.refresh();
    });
    sigma.on("leaveNode", () => {
      hoveredRef.current = null;
      sigma.refresh();
    });
    sigma.on("clickNode", ({ node }) => {
      // Ignore the trailing click Sigma emits after a real drag.
      if (drag.consumeDragMoved()) return;
      useTapestry.getState().select(node);
    });
    sigma.on("clickStage", () => {
      if (drag.consumeDragMoved()) return;
      useTapestry.getState().select(null);
    });

    // Cluster-hull overlay: rebuilt after every render so each hull tracks the
    // camera. Rebuilding a handful of polygons is cheap and keeps the DOM in
    // exact sync with the toggle (zero .semantic__hull nodes when hidden).
    const updateHulls = () => {
      const sig = sigmaRef.current;
      const layer = hullLayerRef.current;
      if (!sig || !layer) return;
      layer.replaceChildren();
      if (!showHullsRef.current) return;

      const positionById = new Map<string, Point>();
      graph.forEachNode((id) => {
        const dd = sig.getNodeDisplayData(id);
        if (!dd) return;
        const vp = sig.framedGraphToViewport(dd);
        positionById.set(id, { x: vp.x, y: vp.y });
      });

      for (const poly of clusterPolygons(clusters, positionById)) {
        if (poly.hull.length < 2) continue;
        const hull = inflateHull(poly.hull, HULL_PAD);
        const shape = document.createElementNS(SVG_NS, "polygon");
        shape.setAttribute("class", "semantic__hull");
        shape.setAttribute("points", hull.map((p) => `${p.x},${p.y}`).join(" "));
        layer.appendChild(shape);

        const cx = hull.reduce((sum, p) => sum + p.x, 0) / hull.length;
        const topY = Math.min(...hull.map((p) => p.y));
        const text = document.createElementNS(SVG_NS, "text");
        text.setAttribute("class", "semantic__hulllabel");
        text.setAttribute("x", String(cx));
        text.setAttribute("y", String(topY - 8));
        text.setAttribute("text-anchor", "middle");
        text.textContent = poly.label;
        layer.appendChild(text);
      }
    };
    sigma.on("afterRender", updateHulls);

    void sigma.getCamera().animatedReset();

    return () => {
      drag.destroy();
      sigma.kill();
      sigmaRef.current = null;
      hullLayerRef.current?.replaceChildren();
    };
  }, [graph, clusters]);

  // Selection layer: keep the reducer current and repaint the halo.
  useEffect(() => {
    selectionRef.current = selection;
    sigmaRef.current?.refresh();
  }, [selection]);

  // Brush layer: mirror the store's brushed set so the map dims non-brushed
  // points too, echoing the Explorer's brushed emphasis.
  useEffect(() => {
    brushSetRef.current = brushedIds ? new Set(brushedIds) : null;
    sigmaRef.current?.refresh();
  }, [brushedIds]);

  // Hull toggle: refresh forces an afterRender so the overlay redraws/clears.
  useEffect(() => {
    showHullsRef.current = showHulls;
    sigmaRef.current?.refresh();
  }, [showHulls]);

  // Opening the cluster-brush menu moves focus to its first cluster, so a
  // keyboard user lands ready to choose (the menu-button pattern).
  useEffect(() => {
    if (brushMenuOpen) firstClusterRef.current?.focus();
  }, [brushMenuOpen]);

  // Arming/disarming the lasso clears any half-drawn path.
  useEffect(() => {
    if (!lassoActive) {
      drawingRef.current = false;
      lassoPointsRef.current = [];
      lassoPathRef.current?.setAttribute("points", "");
    }
  }, [lassoActive]);

  // Re-resolve token colours when the resolved theme changes. rAF defers past
  // App's applyTheme so we read the freshly-stamped `data-theme`. Hull/lasso
  // colours are CSS-driven (DOM overlays) and re-theme automatically; only the
  // canvas node fills need this JS pass.
  useEffect(() => {
    let raf = 0;
    const reresolve = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        resolveSemanticColors(graph);
        dimRef.current = readVar("--color-graph-dim", DIM_FALLBACK);
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

  // ---- Lasso pointer handlers (only live while the tool is armed) -----------
  const canvasRect = (): DOMRect | null => containerRef.current?.getBoundingClientRect() ?? null;

  const drawLasso = (): void => {
    lassoPathRef.current?.setAttribute(
      "points",
      lassoPointsRef.current.map((p) => `${p.x},${p.y}`).join(" "),
    );
  };

  const onLassoDown = (event: React.PointerEvent<HTMLDivElement>): void => {
    if (!lassoActive) return;
    const rect = canvasRect();
    if (!rect) return;
    drawingRef.current = true;
    lassoPointsRef.current = [{ x: event.clientX - rect.left, y: event.clientY - rect.top }];
    event.currentTarget.setPointerCapture(event.pointerId);
    drawLasso();
  };

  const onLassoMove = (event: React.PointerEvent<HTMLDivElement>): void => {
    if (!drawingRef.current) return;
    const rect = canvasRect();
    if (!rect) return;
    lassoPointsRef.current.push({ x: event.clientX - rect.left, y: event.clientY - rect.top });
    drawLasso();
  };

  const onLassoUp = (): void => {
    if (!drawingRef.current) return;
    drawingRef.current = false;
    const path = lassoPointsRef.current;
    const sigma = sigmaRef.current;
    if (sigma && path.length >= MIN_LASSO_POINTS) {
      const pts: (Point & { id: string })[] = [];
      graph.forEachNode((id) => {
        const dd = sigma.getNodeDisplayData(id);
        if (!dd) return;
        const vp = sigma.framedGraphToViewport(dd);
        pts.push({ id, x: vp.x, y: vp.y });
      });
      const ids = pointsInLasso(path, pts);
      setBrushed(ids.length ? ids : null);
    }
    lassoPointsRef.current = [];
    drawLasso();
  };

  // Exports — a WYSIWYG snapshot of the projection. There is no filter layer
  // (every point is always visible; the brush only dims), so the SVG's
  // visibility set is `allVisible`. Hull outlines are a DOM/SVG overlay, not
  // part of the Sigma canvas, so neither export carries them — the SVG's
  // legend documents the projection method and, when present, the cluster
  // region instead, and both controls' titles say so plainly.
  const exportPng = useCallback(() => {
    const sigma = sigmaRef.current;
    if (!sigma) return;
    void exportPngFile(
      sigma,
      exportFilename(graphKey, "semantic", "png"),
      readVar("--color-canvas", CANVAS_FALLBACK),
    );
  }, [graphKey]);

  const exportSvg = useCallback(() => {
    const sigma = sigmaRef.current;
    if (!sigma) return;
    const legend = [
      { label: `${method === "umap" ? "UMAP" : "PCA"} projection`, color: readVar("--color-graph-dim", DIM_FALLBACK) },
      ...(hasClusters
        ? [{ label: "cluster region", color: readVar("--hull-stroke", HULL_STROKE_FALLBACK) }]
        : []),
    ];
    exportSvgFile(
      sigma,
      graph,
      allVisible(graph),
      {
        textColor: readVar("--color-text", TEXT_FALLBACK),
        background: readVar("--color-canvas", CANVAS_FALLBACK),
        legend,
      },
      exportFilename(graphKey, "semantic", "svg"),
    );
  }, [graph, graphKey, method, hasClusters]);

  const brushCount = brushedIds?.length ?? 0;
  const pointCount = graph.order;

  return (
    <section
      id="panel-semantic"
      className="semantic"
      role="tabpanel"
      aria-labelledby="tab-semantic"
      tabIndex={0}
    >
      {semanticIsCurrentOnly && (
        <p className="temporal-note semantic__temporalnote" role="note">
          The embedding map reflects current state, not the historical snapshot shown here.
        </p>
      )}

      <div className="semantic__canvas" ref={containerRef} />

      <svg className="semantic__hulls" ref={hullLayerRef} aria-hidden="true" />

      <div
        className={`semantic__lasso${lassoActive ? " semantic__lasso--on" : ""}`}
        onPointerDown={onLassoDown}
        onPointerMove={onLassoMove}
        onPointerUp={onLassoUp}
        onPointerCancel={onLassoUp}
      >
        <svg className="semantic__lassosvg" aria-hidden="true">
          <polyline className="semantic__lassopath" ref={lassoPathRef} points="" />
        </svg>
      </div>

      {pointCount === 0 ? (
        <div className="semantic__empty">
          <p className="semantic__emptytitle">No embeddings in this scope.</p>
          <p className="semantic__emptybody">
            The Semantic Map plots entity embedding vectors. Re-export with the semantic
            section, or embed the graph's entities, to see the map.
          </p>
        </div>
      ) : (
        <div className="semantic__legend" role="img" aria-label="Semantic map legend">
          <span className="semantic__legendmethod">
            {method === "umap" ? "UMAP" : "PCA"} projection
          </span>
          <span className="semantic__legenddot" aria-hidden="true">
            ·
          </span>
          <span className="semantic__legendcount">
            {pointCount} point{pointCount === 1 ? "" : "s"}
          </span>
          {hasClusters && (
            <span className="semantic__legenditem">
              <span className="semantic__legendhull" aria-hidden="true" />
              cluster region
            </span>
          )}
        </div>
      )}

      {brushCount > 0 && (
        <div className="semantic__brush" role="status">
          <span className="semantic__brushdot" aria-hidden="true" />
          <span className="semantic__brushcount">
            {brushCount} brushed
          </span>
          <button
            type="button"
            className="semantic__brushlink"
            onClick={() => setView("explorer")}
          >
            View in Explorer
          </button>
          <button
            type="button"
            className="semantic__brushclear"
            aria-label="Clear brush"
            onClick={() => setBrushed(null)}
          >
            Clear
          </button>
        </div>
      )}

      <div className="semantic__controls">
        <button
          type="button"
          className="semantic__ctrl"
          aria-pressed={showHulls}
          disabled={!hasClusters}
          title={hasClusters ? undefined : "No clusters in this scope"}
          onClick={() => setShowHulls((v) => !v)}
        >
          <HullIcon />
          {showHulls ? "Hide hulls" : "Show hulls"}
        </button>

        <button
          type="button"
          className="semantic__ctrl semantic__lassotoggle"
          aria-pressed={lassoActive}
          disabled={pointCount === 0}
          title="Draw a loop to brush a set of points"
          onClick={() => setLassoActive((v) => !v)}
        >
          <LassoIcon />
          {lassoActive ? "Lasso on" : "Lasso"}
        </button>

        {/* Keyboard alternative to the pointer-only lasso: pick a cluster to
         * brush its members — the same effect the lasso produces, reachable
         * with the keyboard. A menu button: opening moves focus to the first
         * cluster, and choosing one returns focus to the trigger. The list
         * renders AFTER the button in the DOM so Tab flows into it (it still
         * pops UP visually). Guarded behind hasClusters (no clusters, no list). */}
        {hasClusters && (
          <div className="semantic__brushpicker">
            <button
              type="button"
              ref={brushToggleRef}
              className="semantic__ctrl"
              aria-haspopup="true"
              aria-expanded={brushMenuOpen}
              aria-controls={brushMenuOpen ? "semantic-cluster-list" : undefined}
              title="Brush a cluster's members (keyboard alternative to the lasso)"
              onClick={() => setBrushMenuOpen((v) => !v)}
            >
              <BrushIcon />
              Brush cluster
            </button>
            {brushMenuOpen && (
              <div
                className="semantic__clusterlist"
                id="semantic-cluster-list"
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    setBrushMenuOpen(false);
                    brushToggleRef.current?.focus();
                  }
                }}
              >
                <p className="semantic__clusterhint">Brush a cluster to select its members</p>
                {clusters.map((cluster, i) => (
                  <button
                    key={cluster.id}
                    type="button"
                    ref={i === 0 ? firstClusterRef : undefined}
                    className="semantic__clusteritem"
                    onClick={() => {
                      setBrushed(cluster.entityIds);
                      setBrushMenuOpen(false);
                      brushToggleRef.current?.focus();
                    }}
                  >
                    <span className="semantic__clusterlabel">{cluster.label}</span>
                    <span className="semantic__clustersize">{cluster.size}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        <button
          type="button"
          className="semantic__ctrl"
          disabled={pointCount === 0}
          title="Export PNG. Cluster hull outlines are an on-screen overlay and are not included."
          onClick={exportPng}
        >
          <DownloadIcon />
          PNG
        </button>

        <button
          type="button"
          className="semantic__ctrl"
          disabled={pointCount === 0}
          title="Export SVG with a projection/cluster legend. Hull outlines are an on-screen overlay and are not included."
          onClick={exportSvg}
        >
          <DownloadIcon />
          SVG
        </button>
      </div>
    </section>
  );
}

function DownloadIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true">
      <path
        d="M8 2v7.5M8 9.5 5 6.5M8 9.5l3-3"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M2.5 11.5v1.5a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1v-1.5"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function BrushIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <rect
        x="2.2"
        y="2.7"
        width="11.6"
        height="10.6"
        rx="3"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeDasharray="2.4 1.8"
      />
      <circle cx="6" cy="7" r="1.2" fill="currentColor" />
      <circle cx="10.2" cy="6.2" r="1.2" fill="currentColor" />
      <circle cx="8" cy="10.2" r="1.2" fill="currentColor" />
    </svg>
  );
}

function HullIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <path
        d="M5.5 2.6 12.4 5 13.4 11 8 14 2.6 10.4 3 4.4Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
      <circle cx="5.5" cy="6" r="1" fill="currentColor" />
      <circle cx="9.5" cy="5" r="1" fill="currentColor" />
      <circle cx="8" cy="10" r="1" fill="currentColor" />
    </svg>
  );
}

function LassoIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <ellipse
        cx="8"
        cy="6.5"
        rx="5.4"
        ry="3.6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeDasharray="2 1.6"
      />
      <path
        d="M6 9.6c0 1.8-.6 3.4-2 4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
      />
      <circle cx="4" cy="13.6" r="1.1" fill="currentColor" />
    </svg>
  );
}
