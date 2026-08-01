/**
 * Systems — the causal-loop view.
 *
 * A Sigma diagram over the causal-only subgraph (`buildCausalGraph`), read as a
 * systems-dynamics model rather than a knowledge graph. Three things distinguish
 * it from the Explorer:
 *
 *  1. Edges are coloured by *polarity* — a diverging channel: amplifying (`+`)
 *     influences take the positive pole, inhibiting (`−`) influences the negative
 *     pole — and every edge carries a `+`/`−` glyph at its midpoint, so polarity
 *     is never read from colour alone (CVD-safe, tracked through zoom/pan).
 *  2. The right rail lists every feedback loop; selecting one *isolates* it —
 *     the loop's variables and edges stay lit while the rest of the weave dims.
 *  3. Node fill still follows entity type; canvas colours re-resolve from the
 *     token layer on a theme change, mirroring the Explorer's rAF discipline.
 *  4. Isolating a loop and toggling "Animate flow" sends a signed pulse
 *     travelling the loop in its influence direction — a raised-cosine bump that
 *     rides the ordered edge keys, thickening one edge at a time. Reduced-motion
 *     users get the same emphasis frozen as a static directional gradient.
 *  5. A variable that carries a Meadows leverage point wears a numbered badge
 *     (its leverage level), tinted with the leverage-point token, with the point
 *     name and Meadows lever on hover — so the highest-leverage places to push
 *     the system are legible on the map itself.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type Graph from "graphology";
import Sigma from "sigma";
import { useBundle } from "../../lib/BundleContext";
import { createNodeDrag } from "../../lib/dragNodes";
import { useTapestry } from "../../state/store";
import { allVisible, exportFilename, exportPngFile, exportSvgFile } from "../../lib/exportSvg";
import { resolveTypeColor } from "../explorer/buildGraph";
import { createLayout, type LayoutController } from "../explorer/layout";
import {
  buildCausalGraph,
  flowIntensity,
  leverageTargets,
  loopEdgeKeys,
  type LoopInfo,
} from "./systems";
import { LoopPanel } from "./LoopPanel";
import "./Systems.css";

/** How long FA2 runs on mount before the causal layout freezes. */
const LAYOUT_MS = 2500;
/**
 * Label level-of-detail: above this node count, raise sigma's
 * `labelRenderedSizeThreshold` so only the largest variables keep a label at
 * scale. Gated on order so the fixture is untouched (sigma's default is 6).
 */
const LABEL_LOD_MIN_ORDER = 2000;
const LABEL_LOD_SIZE = 14;
/** One flow-pulse lap around the isolated loop. */
const FLOW_PERIOD_MS = 2500;
/** Extra edge width the pulse peak adds on top of the isolation emphasis. */
const FLOW_BOOST = 6;
/** Viewport nudge that seats a leverage badge on its node's upper-right. */
const LEVERAGE_OFFSET = 13;
const LABEL_FONT = "system-ui, sans-serif";
/** Fallbacks mirror tokens.css (light) for headless/pre-resolve contexts. */
const TEXT_FALLBACK = "#16181f";
const DIM_FALLBACK = "#767b88";
const POSITIVE_FALLBACK = "#2a6fd0";
const NEGATIVE_FALLBACK = "#d83b48";

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

/** Concrete colour for a causal edge, by its polarity pole. */
function resolvePolarityColor(polarity: string | null): string {
  return polarity === "-"
    ? readVar("--polarity-negative", NEGATIVE_FALLBACK)
    : readVar("--polarity-positive", POSITIVE_FALLBACK);
}

/** Re-derive node (by type) and edge (by polarity) colours from the tokens. */
function resolveSystemsColors(graph: Graph): void {
  graph.forEachNode((node, attr) => {
    graph.setNodeAttribute(node, "color", resolveTypeColor(attr.entityType as string));
  });
  graph.forEachEdge((edge, attr) => {
    graph.setEdgeAttribute(edge, "color", resolvePolarityColor(attr.polarity as string | null));
  });
}

/** The bundle's feedback loops, typed to the analytics shape (may be absent). */
function getLoops(bundle: ReturnType<typeof useBundle>): { loops: LoopInfo[]; hasAnalytics: boolean } {
  const analytics = bundle.analytics as { loops?: unknown[] } | undefined;
  return { loops: (analytics?.loops ?? []) as LoopInfo[], hasAnalytics: analytics != null };
}

function resolveLoop(loops: LoopInfo[], key: string | number | null): LoopInfo | null {
  if (key === null) return null;
  if (typeof key === "number") return loops[key] ?? null;
  return loops.find((loop) => loop.id === key) ?? null;
}

