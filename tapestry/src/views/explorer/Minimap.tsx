/**
 * Minimap — a small always-on overview of the weave: every node as a dot, plus
 * a rectangle tracking the camera's current viewport. Click (or drag with the
 * primary button) to recentre the camera there.
 *
 * Cheap by construction: rather than run its own render/animation loop, it
 * repaints once per Sigma repaint by listening to `afterRender` — which
 * already fires on layout ticks, camera moves, and the `sigma.refresh()` calls
 * Explorer's theme effect makes after re-resolving CSS vars, so a single
 * listener keeps the minimap in sync with all three without extra plumbing.
 * Colour reads (`readVar`) happen fresh on every draw, so no separate
 * theme-change listener is needed either.
 */
import { useCallback, useEffect, useRef, type MouseEvent as ReactMouseEvent } from "react";
import type Graph from "graphology";
import type Sigma from "sigma";

const WIDTH = 160;
const HEIGHT = 116;
const PAD = 8;

const SURFACE_FALLBACK = "#eceef3";
const NODE_FALLBACK = "#767b88";
const VIEWPORT_FALLBACK = "#4a44c4";

interface Bounds {
  minX: number;
  minY: number;
  maxY: number;
  scale: number;
}

function readVar(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

export function Minimap({ sigma, graph }: { sigma: Sigma; graph: Graph }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const boundsRef = useRef<Bounds | null>(null);

  const draw = useCallback(() => {
    const ctx = canvasRef.current?.getContext("2d");
    if (!ctx) return;

    ctx.clearRect(0, 0, WIDTH, HEIGHT);

    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    graph.forEachNode((_id, attr) => {
      const x = attr.x as number;
      const y = attr.y as number;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    });
    if (!Number.isFinite(minX)) {
      boundsRef.current = null;
      return;
    }

    const scale = Math.min((WIDTH - PAD * 2) / Math.max(maxX - minX, 1e-6), (HEIGHT - PAD * 2) / Math.max(maxY - minY, 1e-6));
    const bounds: Bounds = { minX, minY, maxY, scale };
    boundsRef.current = bounds;
    const project = (x: number, y: number) => ({
      cx: PAD + (x - bounds.minX) * scale,
      cy: PAD + (bounds.maxY - y) * scale, // graph y is up; canvas y is down
    });

    ctx.fillStyle = readVar("--color-surface-2", SURFACE_FALLBACK);
    ctx.fillRect(0, 0, WIDTH, HEIGHT);

    ctx.fillStyle = readVar("--color-text-3", NODE_FALLBACK);
    graph.forEachNode((_id, attr) => {
      const { cx, cy } = project(attr.x as number, attr.y as number);
      ctx.fillRect(cx - 0.75, cy - 0.75, 1.5, 1.5);
    });

    const dims = sigma.getDimensions();
    const corners = [
      { x: 0, y: 0 },
      { x: dims.width, y: 0 },
      { x: dims.width, y: dims.height },
      { x: 0, y: dims.height },
    ]
      .map((p) => sigma.viewportToGraph(p))
      .map((p) => project(p.x, p.y));

    ctx.strokeStyle = readVar("--color-accent", VIEWPORT_FALLBACK);
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    corners.forEach((c, i) => (i === 0 ? ctx.moveTo(c.cx, c.cy) : ctx.lineTo(c.cx, c.cy)));
    ctx.closePath();
    ctx.stroke();
  }, [graph, sigma]);

  useEffect(() => {
    draw();
    sigma.on("afterRender", draw);
    return () => {
      sigma.off("afterRender", draw);
    };
  }, [sigma, draw]);

  const panTo = useCallback(
    (event: ReactMouseEvent<HTMLCanvasElement>) => {
      const bounds = boundsRef.current;
      const canvas = canvasRef.current;
      if (!bounds || !canvas) return;
      const rect = canvas.getBoundingClientRect();
      const px = ((event.clientX - rect.left) / rect.width) * WIDTH;
      const py = ((event.clientY - rect.top) / rect.height) * HEIGHT;
      const x = bounds.minX + (px - PAD) / bounds.scale;
      const y = bounds.maxY - (py - PAD) / bounds.scale;
      void sigma.getCamera().animate({ x, y }, { duration: 300 });
    },
    [sigma],
  );

  return (
    <div className="minimap">
      <canvas
        ref={canvasRef}
        className="minimap__canvas"
        width={WIDTH}
        height={HEIGHT}
        role="img"
        aria-label="Minimap — click to pan the camera"
        onClick={panTo}
        onMouseMove={(event) => {
          if (event.buttons === 1) panTo(event);
        }}
      />
    </div>
  );
}
