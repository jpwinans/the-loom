/**
 * exportSvg — WYSIWYG export for every Sigma view (Explorer, Systems,
 * Chronicle, Semantic Map).
 *
 * `graphToSvg` is pure and sigma-free: it takes a graphology model, a
 * visibility set (the Explorer's FILTER layer, a view's replay-projected
 * state, or `allVisible` for a view with no filter concept), and an explicit
 * viewport, and serializes only what's actually on screen — same node fills,
 * same edge tints, same positions, plus an optional swatch+label `legend` —
 * as a self-contained SVG string. Colours are read from the graph's own
 * attributes (already resolved by each view's colour-resolve pass), never
 * from CSS custom properties here, so the function has no DOM dependency and
 * stays unit-testable with a plain graphology graph.
 *
 * `exportSvgFile` / `exportPngFile` are the impure edges: they pull the live
 * viewport off a Sigma instance (or the raw layered canvases, for PNG) and
 * trigger a browser download. PNG export draws every canvas layer Sigma
 * maintains (WebGL edges/nodes, then the 2D label/hover overlays) onto one
 * off-screen canvas in paint order — optionally pre-filled with a theme
 * background — so the exported bitmap matches what's rendered on screen
 * pixel-for-pixel. `exportFilename` gives every view's export the same
 * `<graph>-<view>-<date>` name.
 *
 * Known, accepted limitation (stated in each export control's title text):
 * DOM-overlay decorations — the Systems polarity glyphs and leverage badges,
 * the Chronicle status/diff badges, the Semantic Map hull outlines — are
 * React-rendered HTML/SVG layers positioned over the Sigma canvas, not part
 * of it, so they are not captured by either export path. The `legend` option
 * exists so the exported image still documents what those overlays would
 * have shown.
 */
import type Graph from "graphology";
import type Sigma from "sigma";
import type { Visibility } from "../views/explorer/filters";

export interface ExportViewport {
  x: number;
  y: number;
  ratio: number;
}

/** One legend row: a colour swatch paired with its label — never colour-alone. */
export interface LegendEntry {
  label: string;
  color: string;
}

export interface SvgExportOptions {
  /** Resolved `--color-text` value — labels are drawn in this ink. */
  textColor?: string;
  /** Resolved `--color-canvas` value — the SVG's background rect. */
  background?: string;
  /** Graph-space margin added around the visible elements' bounding box. */
  padding?: number;
  /** Swatch + label rows drawn in the SVG's top-left corner, when present. */
  legend?: LegendEntry[];
}

const DEFAULT_TEXT_COLOR = "#16181f";
const DEFAULT_BACKGROUND = "#f7f6f3";
const DEFAULT_PADDING = 40;
const LABEL_FONT = "system-ui, sans-serif";
const LEGEND_SWATCH = 10;
const LEGEND_ROW_HEIGHT = 18;
const LEGEND_FONT_SIZE = 11;
const LEGEND_INSET = 12;

interface ProjectedNode {
  x: number;
  y: number;
  size: number;
  color: string;
  label: string;
}

interface ProjectedEdge {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  color: string;
  size: number;
}

/** Graph space → SVG space: centred on the viewport, scaled by its ratio. */
function project(gx: number, gy: number, viewport: ExportViewport): { x: number; y: number } {
  const ratio = viewport.ratio || 1;
  return { x: (gx - viewport.x) / ratio, y: (gy - viewport.y) / ratio };
}

function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Swatch + label rows, anchored to the SVG's top-left corner (`minX`/`minY` are
 * already in the same unit as the drawn marks — the viewBox is 1:1 with the
 * output `width`/`height`). Each row pairs a colour with its name so the export
 * documents any channel a DOM overlay would otherwise have carried on-screen
 * (polarity, status, cluster region, …) but that the flattened PNG or the
 * marks-only SVG cannot.
 */
