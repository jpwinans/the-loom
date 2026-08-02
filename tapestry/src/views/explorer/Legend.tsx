/**
 * Entity-type legend — a persistent key for the node colours.
 *
 * The same swatch/label pairing lives inside the filter panel, but that is a
 * *control*: it is only on screen while the panel is open, and its rows are
 * checkboxes. Reading the canvas needs the mapping available continuously, so
 * this is the read-only key.
 *
 * Only types actually present in the graph are listed — the palette defines 19,
 * and showing absent ones would imply colours the reader will never see. Rows
 * follow `ENTITY_TYPES` (the model's enum order) so the legend and the filter
 * panel agree on ordering.
 */
import { useMemo } from "react";

import { useGraph } from "../../lib/BundleContext";
import { typeColorVar } from "../../design/palette";
import { humanizeType, legendRows } from "./legendRows";

export function Legend() {
  const graph = useGraph();

  const rows = useMemo(() => {
    const counts = new Map<string, number>();
    graph.forEachNode((_id, attr) => {
      const t = (attr.entityType as string) ?? "concept";
      counts.set(t, (counts.get(t) ?? 0) + 1);
    });
    return legendRows(counts);
  }, [graph]);

  if (rows.length === 0) return null;

  return (
    <div className="legend" aria-labelledby="legend-title">
      <h2 className="legend__title" id="legend-title">
        Entity types
      </h2>
      <ul className="legend__list">
        {rows.map((r) => (
          <li className="legend__row" key={r.type}>
            <span
              className="legend__swatch"
              style={{ background: typeColorVar(r.type) }}
              aria-hidden="true"
            />
            <span className="legend__label">{humanizeType(r.type)}</span>
            <span className="legend__count">{r.count}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
