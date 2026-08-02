/**
 * Explorer — the Graph Explorer view.
 *
 * Instantiates Sigma over the shared graphology model, runs ForceAtlas2 for a
 * few seconds to settle the weave, then hands control to the reader (wheel-zoom,
 * drag-pan, and a physics toggle). Overlaid on the canvas are the search box,
 * filter panel, detail panel, path summary bar, and a minimap.
 *
 * Interaction is expressed through Sigma reducers, not by mutating the graph.
 * Four layers combine, in order: the FILTER layer hides nodes/edges outside the
 * active `filters` (non-destructive); the PATH layer dims everything outside an
 * active path-mode path and highlights the path's edges; the HOVER layer dims
 * everything but the hovered node's neighbourhood; the SELECTION layer haloes
 * the selected node. Reducers read live values from refs and re-run on
 * `sigma.refresh()`. Concrete highlight colours re-resolve from the token layer
 * whenever the theme changes, mirroring `resolveGraphColors`.
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from "react";
import type Graph from "graphology";
import Sigma from "sigma";
import { useBundle, useGraph } from "../../lib/BundleContext";
import { createNodeDrag } from "../../lib/dragNodes";
import { useKeyboard } from "../../lib/keyboard";
import { downloadBlob, exportFilename, exportPngFile, exportSvgFile } from "../../lib/exportSvg";
import {
  deleteView,
  importViews,
  listViews,
  renameView,
  saveView,
  serializeViews,
  type SavedView,
} from "../../lib/savedViews";
import { useTapestry } from "../../state/store";
import { applyHash } from "../../state/urlHash";
import { ENTITY_TYPES } from "../../design/palette";
import { edgeFamily, resolveGraphColors, resolveTypeColor } from "./buildGraph";
import { applyFilters, type Visibility } from "./filters";
import { createLayout, type LayoutController } from "./layout";
import { findPath, type Path } from "./pathMode";
import { Minimap } from "./Minimap";
import { SearchBox } from "./SearchBox";
import { FilterPanel } from "./FilterPanel";
import { DetailPanel } from "./DetailPanel";
import { Legend } from "./Legend";
import {
  createWrappedHoverRenderer,
  createWrappedLabelRenderer,
  labelMaxWidth,
  maxLinesFor,
  revealLabel,
} from "../../lib/nodeLabels";
import "./Explorer.css";

/** How long FA2 runs on mount before the layout freezes. */
const LAYOUT_MS = 3000;
/**
 * Label level-of-detail: above this node count, raise sigma's
 * `labelRenderedSizeThreshold` so only the largest (highest-degree) nodes keep a
 * label — at 50k a label per node is unreadable and expensive. Gated on order so
 * the fixture (10 nodes) is untouched and keeps sigma's default (6) behaviour.
 */
const LABEL_LOD_MIN_ORDER = 2000;
/** The threshold applied at scale (node size is `3 + 2*sqrt(degree)`). */
const LABEL_LOD_SIZE = 14;
/** A concrete family stack for canvas labels (CSS vars don't resolve on canvas). */
const LABEL_FONT = "system-ui, sans-serif";
/** Fallbacks mirror tokens.css `--color-text` / `--color-graph-dim` / `--color-accent` (light). */
const TEXT_FALLBACK = "#16181f";
const DIM_FALLBACK = "#dfdedb";
const ACCENT_FALLBACK = "#4a44c4";
/** Fallback mirrors tokens.css `--color-canvas` (light) — the SVG export background. */
const CANVAS_FALLBACK = "#f7f6f3";

type ScreenDirection = "up" | "down" | "left" | "right";

