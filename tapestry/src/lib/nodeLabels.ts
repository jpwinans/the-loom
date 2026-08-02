/**
 * Node label rendering shared by the four sigma views.
 *
 * Two concerns, deliberately separable so each is testable on its own:
 *
 * 1. **Reveal policy** (`revealLabel`) — which nodes carry a label at all.
 *    Sigma's own level-of-detail only engages past `LABEL_LOD_MIN_ORDER` nodes,
 *    so at ordinary graph sizes every node labelled itself at full length. On a
 *    graph whose entity names are sentences — the deep-research graphs run to a
 *    median of ~110 characters — that buries the topology under text. Labels are
 *    now revealed by interaction: the focused node and its immediate
 *    neighbourhood, nothing else.
 *
 * 2. **Wrapping** (`createWrappedLabelRenderer`) — sigma draws labels with a
 *    single `fillText`, so a long name runs off the viewport. This wraps to a
 *    fraction of the container width and caps the line count, ellipsising what
 *    doesn't fit.
 *
 * The renderer takes the max width as a callback rather than reading
 * `context.canvas.width`, because that canvas is sized in device pixels while
 * the drawing context works in CSS pixels — deriving one from the other means
 * guessing at the device pixel ratio. The views know their container width, so
 * they supply it.
 */
import type { Settings } from "sigma/settings";
import type { NodeDisplayData, PartialButFor } from "sigma/types";

/** Fraction of the container width a wrapped label may occupy. */
export const LABEL_WIDTH_RATIO = 0.25;
/** Lines allowed for the focused node (hovered or selected). */
export const FOCUS_MAX_LINES = 4;
/** Lines allowed for its neighbours — kept tighter so focus stays legible. */
export const NEIGHBOR_MAX_LINES = 2;
/** Vertical distance between wrapped lines, as a multiple of font size. */
const LINE_HEIGHT = 1.25;

export type LabelState = {
  hovered: boolean;
  selected: boolean;
  neighbor: boolean;
};

/**
 * The label a node should carry. `""` hides it. Callers apply this first, so
 * their own dimming/brushing/path-mode branches can still blank it afterwards.
 */
export function revealLabel(name: string, state: LabelState): string {
  if (state.hovered || state.selected) return name;
  if (state.neighbor) return name;
  return "";
}

/** How many lines this node is allowed, given its interaction state. */
export function maxLinesFor(state: LabelState): number {
  return state.hovered || state.selected ? FOCUS_MAX_LINES : NEIGHBOR_MAX_LINES;
}

/**
 * Greedy word wrap against a measuring function, capped at `maxLines`. The last
 * line is ellipsised when text remains. A single word longer than `maxWidth` is
 * not broken mid-word — it overflows rather than producing unreadable fragments.
 *
 * `measure` is injected so this is testable without a canvas.
 */
export function wrapLines(
  text: string,
  maxWidth: number,
  maxLines: number,
  measure: (s: string) => number,
): string[] {
  const words = text.split(/\s+/).filter(Boolean);
  if (words.length === 0) return [];
  if (maxLines < 1 || maxWidth <= 0) return [];

  const lines: string[] = [];
  let current = "";

  for (let i = 0; i < words.length; i++) {
    const candidate = current ? `${current} ${words[i]}` : words[i];
    if (measure(candidate) <= maxWidth || !current) {
      current = candidate;
      continue;
    }
    lines.push(current);
    current = words[i];
    if (lines.length === maxLines) break;
  }

  if (lines.length < maxLines && current) lines.push(current);

  // Anything left over? Ellipsise the final line so the truncation is visible.
  const consumed = lines.join(" ").split(/\s+/).filter(Boolean).length;
  if (consumed < words.length && lines.length > 0) {
    const last = lines.length - 1;
    let line = lines[last];
    while (line.length > 1 && measure(`${line}…`) > maxWidth) {
      line = line.slice(0, -1).trimEnd();
    }
    lines[last] = `${line}…`;
  }
  return lines;
}

type LabelData = PartialButFor<NodeDisplayData, "x" | "y" | "size" | "label" | "color">;

/** Padding around the hover background box, matching sigma's own. */
const BOX_PADDING = 2;

/**
 * Geometry of a wrapped label block. `x`/`baselineTop` are where text is drawn;
 * the box fields describe the background rectangle that would sit behind it.
 * Both renderers derive from this so the box always matches the text.
 */
function blockGeometry(data: LabelData, size: number, lines: string[], widest: number) {
  const step = size * LINE_HEIGHT;
  const x = data.x + data.size + 3;
  // Centre the block on the node, collapsing to sigma's single-line anchor
  // (y + size/3) when there is exactly one line.
  const baselineTop = data.y + size / 3 - ((lines.length - 1) * step) / 2;
  return {
    x,
    step,
    baselineTop,
    boxX: x - BOX_PADDING,
    boxY: baselineTop - size * 0.8 - BOX_PADDING,
    boxWidth: widest + BOX_PADDING * 2 + 3,
    boxHeight: (lines.length - 1) * step + size * 1.05 + BOX_PADDING * 2,
  };
}

