import { describe, expect, it } from "vitest";

import {
  createWrappedHoverRenderer,
  createWrappedLabelRenderer,
  FOCUS_MAX_LINES,
  NEIGHBOR_MAX_LINES,
  labelMaxWidth,
  maxLinesFor,
  revealLabel,
  wrapLines,
} from "./nodeLabels";

/** Fixed-width measurer: 10 units per character. Keeps the arithmetic obvious. */
const measure = (s: string) => s.length * 10;

describe("revealLabel", () => {
  const base = { hovered: false, selected: false, neighbor: false };

  it("hides labels on nodes with no interaction — the whole point of reveal", () => {
    expect(revealLabel("Some very long entity name", base)).toBe("");
  });

  it("shows the full name when hovered or selected", () => {
    expect(revealLabel("Cognitive Debt", { ...base, hovered: true })).toBe("Cognitive Debt");
    expect(revealLabel("Cognitive Debt", { ...base, selected: true })).toBe("Cognitive Debt");
  });

  it("shows neighbours so the local neighbourhood is readable", () => {
    expect(revealLabel("Comprehension Gate", { ...base, neighbor: true })).toBe(
      "Comprehension Gate",
    );
  });
});

describe("maxLinesFor", () => {
  const base = { hovered: false, selected: false, neighbor: false };

  it("gives the focused node more lines than its neighbours", () => {
    expect(maxLinesFor({ ...base, hovered: true })).toBe(FOCUS_MAX_LINES);
    expect(maxLinesFor({ ...base, selected: true })).toBe(FOCUS_MAX_LINES);
    expect(maxLinesFor({ ...base, neighbor: true })).toBe(NEIGHBOR_MAX_LINES);
    expect(FOCUS_MAX_LINES).toBeGreaterThan(NEIGHBOR_MAX_LINES);
  });
});

describe("wrapLines", () => {
  it("leaves a short label on one line", () => {
    expect(wrapLines("short name", 200, 4, measure)).toEqual(["short name"]);
  });

  it("wraps at the width rather than the character count", () => {
    // 70 units fits "aaa bbb" (7 chars) exactly; "aaa bbb ccc" (11) does not.
    expect(wrapLines("aaa bbb ccc", 70, 4, measure)).toEqual(["aaa bbb", "ccc"]);
    // Narrower: only one word per line.
    expect(wrapLines("aaa bbb ccc", 30, 4, measure)).toEqual(["aaa", "bbb", "ccc"]);
  });

  it("never exceeds maxLines", () => {
    const lines = wrapLines("aaa bbb ccc ddd eee fff ggg", 40, 2, measure);
    expect(lines).toHaveLength(2);
  });

  it("ellipsises when text is left over, so truncation is visible", () => {
    const lines = wrapLines("aaa bbb ccc ddd eee fff", 40, 2, measure);
    expect(lines.at(-1)?.endsWith("…")).toBe(true);
  });

  it("does not ellipsise when everything fit", () => {
    const lines = wrapLines("aaa bbb", 100, 4, measure);
    expect(lines.join("")).not.toContain("…");
  });

  it("overflows rather than breaking a single unbreakable word", () => {
    // A 20-char word against a 5-char line: emitting fragments would be worse.
    expect(wrapLines("supercalifragilistic", 50, 2, measure)).toEqual(["supercalifragilistic"]);
  });

  it("returns nothing for empty or whitespace-only text", () => {
    expect(wrapLines("", 100, 4, measure)).toEqual([]);
    expect(wrapLines("   ", 100, 4, measure)).toEqual([]);
  });

  it("returns nothing for a non-positive width or line cap", () => {
    expect(wrapLines("aaa bbb", 0, 4, measure)).toEqual([]);
    expect(wrapLines("aaa bbb", 100, 0, measure)).toEqual([]);
  });

  it("wraps a real research entity name to a readable block", () => {
    const name =
      "Counter-evidence: AI explanations raised acceptance of AI recommendations regardless of correctness";
    const lines = wrapLines(name, 300, FOCUS_MAX_LINES, measure);
    expect(lines.length).toBeGreaterThan(1);
    expect(lines.length).toBeLessThanOrEqual(FOCUS_MAX_LINES);
    for (const l of lines) expect(measure(l)).toBeLessThanOrEqual(300);
  });
});