function renderLegend(legend: LegendEntry[], minX: number, minY: number, textColor: string): string {
  if (legend.length === 0) return "";
  const x = minX + LEGEND_INSET;
  const rows = legend
    .map((entry, i) => {
      const y = minY + LEGEND_INSET + 6 + i * LEGEND_ROW_HEIGHT;
      return (
        `<rect x="${x.toFixed(2)}" y="${(y - LEGEND_SWATCH / 2).toFixed(2)}" width="${LEGEND_SWATCH}" height="${LEGEND_SWATCH}" rx="2" fill="${entry.color}" />` +
        `<text x="${(x + LEGEND_SWATCH + 6).toFixed(2)}" y="${(y + 4).toFixed(2)}" font-family="${LABEL_FONT}" font-size="${LEGEND_FONT_SIZE}" fill="${textColor}">${escapeXml(entry.label)}</text>`
      );
    })
    .join("");
  return `<g class="export-legend">${rows}</g>`;
}

/**
 * Serialize the visible subset of `graph` to a standalone SVG string. Only
 * nodes in `visible.visibleNodes` are drawn; an edge is drawn only when it is
 * in `visible.visibleEdges` (the FILTER layer already requires both endpoints
 * to be visible for an edge to qualify, but this re-checks defensively).
 */
export function graphToSvg(
  graph: Graph,
  visible: Visibility,
  viewport: ExportViewport,
  options: SvgExportOptions = {},
): string {
  const textColor = options.textColor ?? DEFAULT_TEXT_COLOR;
  const background = options.background ?? DEFAULT_BACKGROUND;
  const padding = options.padding ?? DEFAULT_PADDING;

  const nodes: ProjectedNode[] = [];
  graph.forEachNode((node, attr) => {
    if (!visible.visibleNodes.has(node)) return;
    const p = project((attr.x as number) ?? 0, (attr.y as number) ?? 0, viewport);
    nodes.push({
      x: p.x,
      y: p.y,
      size: (attr.size as number) ?? 3,
      color: (attr.color as string) ?? textColor,
      label: (attr.label as string) ?? node,
    });
  });

  const edges: ProjectedEdge[] = [];
  graph.forEachEdge((edge, attr, source, target) => {
    if (!visible.visibleEdges.has(edge)) return;
    if (!visible.visibleNodes.has(source) || !visible.visibleNodes.has(target)) return;
    const sp = project(
      (graph.getNodeAttribute(source, "x") as number) ?? 0,
      (graph.getNodeAttribute(source, "y") as number) ?? 0,
      viewport,
    );
    const tp = project(
      (graph.getNodeAttribute(target, "x") as number) ?? 0,
      (graph.getNodeAttribute(target, "y") as number) ?? 0,
      viewport,
    );
    edges.push({
      x1: sp.x,
      y1: sp.y,
      x2: tp.x,
      y2: tp.y,
      color: (attr.color as string) ?? "#9096a3",
      size: (attr.size as number) ?? 1,
    });
  });

  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const n of nodes) {
    minX = Math.min(minX, n.x - n.size);
    minY = Math.min(minY, n.y - n.size);
    maxX = Math.max(maxX, n.x + n.size);
    maxY = Math.max(maxY, n.y + n.size);
  }
  if (!Number.isFinite(minX)) {
    minX = 0;
    minY = 0;
    maxX = 0;
    maxY = 0;
  }
  minX -= padding;
  minY -= padding;
  maxX += padding;
  maxY += padding;
  const width = Math.max(1, maxX - minX);
  const height = Math.max(1, maxY - minY);

  const edgeEls = edges
    .map(
      (e) =>
        `<line x1="${e.x1.toFixed(2)}" y1="${e.y1.toFixed(2)}" x2="${e.x2.toFixed(2)}" y2="${e.y2.toFixed(2)}" ` +
        `stroke="${e.color}" stroke-width="${e.size}" stroke-linecap="round" />`,
    )
    .join("");

  const nodeEls = nodes
    .map(
      (n) =>
        `<circle cx="${n.x.toFixed(2)}" cy="${n.y.toFixed(2)}" r="${n.size.toFixed(2)}" fill="${n.color}" />` +
        `<text x="${(n.x + n.size + 3).toFixed(2)}" y="${(n.y + 3.5).toFixed(2)}" font-family="${LABEL_FONT}" ` +
        `font-size="10" fill="${textColor}">${escapeXml(n.label)}</text>`,
    )
    .join("");

  const legendEls = renderLegend(options.legend ?? [], minX, minY, textColor);

  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${minX.toFixed(2)} ${minY.toFixed(2)} ${width.toFixed(2)} ${height.toFixed(2)}" ` +
    `width="${Math.round(width)}" height="${Math.round(height)}">` +
    `<rect x="${minX.toFixed(2)}" y="${minY.toFixed(2)}" width="${width.toFixed(2)}" height="${height.toFixed(2)}" fill="${background}" />` +
    `<g>${edgeEls}</g><g>${nodeEls}</g>${legendEls}` +
    `</svg>`
  );
}