function readVar(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

/** A live path's node/edge membership, cheap to test from the reducers. */
interface PathHighlight {
  nodes: Set<string>;
  edges: Set<string>;
}

export function Explorer() {
  const graph = useGraph();
  const graphKey = useBundle().meta.graph;
  const theme = useTapestry((s) => s.theme);
  const filters = useTapestry((s) => s.filters);
  const selection = useTapestry((s) => s.selection);
  const select = useTapestry((s) => s.select);
  const pathMode = useTapestry((s) => s.pathMode);
  const pathEndpoints = useTapestry((s) => s.pathEndpoints);
  const setPathMode = useTapestry((s) => s.setPathMode);
  const clearPath = useTapestry((s) => s.clearPath);
  const brushedIds = useTapestry((s) => s.brushedIds);
  const setBrushed = useTapestry((s) => s.setBrushed);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const layoutRef = useRef<LayoutController | null>(null);

  // Live reducer inputs — held in refs so the render loop reads current values
  // without re-instantiating Sigma.
  const visibilityRef = useRef<Visibility | null>(null);
  const hoveredRef = useRef<string | null>(null);
  const neighborsRef = useRef<Set<string> | null>(null);
  const selectionRef = useRef<string | null>(null);
  /** Interaction state a node's label is decided from. Reads refs, so it is
   * safe to call from inside sigma's reducers and label renderer. */
  const labelStateFor = useCallback(
    (node: string) => ({
      hovered: node === hoveredRef.current,
      selected: node === selectionRef.current,
      neighbor: neighborsRef.current?.has(node) ?? false,
    }),
    [],
  );
  const pathHighlightRef = useRef<PathHighlight | null>(null);
  // The Semantic Map lasso's brushed set, echoed here as an emphasis layer.
  const brushSetRef = useRef<Set<string> | null>(null);
  const dimRef = useRef<string>(DIM_FALLBACK);
  const accentRef = useRef<string>(ACCENT_FALLBACK);

  const [running, setRunning] = useState(false);
  // Mirrors sigmaRef in React state, purely so children (the minimap) can be
  // rendered once the instance exists — the reducers above still read the ref.
  const [sigmaInstance, setSigmaInstance] = useState<Sigma | null>(null);
  // Mirrors pathHighlightRef in React state so the summary bar can render the
  // hop list reactively; the ref stays the source of truth for the reducers.
  const [pathResult, setPathResult] = useState<Path | null>(null);

  // Instantiate Sigma, wire reducers + interaction, and run the force layout once.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    resolveGraphColors(graph);
    dimRef.current = readVar("--color-graph-dim", DIM_FALLBACK);
    accentRef.current = readVar("--color-accent", ACCENT_FALLBACK);

    const sigma = new Sigma(graph, container, {
      renderEdgeLabels: false,
      defaultEdgeType: "arrow",
      labelFont: LABEL_FONT,
      labelColor: { color: readVar("--color-text", TEXT_FALLBACK) },
      labelDensity: 0.6,
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
      nodeReducer: (node, data) => {
        const vis = visibilityRef.current;
        if (vis && !vis.visibleNodes.has(node)) return { ...data, hidden: true };

        // Reveal-on-interaction: only the focused node and its neighbourhood
        // carry a label. Runs first so the dim/brush/path branches below can
        // still blank it, as they do today.
        let res = data;
        res = { ...res, label: revealLabel(String(data.label ?? ""), labelStateFor(node)) };
        // BRUSH layer: when the Semantic Map lasso is active, emphasise its set
        // and dim the rest (emphasis, never hiding — that stays the FILTER job).
        const brush = brushSetRef.current;
        if (brush && !brush.has(node)) {
          res = { ...res, color: dimRef.current, label: "", zIndex: 0 };
        } else if (brush) {
          res = { ...res, zIndex: 1 };
        }

        const path = pathHighlightRef.current;
        const inPath = path?.nodes.has(node) ?? false;
        if (path && !inPath) {
          res = { ...res, color: dimRef.current, label: "", zIndex: 0 };
        } else if (inPath) {
          res = { ...res, zIndex: 1 };
        }

        const hovered = hoveredRef.current;
        const selected = selectionRef.current;
        if (hovered) {
          if (node === hovered) {
            res = { ...res, highlighted: true, zIndex: 1 };
          } else if (neighborsRef.current?.has(node)) {
            res = { ...res, zIndex: 1 };
          } else if (node !== selected && !inPath) {
            res = { ...res, color: dimRef.current, label: "", zIndex: 0 };
          }
        }
        if (node === selected) res = { ...res, highlighted: true, zIndex: 1 };
        return res;
      },
      edgeReducer: (edge, data) => {
        const vis = visibilityRef.current;
        if (vis && !vis.visibleEdges.has(edge)) return { ...data, hidden: true };

        let res = data;
        // BRUSH layer: dim any edge with an endpoint outside the brushed set.
        const brush = brushSetRef.current;
        if (brush) {
          const [source, target] = graph.extremities(edge);
          if (!brush.has(source) || !brush.has(target)) {
            res = { ...res, color: dimRef.current, zIndex: 0 };
          }
        }

        const path = pathHighlightRef.current;
        const inPath = path?.edges.has(edge) ?? false;
        if (path) {
          res = inPath
            ? { ...res, color: accentRef.current, size: (res.size ?? 1.5) + 1.5, zIndex: 1 }
            : { ...res, color: dimRef.current, zIndex: 0 };
        }

        const hovered = hoveredRef.current;
        if (hovered && !inPath && !graph.hasExtremity(edge, hovered)) {
          res = { ...res, color: dimRef.current, zIndex: 0 };
        }
        return res;
      },
    });
    sigmaRef.current = sigma;
    setSigmaInstance(sigma);

    // Click-hold-drag node repositioning (Obsidian-style). Pauses/resumes the
    // physics layout around the drag; its `consumeDragMoved()` latch lets the
    // click handlers below ignore the trailing click Sigma emits after a drag.
    const drag = createNodeDrag({
      sigma,
      graph,
      container,
      getLayout: () => layoutRef.current,
    });

    sigma.on("enterNode", ({ node }) => {
      // While the physics loop is running, skip the hover layer entirely: at 50k
      // nodes the neighbour-set build + refresh would fight the layout's own
      // per-frame repaints. Hover resumes the instant the layout freezes.
      if (layoutRef.current?.running) return;
      hoveredRef.current = node;
      const nbrs = new Set<string>();
      graph.forEachNeighbor(node, (n) => nbrs.add(n));
      neighborsRef.current = nbrs;
      sigma.refresh();
    });
    sigma.on("leaveNode", () => {
      if (layoutRef.current?.running) return;
      hoveredRef.current = null;
      neighborsRef.current = null;
      sigma.refresh();
    });
    sigma.on("clickNode", ({ node }) => {
      // A real drag just ended on this node — don't also select / pick an
      // endpoint from the click Sigma emits after the drag.
      if (drag.consumeDragMoved()) return;
      const state = useTapestry.getState();
      if (state.pathMode) {
        const [from, to] = state.pathEndpoints;
        state.setPathEndpoints(from === null || to !== null ? [node, null] : [from, node]);
        return;
      }
      state.select(node);
    });
    sigma.on("clickStage", () => {
      // A drag that ended over the stage must not deselect.
      if (drag.consumeDragMoved()) return;
      useTapestry.getState().select(null);
    });

    const layout = createLayout(graph);
    layoutRef.current = layout;
    layout.start();
    setRunning(true);

    const timer = window.setTimeout(() => {
      layout.stop();
      setRunning(false);
      void sigma.getCamera().animatedReset();
    }, LAYOUT_MS);

    return () => {
      window.clearTimeout(timer);
      drag.destroy();
      layout.kill();
      sigma.kill();
      sigmaRef.current = null;
      layoutRef.current = null;
      setSigmaInstance(null);
    };
  }, [graph]);

  // Filter layer: recompute the visible sets whenever filters (or graph) change.
  useEffect(() => {
    visibilityRef.current = applyFilters(graph, filters);
    sigmaRef.current?.refresh();
  }, [graph, filters]);

  // Path layer: recompute the highlighted path whenever the endpoints (or
  // graph) change. Both a ref (for the reducers) and state (for the summary
  // bar) are kept, mirroring how `selection` and `selectionRef` split duties.
  useEffect(() => {
    const [from, to] = pathEndpoints;
    const found =
      from && to && graph.hasNode(from) && graph.hasNode(to) ? findPath(graph, from, to) : null;
    pathHighlightRef.current = found ? { nodes: new Set(found.nodes), edges: new Set(found.edges) } : null;
    setPathResult(found);
    sigmaRef.current?.refresh();
  }, [graph, pathEndpoints]);

  // Selection layer: keep the reducer's selection current and repaint the halo.
  useEffect(() => {
    selectionRef.current = selection;
    sigmaRef.current?.refresh();
  }, [selection]);

  // Brush layer: mirror the store's brushed set (set by the Semantic Map lasso)
  // into the reducer ref and repaint, mirroring the selection effect.
  useEffect(() => {
    brushSetRef.current = brushedIds ? new Set(brushedIds) : null;
    sigmaRef.current?.refresh();
  }, [brushedIds]);

  // Re-resolve token colours when the resolved theme changes (explicit switch or
  // OS scheme change while on "auto"). rAF defers past App's applyTheme effect so
  // we read the freshly-stamped `data-theme`.
  useEffect(() => {
    let raf = 0;
    const reresolve = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        resolveGraphColors(graph);
        dimRef.current = readVar("--color-graph-dim", DIM_FALLBACK);
        accentRef.current = readVar("--color-accent", ACCENT_FALLBACK);
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

  const togglePhysics = () => {
    const layout = layoutRef.current;
    if (!layout) return;
    if (layout.running) {
      layout.stop();
      setRunning(false);
    } else {
      layout.start();
      setRunning(true);
    }
  };

  const togglePathMode = useCallback(() => {
    const next = !pathMode;
    setPathMode(next);
    if (!next) clearPath();
  }, [pathMode, setPathMode, clearPath]);

  // Exports — a WYSIWYG snapshot of the current WebGL state, drawn from the
  // live sigma instance and the current FILTER-layer visibility. Both share the
  // `<graph>-<view>-<date>` filename convention every view's export uses.
  const exportPng = useCallback(() => {
    const sigma = sigmaRef.current;
    if (!sigma) return;
    void exportPngFile(
      sigma,
      exportFilename(graphKey, "explorer", "png"),
      readVar("--color-canvas", CANVAS_FALLBACK),
    );
  }, [graphKey]);

  const exportSvg = useCallback(() => {
    const sigma = sigmaRef.current;
    if (!sigma) return;
    const visible = applyFilters(graph, filters);
    // Legend: the entity types actually present among the visible nodes, in
    // canonical ENTITY_TYPES order — never every type the bundle could carry.
    const presentTypes = new Set<string>();
    for (const node of visible.visibleNodes) {
      presentTypes.add(graph.getNodeAttribute(node, "entityType") as string);
    }
    const legend = (ENTITY_TYPES as readonly string[])
      .filter((t) => presentTypes.has(t))
      .map((t) => ({ label: t.replace(/_/g, " "), color: resolveTypeColor(t) }));
    exportSvgFile(
      sigma,
      graph,
      visible,
      {
        textColor: readVar("--color-text", TEXT_FALLBACK),
        background: readVar("--color-canvas", CANVAS_FALLBACK),
        legend,
      },
      exportFilename(graphKey, "explorer", "svg"),
    );
  }, [graph, filters, graphKey]);

  // Select an entity and fly the camera to it — used by search, the detail
  // panel's neighbour list, and arrow-key walking. A direct node click just
  // selects (it's already in view).
  const navigate = useCallback(
    (id: string) => {
      select(id);
      const sigma = sigmaRef.current;
      if (!sigma || !graph.hasNode(id)) return;
      const pos = sigma.getNodeDisplayData(id);
      if (!pos) return;
      const camera = sigma.getCamera();
      void camera.animate(
        { x: pos.x, y: pos.y, ratio: Math.min(camera.getState().ratio, 0.7) },
        { duration: 500 },
      );
    },
    [select, graph],
  );

  // Arrow-key walking: among the selected node's neighbours, pick the closest
  // one lying within the given screen-space direction's 90° cone, using Sigma's
  // own viewport projection so this respects the current camera rotation/zoom.
  const nearestNeighbor = useCallback(
    (dir: ScreenDirection): string | null => {
      const sigma = sigmaRef.current;
      const current = useTapestry.getState().selection;
      if (!sigma || !current || !graph.hasNode(current)) return null;
      const origin = sigma.getNodeDisplayData(current);
      if (!origin) return null;
      const originVp = sigma.graphToViewport(origin);

      let best: string | null = null;
      let bestDist = Infinity;
      graph.forEachNeighbor(current, (neighbor) => {
        const pos = sigma.getNodeDisplayData(neighbor);
        if (!pos) return;
        const vp = sigma.graphToViewport(pos);
        const dx = vp.x - originVp.x;
        const dy = vp.y - originVp.y; // screen space: y grows downward
        const inCone =
          dir === "up"
            ? dy < 0 && Math.abs(dy) >= Math.abs(dx)
            : dir === "down"
              ? dy > 0 && Math.abs(dy) >= Math.abs(dx)
              : dir === "left"
                ? dx < 0 && Math.abs(dx) >= Math.abs(dy)
                : dx > 0 && Math.abs(dx) >= Math.abs(dy);
        if (!inCone) return;
        const dist = Math.hypot(dx, dy);
        if (dist < bestDist) {
          bestDist = dist;
          best = neighbor;
        }
      });
      return best;
    },
    [graph],
  );

  const walk = useCallback(
    (dir: ScreenDirection) => {
      const next = nearestNeighbor(dir);
      if (next) navigate(next);
    },
    [nearestNeighbor, navigate],
  );

  useKeyboard({
    "/": (e) => {
      e.preventDefault();
      document.getElementById("explorer-search-input")?.focus();
    },
    p: () => togglePathMode(),
    f: () => {
      void sigmaRef.current?.getCamera().animatedReset();
    },
    Escape: () => {
      select(null);
      clearPath();
    },
    ArrowUp: (e) => {
      e.preventDefault();
      walk("up");
    },
    ArrowDown: (e) => {
      e.preventDefault();
      walk("down");
    },
    ArrowLeft: (e) => {
      e.preventDefault();
      walk("left");
    },
    ArrowRight: (e) => {
      e.preventDefault();
      walk("right");
    },
  });

  return (
    <section
      id="panel-explorer"
      className="explorer"
      role="tabpanel"
      aria-labelledby="tab-explorer"
      tabIndex={0}
    >
      <div className="explorer__canvas" ref={containerRef} />

      {graph.order === 0 && (
        <div className="explorer__empty">
          <p>This graph has no entities to weave yet.</p>
        </div>
      )}

      <div className="explorer__toolbar">
        <SearchBox onNavigate={navigate} />
        <FilterPanel />
        {brushedIds && (
          <div className="explorer__brush" role="status">
            <span className="explorer__brushdot" aria-hidden="true" />
            <span className="explorer__brushcount">{brushedIds.length} brushed</span>
            <button
              type="button"
              className="explorer__brushclear"
              aria-label="Clear brush"
              onClick={() => setBrushed(null)}
            >
              <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true">
                <path
                  d="M4 4l8 8M12 4l-8 8"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                />
              </svg>
            </button>
          </div>
        )}
      </div>

      {pathMode && (
        <PathBar endpoints={pathEndpoints} result={pathResult} graph={graph} onClear={clearPath} />
      )}

      {selection && <DetailPanel onNavigate={navigate} />}

      <Legend />

      <div className="explorer__controls">
        <button
          type="button"
          className="explorer__physics"
          aria-pressed={running}
          onClick={togglePhysics}
        >
          <span
            className={`explorer__pulse${running ? " explorer__pulse--live" : ""}`}
            aria-hidden="true"
          />
          {running ? "Pause layout" : "Resume layout"}
        </button>

        <button
          type="button"
          className={`explorer__pathmode${pathMode ? " explorer__pathmode--on" : ""}`}
          aria-pressed={pathMode}
          onClick={togglePathMode}
          title="Toggle path mode (p)"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
            <circle cx="3" cy="12.5" r="1.6" fill="currentColor" />
            <circle cx="13" cy="3.5" r="1.6" fill="currentColor" />
            <path
              d="M4.2 11.3 11.8 4.7"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeDasharray="1.6 1.8"
              strokeLinecap="round"
            />
          </svg>
          Path mode
        </button>

        <ViewsMenu graphKey={graphKey} onExportPng={exportPng} onExportSvg={exportSvg} />
      </div>

      {sigmaInstance && <Minimap sigma={sigmaInstance} graph={graph} />}
    </section>
  );
}

/**
 * ViewsMenu — exports and saved views, tucked behind one toggle in the
 * floating control cluster. "Export PNG/SVG" hand off to the sigma-backed
 * callbacks passed in from `Explorer`; "Save current view" bookmarks the URL
 * hash App already keeps current (`serializeState`) under a name in
 * `localStorage`; each saved row applies its hash live via `applyHash` — no
 * reload — or can be deleted. Saved views are scoped per graph so switching
 * bundles never surfaces another graph's bookmarks.
 */
function ViewsMenu({
  graphKey,
  onExportPng,
  onExportSvg,
}: {
  graphKey: string;
  onExportPng: () => void;
  onExportSvg: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [views, setViews] = useState<SavedView[]>(() => listViews(graphKey));
  const [name, setName] = useState("");
  const [renaming, setRenaming] = useState<{ name: string; value: string; error: string | null } | null>(
    null,
  );
  const [importNotice, setImportNotice] = useState<{ text: string; error: boolean } | null>(null);

  const refresh = useCallback(() => setViews(listViews(graphKey)), [graphKey]);

  const submitSave = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || !window.location.hash) return;
    saveView(graphKey, trimmed, window.location.hash);
    setName("");
    refresh();
  };

  const apply = (view: SavedView) => {
    applyHash(view.hash);
    setOpen(false);
  };

  const remove = (view: SavedView, event: ReactMouseEvent) => {
    event.stopPropagation();
    deleteView(graphKey, view.name);
    refresh();
  };

  const startRename = (view: SavedView) => setRenaming({ name: view.name, value: view.name, error: null });

  const submitRename = (event: FormEvent) => {
    event.preventDefault();
    if (!renaming) return;
    const trimmed = renaming.value.trim();
    if (!trimmed) {
      setRenaming({ ...renaming, error: "Enter a name." });
      return;
    }
    if (!renameView(graphKey, renaming.name, trimmed)) {
      setRenaming({ ...renaming, error: "That name is already taken." });
      return;
    }
    setRenaming(null);
    refresh();
  };

  const exportViews = () => {
    downloadBlob(
      new Blob([serializeViews(graphKey)], { type: "application/json" }),
      exportFilename(graphKey, "views", "json"),
    );
  };

  const onImportFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = ""; // allow re-selecting the same file on a retry
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const text = typeof reader.result === "string" ? reader.result : "";
      const { added, error } = importViews(graphKey, text);
      if (error) {
        setImportNotice({ text: error, error: true });
        return;
      }
      setImportNotice({ text: `Imported ${added} saved view${added === 1 ? "" : "s"}.`, error: false });
      refresh();
    };
    reader.readAsText(file);
  };

  return (
    <div className="views">
      <button
        type="button"
        className={`views__toggle${open ? " views__toggle--on" : ""}`}
        aria-expanded={open}
        aria-controls="views-panel"
        onClick={() => {
          if (!open) refresh();
          setOpen((v) => !v);
        }}
      >
        <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
          <path
            d="M4 2.5h8a1 1 0 0 1 1 1V14l-5-3-5 3V3.5a1 1 0 0 1 1-1Z"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.4"
            strokeLinejoin="round"
          />
        </svg>
        Views
      </button>

      {open && (
        <div id="views-panel" className="views__panel" role="group" aria-label="Exports and saved views">
          <section className="views__section">
            <span className="views__eyebrow">Export</span>
            <div className="views__exportrow">
              <button type="button" className="views__exportbtn" onClick={onExportPng}>
                <DownloadIcon />
                PNG
              </button>
              <button type="button" className="views__exportbtn" onClick={onExportSvg}>
                <DownloadIcon />
                SVG
              </button>
            </div>
          </section>

          <section className="views__section">
            <span className="views__eyebrow">Save current view</span>
            <form className="views__saverow" onSubmit={submitSave}>
              <input
                className="views__input"
                type="text"
                placeholder="View name…"
                value={name}
                aria-label="View name"
                onChange={(event) => setName(event.target.value)}
              />
              <button type="submit" className="views__savebtn" disabled={!name.trim()}>
                Save
              </button>
            </form>
          </section>

          <section className="views__section">
            <div className="views__sectionhead">
              <span className="views__eyebrow">Saved views</span>
              <div className="views__mgmtrow">
                <button
                  type="button"
                  className="views__mgmtbtn"
                  onClick={exportViews}
                  disabled={views.length === 0}
                  title="Export saved views as JSON"
                >
                  <DownloadIcon />
                  Export
                </button>
                <label className="views__mgmtbtn views__importlabel" title="Import saved views from JSON">
                  <UploadIcon />
                  Import
                  <input
                    type="file"
                    accept="application/json"
                    className="views__importinput"
                    onChange={onImportFile}
                  />
                </label>
              </div>
            </div>

            {importNotice && (
              <p
                className={`views__notice${importNotice.error ? " views__notice--error" : ""}`}
                role={importNotice.error ? "alert" : "status"}
              >
                {importNotice.text}
              </p>
            )}

            {views.length === 0 ? (
              <p className="views__empty">No saved views yet.</p>
            ) : (
              <ul className="views__list">
                {views.map((view) => {
                  const isRenaming = renaming?.name === view.name;
                  return (
                    <li key={view.name} className="views__item">
                      <div className="views__itemrow">
                        {isRenaming ? (
                          <form className="views__renamerow" onSubmit={submitRename}>
                            <input
                              className="views__input views__renameinput"
                              type="text"
                              autoFocus
                              aria-label={`Rename saved view ${view.name}`}
                              value={renaming.value}
                              onChange={(event) =>
                                setRenaming({ name: view.name, value: event.target.value, error: null })
                              }
                              onKeyDown={(event) => {
                                if (event.key === "Escape") setRenaming(null);
                              }}
                            />
                            <button type="submit" className="views__itemconfirm" aria-label="Confirm rename">
                              <CheckIcon />
                            </button>
                            <button
                              type="button"
                              className="views__itemcancel"
                              aria-label="Cancel rename"
                              onClick={() => setRenaming(null)}
                            >
                              <CancelIcon />
                            </button>
                          </form>
                        ) : (
                          <>
                            <button type="button" className="views__itemapply" onClick={() => apply(view)}>
                              <span className="views__itemname">{view.name}</span>
                              <span className="views__itemtime">
                                {new Date(view.savedAt).toLocaleString()}
                              </span>
                            </button>
                            <button
                              type="button"
                              className="views__itemrename"
                              aria-label={`Rename saved view ${view.name}`}
                              onClick={() => startRename(view)}
                            >
                              <RenameIcon />
                            </button>
                            <button
                              type="button"
                              className="views__itemdelete"
                              aria-label={`Delete saved view ${view.name}`}
                              onClick={(event) => remove(view, event)}
                            >
                              <CancelIcon />
                            </button>
                          </>
                        )}
                      </div>
                      {isRenaming && renaming.error && (
                        <p className="views__renameerror" role="alert">
                          {renaming.error}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </div>
      )}
    </div>
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

/** DownloadIcon flipped: the arrow rises into the tray, for "Import". */
function UploadIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true">
      <path
        d="M8 9.5V2M8 2 5 5M8 2l3 3"
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

/** A small pencil glyph — the rename affordance on a saved-view row. */
function RenameIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true">
      <path
        d="M11.5 2.5 13.5 4.5 5 13H3v-2l8.5-8.5Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** A checkmark — confirms a rename in progress. */
function CheckIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true">
      <path
        d="M3 8.5 6.5 12 13 4.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** An "x" glyph — cancels a rename, or deletes a saved view. */
function CancelIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true">
      <path
        d="M4 4l8 8M12 4l-8 8"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

/**
 * PathBar — the floating status strip shown while path mode is on: a prompt
 * while the endpoints are still being picked, a "no path" notice if the two
 * entities aren't connected, or the hop trail (`A —supports→ B —causes→ C`)
 * once one is found. Always paired with a clear button once either endpoint
 * is set, so leaving a half-picked path is a single click away.
 */
function PathBar({
  endpoints,
  result,
  graph,
  onClear,
}: {
  endpoints: [string | null, string | null];
  result: Path | null;
  graph: Graph;
  onClear: () => void;
}) {
  const [from, to] = endpoints;
  const label = (id: string): string =>
    graph.hasNode(id) ? ((graph.getNodeAttribute(id, "label") as string) ?? id) : id;

  let body: ReactNode;
  if (from === null) {
    body = <span className="pathbar__hint">Path mode — click a node to start.</span>;
  } else if (to === null) {
    body = (
      <span className="pathbar__hint">
        From <strong>{label(from)}</strong> — click a node to finish.
      </span>
    );
  } else if (!result) {
    body = (
      <span className="pathbar__hint">
        No path between <strong>{label(from)}</strong> and <strong>{label(to)}</strong>.
      </span>
    );
  } else if (result.edges.length === 0) {
    body = <span className="pathbar__hint">Pick a different destination than {label(result.nodes[0])}.</span>;
  } else {
    body = (
      <ol className="pathbar__trail">
        {result.nodes.map((node, i) => (
          <li key={`${node}-${i}`} className="pathbar__hop">
            <span className="pathbar__node">{label(node)}</span>
            {i < result.edges.length && (() => {
              // `findPath` walks the graph as undirected, so a hop's edge may
              // run either way — render the arrow to match its real source,
              // not the direction the path happens to be traversing it.
              const edgeKey = result.edges[i];
              const forward = graph.source(edgeKey) === node;
              const rel = ((graph.getEdgeAttribute(edgeKey, "relationType") as string) ?? "related_to").replace(
                /_/g,
                " ",
              );
              const color = `var(--edge-${edgeFamily(graph.getEdgeAttribute(edgeKey, "relationType") as string)})`;
              return (
                <span className="pathbar__arrow" style={{ color }}>
                  {forward ? `—${rel}→` : `←${rel}—`}
                </span>
              );
            })()}
          </li>
        ))}
      </ol>
    );
  }

  return (
    <div className="pathbar" role="status">
      {body}
      {(from !== null || to !== null) && (
        <button type="button" className="pathbar__clear" aria-label="Clear path" onClick={onClear}>
          <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </button>
      )}
    </div>
  );
}