/** Wrap `data.label`, returning the lines and the widest line's width. */
function layout(
  context: CanvasRenderingContext2D,
  data: LabelData,
  settings: Settings,
  maxWidth: number,
  maxLines: number,
): { lines: string[]; widest: number } {
  context.font = `${settings.labelWeight} ${settings.labelSize}px ${settings.labelFont}`;
  const lines = wrapLines(String(data.label ?? ""), maxWidth, maxLines, (s) =>
    context.measureText(s).width,
  );
  let widest = 0;
  for (const l of lines) widest = Math.max(widest, context.measureText(l).width);
  return { lines, widest };
}

function labelColor(data: LabelData, settings: Settings): string {
  const attr = settings.labelColor.attribute;
  if (!attr) return settings.labelColor.color ?? "#000";
  return (
    ((data as Record<string, unknown>)[attr] as string | undefined) ??
    settings.labelColor.color ??
    "#000"
  );
}

/**
 * A sigma `defaultDrawNodeLabel` replacement that wraps. Mirrors sigma's own
 * `drawDiscNodeLabel` for font, colour and anchor; the only difference is
 * multi-line output.
 *
 * `getMaxWidth` is called per draw so the wrap width tracks container resizes.
 * `getMaxLines` lets the caller vary the cap by interaction state.
 */
export function createWrappedLabelRenderer(
  getMaxWidth: () => number,
  getMaxLines: (node: string) => number = () => FOCUS_MAX_LINES,
) {
  return function drawWrappedNodeLabel(
    context: CanvasRenderingContext2D,
    data: LabelData,
    settings: Settings,
  ): void {
    if (!data.label) return;
    const { lines, widest } = layout(
      context,
      data,
      settings,
      getMaxWidth(),
      getMaxLines(data.key ?? ""),
    );
    if (lines.length === 0) return;

    const g = blockGeometry(data, settings.labelSize, lines, widest);
    context.fillStyle = labelColor(data, settings);
    for (let i = 0; i < lines.length; i++) {
      context.fillText(lines[i], g.x, g.baselineTop + i * g.step);
    }
  };
}

/**
 * A sigma `defaultDrawNodeHover` replacement.
 *
 * This override is **required**, not optional: sigma's `drawDiscNodeHover`
 * measures the label as a single line, sizes its background box from that, and
 * then calls its own module-local `drawDiscNodeLabel` — not whatever
 * `defaultDrawNodeLabel` was configured. Overriding only the label renderer
 * therefore leaves hovered and highlighted nodes drawing a second, unwrapped
 * label on top of the wrapped one.
 *
 * The background is drawn as a plain rounded rect rather than sigma's
 * circle-joined shape: that shape derives an angle from
 * `asin(boxHeight / 2 / radius)`, which is NaN once the box is taller than the
 * node's radius — guaranteed as soon as a label wraps to two lines.
 */
export function createWrappedHoverRenderer(
  getMaxWidth: () => number,
  getMaxLines: (node: string) => number = () => FOCUS_MAX_LINES,
) {
  return function drawWrappedNodeHover(
    context: CanvasRenderingContext2D,
    data: LabelData,
    settings: Settings,
  ): void {
    const { lines, widest } = layout(
      context,
      data,
      settings,
      getMaxWidth(),
      getMaxLines(data.key ?? ""),
    );

    context.shadowOffsetX = 0;
    context.shadowOffsetY = 0;
    context.shadowBlur = 8;
    context.shadowColor = "#000";
    context.fillStyle = "#FFF";

    if (lines.length === 0) {
      // No label to frame — just halo the node, as sigma does.
      context.beginPath();
      context.arc(data.x, data.y, data.size + BOX_PADDING, 0, Math.PI * 2);
      context.closePath();
      context.fill();
      context.shadowBlur = 0;
      return;
    }

    const g = blockGeometry(data, settings.labelSize, lines, widest);
    const r = Math.min(4, g.boxHeight / 2);
    context.beginPath();
    context.roundRect(g.boxX, g.boxY, g.boxWidth, g.boxHeight, r);
    context.closePath();
    context.fill();
    context.shadowBlur = 0;

    context.fillStyle = labelColor(data, settings);
    for (let i = 0; i < lines.length; i++) {
      context.fillText(lines[i], g.x, g.baselineTop + i * g.step);
    }
  };
}

/** Wrap width for a container, in CSS pixels. */
export function labelMaxWidth(container: HTMLElement | null): number {
  const w = container?.clientWidth ?? 0;
  return w * LABEL_WIDTH_RATIO;
}