export function Systems() {
  const bundle = useBundle();
  const graphKey = bundle.meta.graph;
  const theme = useTapestry((s) => s.theme);
  const selectedLoop = useTapestry((s) => s.selectedLoop);
  const selectLoop = useTapestry((s) => s.selectLoop);

  const graph = useMemo(() => buildCausalGraph(bundle), [bundle]);
  const { loops, hasAnalytics } = useMemo(() => getLoops(bundle), [bundle]);
  const marks = useMemo(() => leverageTargets(bundle), [bundle]);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const glyphLayerRef = useRef<HTMLDivElement | null>(null);
  const leverageLayerRef = useRef<HTMLDivElement | null>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const layoutRef = useRef<LayoutController | null>(null);

  // Live reducer inputs — the isolated loop's members, held in refs so the render
  // loop reads current values without re-instantiating Sigma.
  const isolatedNodesRef = useRef<Set<string> | null>(null);
  const isolatedEdgesRef = useRef<Set<string> | null>(null);
  // The isolated loop's edges in path order — the flow pulse rides this array.
  const orderedLoopEdgesRef = useRef<string[]>([]);
  // Flow-animation state, read by the edge reducer each frame.
  const phaseRef = useRef(0);
  const flowActiveRef = useRef(false);
  const dimRef = useRef<string>(DIM_FALLBACK);

  const [running, setRunning] = useState(false);
  const [animating, setAnimating] = useState(false);

  // Instantiate Sigma, wire the isolation reducers + polarity glyph overlay, and
  // run the force layout once.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    resolveSystemsColors(graph);
    dimRef.current = readVar("--color-text-3", DIM_FALLBACK);

    const sigma = new Sigma(graph, container, {
      renderEdgeLabels: false,
      defaultEdgeType: "arrow",
      labelFont: LABEL_FONT,
      labelColor: { color: readVar("--color-text", TEXT_FALLBACK) },
      labelDensity: 0.7,
      ...(graph.order > LABEL_LOD_MIN_ORDER ? { labelRenderedSizeThreshold: LABEL_LOD_SIZE } : {}),
      nodeReducer: (node, data) => {
        const iso = isolatedNodesRef.current;
        if (iso && !iso.has(node)) return { ...data, color: dimRef.current, label: "", zIndex: 0 };
        return iso ? { ...data, zIndex: 1 } : data;
      },
      edgeReducer: (edge, data) => {
        const iso = isolatedEdgesRef.current;
        if (iso && !iso.has(edge)) return { ...data, color: dimRef.current, zIndex: 0 };
        if (!iso) return data;
        let size = (data.size ?? 1.5) + 1.5;
        if (flowActiveRef.current) {
          // A pulse travels the loop: the in-path edge index sets where this edge
          // sits in the raised-cosine bump, so exactly one leg peaks at a time.
          const ordered = orderedLoopEdgesRef.current;
          const idx = ordered.indexOf(edge);
          if (idx >= 0) size += flowIntensity(idx, ordered.length, phaseRef.current) * FLOW_BOOST;
        }
        return { ...data, size, zIndex: 1 };
      },
    });
    sigmaRef.current = sigma;

    // Click-hold-drag node repositioning. The polarity glyphs and leverage
    // badges follow the dragged variable for free (they reposition on
    // afterRender); the bbox freeze keeps "Animate flow" from rescaling the map
    // mid-drag as its rAF loop refreshes.
    const drag = createNodeDrag({
      sigma,
      graph,
      container,
      getLayout: () => layoutRef.current,
    });

    // Polarity glyphs: one DOM badge per edge, repositioned to the edge midpoint
    // after every render so they ride the graph through zoom/pan/layout.
    const glyphLayer = glyphLayerRef.current;
    const glyphEls = new Map<string, HTMLSpanElement>();
    if (glyphLayer) {
      glyphLayer.replaceChildren();
      graph.forEachEdge((edge, attr) => {
        const span = document.createElement("span");
        span.className = "systems__glyph";
        span.textContent = attr.polarity === "-" ? "−" : "+";
        glyphLayer.appendChild(span);
        glyphEls.set(edge, span);
      });
    }

    // Leverage badges: one numbered pill per variable that carries a Meadows
    // leverage point, seated on the node's upper-right and titled with the lever.
    const leverageLayer = leverageLayerRef.current;
    const leverageEls = new Map<string, HTMLSpanElement>();
    if (leverageLayer) {
      leverageLayer.replaceChildren();
      for (const [targetId, mark] of marks) {
        if (!graph.hasNode(targetId)) continue;
        const badge = document.createElement("span");
        badge.className = "systems__leverage";
        badge.textContent = mark.level != null ? String(mark.level) : "◆";
        const lever = mark.meadowsName ? ` — ${mark.meadowsName}` : "";
        const level = mark.level != null ? ` · Meadows level ${mark.level}${lever}` : "";
        const label = `Leverage point: ${mark.pointName}${level}`;
        badge.title = label;
        badge.setAttribute("role", "img");
        badge.setAttribute("aria-label", label);
        leverageLayer.appendChild(badge);
        leverageEls.set(targetId, badge);
      }
    }

    const updateOverlays = () => {
      const sig = sigmaRef.current;
      if (!sig) return;
      const isoEdges = isolatedEdgesRef.current;
      graph.forEachEdge((edge, attr, source, target) => {
        const el = glyphEls.get(edge);
        if (!el) return;
        const sPos = sig.getNodeDisplayData(source);
        const tPos = sig.getNodeDisplayData(target);
        if (!sPos || !tPos) {
          el.style.display = "none";
          return;
        }
        const s = sig.framedGraphToViewport(sPos);
        const t = sig.framedGraphToViewport(tPos);
        el.style.display = "";
        el.style.transform = `translate(-50%, -50%) translate(${(s.x + t.x) / 2}px, ${(s.y + t.y) / 2}px)`;
        el.style.color = resolvePolarityColor(attr.polarity as string | null);
        el.style.opacity = isoEdges != null && !isoEdges.has(edge) ? "0.12" : "1";
      });

      const isoNodes = isolatedNodesRef.current;
      for (const [targetId, el] of leverageEls) {
        const pos = sig.getNodeDisplayData(targetId);
        if (!pos) {
          el.style.display = "none";
          continue;
        }
        const p = sig.framedGraphToViewport(pos);
        el.style.display = "";
        el.style.transform = `translate(-50%, -50%) translate(${p.x + LEVERAGE_OFFSET}px, ${p.y - LEVERAGE_OFFSET}px)`;
        el.style.opacity = isoNodes != null && !isoNodes.has(targetId) ? "0.12" : "1";
      }
    };
    sigma.on("afterRender", updateOverlays);

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
      glyphEls.clear();
      glyphLayerRef.current?.replaceChildren();
      leverageEls.clear();
      leverageLayerRef.current?.replaceChildren();
    };
  }, [graph, marks]);

  // Isolation layer: fill the reducer refs from the selected loop's members, then
  // repaint. An empty selection releases everything to full strength. The ordered
  // edge keys (path direction is load-bearing) feed the flow pulse.
  useEffect(() => {
    const loop = resolveLoop(loops, selectedLoop);
    if (loop) {
      isolatedNodesRef.current = new Set(loop.memberIds);
      const ordered = loopEdgeKeys(loop, graph);
      isolatedEdgesRef.current = new Set(ordered);
      orderedLoopEdgesRef.current = ordered;
    } else {
      isolatedNodesRef.current = null;
      isolatedEdgesRef.current = null;
      orderedLoopEdgesRef.current = [];
    }
    sigmaRef.current?.refresh();
  }, [selectedLoop, loops, graph]);

  // Flow is bound to a loop: clearing the isolation also stops the pulse, so the
  // toggle never claims to animate a graph with nothing selected.
  useEffect(() => {
    if (selectedLoop === null) setAnimating(false);
  }, [selectedLoop]);

  // Flow animation: while "Animate flow" is on and a loop is isolated, advance the
  // pulse phase each frame and repaint (the edge reducer reads phaseRef). Under
  // reduced-motion the phase stays put — a static directional gradient, no rAF.
  useEffect(() => {
    const loop = resolveLoop(loops, selectedLoop);
    const active = animating && loop != null;
    flowActiveRef.current = active;
    phaseRef.current = 0;

    if (!active) {
      sigmaRef.current?.refresh();
      return;
    }
    if (prefersReducedMotion()) {
      sigmaRef.current?.refresh();
      return;
    }

    let raf = 0;
    let last = 0;
    const tick = (now: number) => {
      if (last === 0) last = now;
      phaseRef.current = (phaseRef.current + (now - last) / FLOW_PERIOD_MS) % 1;
      last = now;
      sigmaRef.current?.refresh();
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      flowActiveRef.current = false;
      phaseRef.current = 0;
      sigmaRef.current?.refresh();
    };
  }, [animating, selectedLoop, loops, graph]);

  // Re-resolve token colours when the resolved theme changes. rAF defers past
  // App's applyTheme so we read the freshly-stamped `data-theme`.
  useEffect(() => {
    let raf = 0;
    const reresolve = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        resolveSystemsColors(graph);
        dimRef.current = readVar("--color-text-3", DIM_FALLBACK);
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

  // Exports — a WYSIWYG snapshot of the causal-loop canvas. The polarity/
  // leverage overlays are DOM, not part of the Sigma canvas or the raw graph
  // attributes `graphToSvg` reads, so the SVG carries a polarity legend instead
  // and both controls' titles say so plainly (the accepted export limitation).
  const exportPng = useCallback(() => {
    const sigma = sigmaRef.current;
    if (!sigma) return;
    void exportPngFile(
      sigma,
      exportFilename(graphKey, "systems", "png"),
      readVar("--color-canvas", "#f7f6f3"),
    );
  }, [graphKey]);

  const exportSvg = useCallback(() => {
    const sigma = sigmaRef.current;
    if (!sigma) return;
    exportSvgFile(
      sigma,
      graph,
      allVisible(graph),
      {
        textColor: readVar("--color-text", TEXT_FALLBACK),
        background: readVar("--color-canvas", "#f7f6f3"),
        legend: [
          { label: "amplifies (+)", color: resolvePolarityColor("+") },
          { label: "inhibits (−)", color: resolvePolarityColor("-") },
        ],
      },
      exportFilename(graphKey, "systems", "svg"),
    );
  }, [graph, graphKey]);

  return (
    <section
      id="panel-systems"
      className="systems"
      role="tabpanel"
      aria-labelledby="tab-systems"
      tabIndex={0}
    >
      <div className="systems__canvas" ref={containerRef} />
      <div className="systems__glyphs" ref={glyphLayerRef} aria-hidden="true" />
      <div className="systems__leverages" ref={leverageLayerRef} />

      {graph.order === 0 && (
        <div className="systems__empty">
          <p className="systems__emptytitle">No causal relations in this scope.</p>
          <p className="systems__emptybody">
            The Systems view needs causal edges — causes, enables, requires, inhibits,
            amplifies, or dampens — to draw a causal-loop diagram.
          </p>
        </div>
      )}

      {graph.order > 0 && (
        <div
          className="systems__legend"
          role="img"
          aria-label={
            marks.size > 0
              ? "Legend: edge polarity and Meadows leverage points"
              : "Edge polarity legend"
          }
        >
          <span className="systems__legenditem">
            <span className="systems__legendglyph systems__legendglyph--pos" aria-hidden="true">
              +
            </span>
            amplifies
          </span>
          <span className="systems__legenditem">
            <span className="systems__legendglyph systems__legendglyph--neg" aria-hidden="true">
              −
            </span>
            inhibits
          </span>
          {marks.size > 0 && (
            <span className="systems__legenditem">
              <span className="systems__legendlev" aria-hidden="true">
                ◆
              </span>
              leverage point (Meadows level)
            </span>
          )}
        </div>
      )}

      <div className="systems__controls">
        <button
          type="button"
          className="systems__ctrl"
          aria-pressed={running}
          onClick={togglePhysics}
        >
          <span
            className={`systems__pulse${running ? " systems__pulse--live" : ""}`}
            aria-hidden="true"
          />
          {running ? "Pause layout" : "Resume layout"}
        </button>

        <button
          type="button"
          className="systems__ctrl systems__flow"
          aria-pressed={animating}
          disabled={selectedLoop === null}
          title={
            selectedLoop === null ? "Isolate a loop to animate its signed flow" : undefined
          }
          onClick={() => setAnimating((a) => !a)}
        >
          <span
            className={`systems__pulse${animating ? " systems__pulse--live" : ""}`}
            aria-hidden="true"
          />
          {animating ? "Stop flow" : "Animate flow"}
        </button>

        <button
          type="button"
          className="systems__ctrl"
          title="Export PNG. Polarity glyphs and leverage badges are on-screen overlays and are not included."
          onClick={exportPng}
        >
          <DownloadIcon />
          PNG
        </button>

        <button
          type="button"
          className="systems__ctrl"
          title="Export SVG with a polarity legend. Leverage badges are an on-screen overlay and are not included."
          onClick={exportSvg}
        >
          <DownloadIcon />
          SVG
        </button>
      </div>

      <LoopPanel
        loops={loops}
        hasAnalytics={hasAnalytics}
        selected={selectedLoop}
        onSelect={selectLoop}
      />
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