/**
 * A stub 2D context recording what was drawn. Enough surface for both
 * renderers; `measureText` uses the same 10-units-per-char rule as above.
 */
function stubContext() {
  const calls: { text: string; x: number; y: number }[] = [];
  const rects: { x: number; y: number; w: number; h: number }[] = [];
  const ctx = {
    font: "",
    fillStyle: "",
    shadowOffsetX: 0,
    shadowOffsetY: 0,
    shadowBlur: 0,
    shadowColor: "",
    measureText: (s: string) => ({ width: s.length * 10 }),
    fillText: (text: string, x: number, y: number) => calls.push({ text, x, y }),
    beginPath: () => {},
    closePath: () => {},
    fill: () => {},
    arc: () => {},
    roundRect: (x: number, y: number, w: number, h: number) => rects.push({ x, y, w, h }),
  };
  return { ctx: ctx as unknown as CanvasRenderingContext2D, calls, rects };
}

const settings = {
  labelSize: 12,
  labelFont: "sans-serif",
  labelWeight: "400",
  labelColor: { color: "#000" },
} as unknown as Parameters<ReturnType<typeof createWrappedLabelRenderer>>[2];

const node = { key: "n1", x: 0, y: 0, size: 5, color: "#000", label: "" };

describe("createWrappedHoverRenderer", () => {
  // Regression: sigma's own drawDiscNodeHover measures the label as ONE line and
  // then calls its module-local drawDiscNodeLabel — not the configured
  // defaultDrawNodeLabel. Overriding only the label renderer left hovered nodes
  // painting a second, unwrapped label across the viewport.
  it("draws one fillText per wrapped line, not a single unwrapped label", () => {
    const { ctx, calls } = stubContext();
    const draw = createWrappedHoverRenderer(() => 100, () => 4);
    draw(ctx, { ...node, label: "aaa bbb ccc ddd" }, settings);
    expect(calls.length).toBeGreaterThan(1);
    for (const c of calls) expect(c.text.length * 10).toBeLessThanOrEqual(100);
  });

  it("sizes the background box to the wrapped block", () => {
    const { ctx, rects } = stubContext();
    const draw = createWrappedHoverRenderer(() => 100, () => 4);
    draw(ctx, { ...node, label: "aaa bbb ccc ddd" }, settings);
    expect(rects).toHaveLength(1);
    // Multi-line block: taller than a single line of 12px text.
    expect(rects[0].h).toBeGreaterThan(12);
    // Every geometry value must be finite — sigma's circle-joined box computes
    // asin(boxHeight/2/radius), which goes NaN as soon as the label wraps.
    for (const v of Object.values(rects[0])) expect(Number.isFinite(v)).toBe(true);
  });

  it("haloes the node without a box when there is no label", () => {
    const { ctx, calls, rects } = stubContext();
    const draw = createWrappedHoverRenderer(() => 100, () => 4);
    draw(ctx, { ...node, label: "" }, settings);
    expect(calls).toHaveLength(0);
    expect(rects).toHaveLength(0);
  });
});

describe("createWrappedLabelRenderer", () => {
  it("draws nothing when the label was blanked by the reveal policy", () => {
    const { ctx, calls } = stubContext();
    const draw = createWrappedLabelRenderer(() => 100, () => 4);
    draw(ctx, { ...node, label: "" }, settings);
    expect(calls).toHaveLength(0);
  });

  it("stacks wrapped lines at a constant step", () => {
    const { ctx, calls } = stubContext();
    const draw = createWrappedLabelRenderer(() => 100, () => 4);
    draw(ctx, { ...node, label: "aaa bbb ccc ddd" }, settings);
    expect(calls.length).toBeGreaterThan(1);
    const step = calls[1].y - calls[0].y;
    for (let i = 2; i < calls.length; i++) {
      expect(calls[i].y - calls[i - 1].y).toBeCloseTo(step);
    }
    // All lines share one left edge.
    for (const c of calls) expect(c.x).toBe(calls[0].x);
  });
});

describe("labelMaxWidth", () => {
  it("is a quarter of the container width", () => {
    expect(labelMaxWidth({ clientWidth: 1200 } as HTMLElement)).toBe(300);
  });

  it("is zero when there is no container", () => {
    expect(labelMaxWidth(null)).toBe(0);
  });
});