/**
 * The visibility set for a Sigma view with no FILTER layer (Systems, Semantic):
 * every node and edge currently in the graphology model. Mirrors the shape
 * `applyFilters` returns so `graphToSvg`/`exportSvgFile` take the same argument
 * regardless of which view supplies it.
 */
export function allVisible(graph: Graph): Visibility {
  return { visibleNodes: new Set(graph.nodes()), visibleEdges: new Set(graph.edges()) };
}

/**
 * `<graph>-<view>-<date>` — the export filename convention every view's PNG/SVG
 * (and the saved-views JSON export) shares, so a download always identifies its
 * source graph, which view produced it, and the day it was captured.
 */
export function exportFilename(graph: string, view: string, ext: string): string {
  const date = new Date().toISOString().slice(0, 10);
  return `${graph}-${view}-${date}.${ext}`;
}

// ---- Download plumbing -----------------------------------------------------

/** Create an `<a download>` for `blob`, click it, then revoke the object URL. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** Build the SVG for the current sigma viewport and trigger a download. */
export function exportSvgFile(
  sigma: Sigma,
  graph: Graph,
  visible: Visibility,
  options: SvgExportOptions,
  filename: string,
): void {
  const svg = graphToSvg(graph, visible, sigma.getCamera().getState(), options);
  downloadBlob(new Blob([svg], { type: "image/svg+xml" }), filename);
}

/** Paint order for sigma's layered canvases — WebGL marks under 2D overlays. */
const PNG_LAYER_ORDER = ["edges", "nodes", "edgeLabels", "labels", "hovers"];

/**
 * Flatten every canvas Sigma maintains onto one off-screen canvas, in paint
 * order, and trigger a PNG download. Sigma v3 exposes the layers via
 * `getCanvases()` (a `{[layerId]: HTMLCanvasElement}` record) — see
 * `sigma.d.ts`. The interaction-only "mouse" layer is intentionally skipped;
 * it carries no visible marks.
 *
 * Sigma's WebGL contexts (the "edges" and "nodes" layers) are created with
 * `preserveDrawingBuffer: false` and are not overridable from the public API
 * — the browser is free to clear them any time after compositing, which in
 * practice means their contents are usually already gone by the time a user
 * clicks an export button well after the layout settled. `sigma.refresh()`
 * with no arguments renders synchronously (its `schedule` option defaults to
 * false), so calling it immediately before reading the canvases repaints the
 * WebGL buffers within this same synchronous call — before the browser gets
 * a chance to composite and clear them — making the snapshot reliable.
 *
 * `background`, when given, is filled onto the output canvas before the
 * layers are drawn — Sigma's own canvases are transparent, so without it a
 * PNG opened outside the app (dark chrome, image viewer, a slide) would show
 * through to whatever sits behind it instead of the theme's own canvas tint.
 */
export function exportPngFile(sigma: Sigma, filename: string, background?: string): Promise<void> {
  sigma.refresh();
  const canvases = sigma.getCanvases();
  const layers = PNG_LAYER_ORDER.map((id) => canvases[id]).filter(
    (c): c is HTMLCanvasElement => c != null,
  );
  if (layers.length === 0) return Promise.resolve();

  const width = layers[0].width;
  const height = layers[0].height;
  const out = document.createElement("canvas");
  out.width = width;
  out.height = height;
  const ctx = out.getContext("2d");
  if (!ctx) return Promise.resolve();
  if (background) {
    ctx.fillStyle = background;
    ctx.fillRect(0, 0, width, height);
  }
  for (const layer of layers) {
    ctx.drawImage(layer, 0, 0, width, height);
  }

  return new Promise<void>((resolve) => {
    out.toBlob((blob) => {
      if (blob) downloadBlob(blob, filename);
      resolve();
    }, "image/png");
  });
}
