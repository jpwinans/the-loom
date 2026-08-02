/**
 * Legend row selection — which entity types the key lists, and in what order.
 *
 * Pure over a type→count map so it is testable without a graph, matching the
 * `filters.ts` / `stats.ts` split the other views use.
 */
import { ENTITY_TYPES } from "../../design/palette";

export interface LegendRow {
  type: string;
  count: number;
}

/**
 * Rows for the types actually present, in `ENTITY_TYPES` (model enum) order so
 * the legend and the filter panel agree.
 *
 * Absent types are omitted: the palette defines 19, and listing all of them
 * would imply colours the reader will never encounter in this graph. Types not
 * in the palette (legacy or forward-compatible data) are appended alphabetically
 * rather than dropped, so the key never silently under-reports the canvas.
 */
export function legendRows(counts: Map<string, number>): LegendRow[] {
  const known: LegendRow[] = [];
  for (const t of ENTITY_TYPES) {
    const count = counts.get(t);
    if (count) known.push({ type: t, count });
  }
  const unknown: LegendRow[] = [...counts.entries()]
    .filter(([t, count]) => count > 0 && !(ENTITY_TYPES as readonly string[]).includes(t))
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([type, count]) => ({ type, count }));
  return [...known, ...unknown];
}

/** `leverage_point` → `leverage point`, for display. */
export function humanizeType(type: string): string {
  return type.replace(/_/g, " ");
}
