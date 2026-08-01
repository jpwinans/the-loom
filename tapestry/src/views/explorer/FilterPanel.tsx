/**
 * FilterPanel — non-destructive facets over the weave.
 *
 * A toggle in the toolbar opens a card of entity-type checkboxes (with live
 * per-type counts), a confidence floor slider, status toggles, and relation-type
 * checkboxes. Every control writes into the store's `filters`; the Explorer's
 * reducers hide what falls out — nodes and edges are never removed, and colours
 * never repaint on a filter (dataviz: colour follows the entity, not a filter).
 *
 * Facet counts are derived from the shared graph here; the Overview (Task 14) can
 * reuse the same tallies.
 */
import { useMemo, useState } from "react";
import { useGraph } from "../../lib/BundleContext";
import { useTapestry } from "../../state/store";
import { ENTITY_TYPES, typeColorVar } from "../../design/palette";
import { edgeFamily } from "./buildGraph";

/** Fixed display order for the effective-status enum (theloom/model.py). */
const STATUS_ORDER = ["active", "investigating", "superseded", "deprecated", "retracted"];

interface Facets {
  types: Map<string, number>;
  statuses: Map<string, number>;
  relations: Map<string, number>;
}

function computeFacets(graph: ReturnType<typeof useGraph>): Facets {
  const types = new Map<string, number>();
  const statuses = new Map<string, number>();
  const relations = new Map<string, number>();
  graph.forEachNode((_id, attr) => {
    const t = (attr.entityType as string) ?? "concept";
    types.set(t, (types.get(t) ?? 0) + 1);
    const s = (attr.status as string) ?? "active";
    statuses.set(s, (statuses.get(s) ?? 0) + 1);
  });
  graph.forEachEdge((_id, attr) => {
    const r = (attr.relationType as string) ?? "related_to";
    relations.set(r, (relations.get(r) ?? 0) + 1);
  });
  return { types, statuses, relations };
}

function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

export function FilterPanel() {
  const graph = useGraph();
  const filters = useTapestry((s) => s.filters);
  const setFilters = useTapestry((s) => s.setFilters);
  const [open, setOpen] = useState(false);

  const facets = useMemo(() => computeFacets(graph), [graph]);

  // Types in enum order, statuses in fixed order, relations grouped by family —
  // always restricted to what the graph actually contains.
  const typeRows = useMemo(
    () => ENTITY_TYPES.filter((t) => facets.types.has(t)),
    [facets],
  );
  const statusRows = useMemo(() => {
    const present = [...facets.statuses.keys()];
    const ordered = STATUS_ORDER.filter((s) => facets.statuses.has(s));
    const extra = present.filter((s) => !STATUS_ORDER.includes(s)).sort();
    return [...ordered, ...extra];
  }, [facets]);
  const relationRows = useMemo(() => {
    const order = { structural: 0, epistemic: 1, causal: 2 } as const;
    return [...facets.relations.keys()].sort((a, b) => {
      const fa = order[edgeFamily(a)];
      const fb = order[edgeFamily(b)];
      return fa !== fb ? fa - fb : a.localeCompare(b);
    });
  }, [facets]);

  const activeCount =
    filters.entityTypes.length +
    filters.relationTypes.length +
    filters.statuses.length +
    (filters.confidenceMin > 0 ? 1 : 0);

  const clearAll = () =>
    setFilters({ entityTypes: [], relationTypes: [], statuses: [], confidenceMin: 0 });

  return (
    <div className="filters">
      <button
        type="button"
        className={`filters__toggle${activeCount > 0 ? " filters__toggle--on" : ""}`}
        aria-expanded={open}
        aria-controls="filter-panel"
        onClick={() => setOpen((v) => !v)}
      >
        <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
          <path
            d="M2 3.5h12M4.5 8h7M6.75 12.5h2.5"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        </svg>
        Filters
        {activeCount > 0 && <span className="filters__badge">{activeCount}</span>}
      </button>

      {open && (
        <div id="filter-panel" className="filters__panel" role="group" aria-label="Filters">
          <div className="filters__head">
            <span className="filters__eyebrow">Refine the weave</span>
            {activeCount > 0 && (
              <button type="button" className="filters__clear" onClick={clearAll}>
                Clear all
              </button>
            )}
          </div>

          {/* Confidence floor */}
          <section className="filters__group">
            <div className="filters__grouphead">
              <h3 className="filters__title">Confidence floor</h3>
              <span className="filters__value">
                {filters.confidenceMin === 0 ? "any" : filters.confidenceMin.toFixed(2)}
              </span>
            </div>
            <input
              className="filters__slider"
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={filters.confidenceMin}
              aria-label="Minimum confidence"
              onChange={(e) => setFilters({ confidenceMin: Number(e.target.value) })}
            />
            <p className="filters__hint">Entities without a score always pass.</p>
          </section>

          {/* Entity types */}
          {typeRows.length > 0 && (
            <section className="filters__group">
              <h3 className="filters__title">Entity type</h3>
              <ul className="filters__list">
                {typeRows.map((type) => {
                  const on = filters.entityTypes.includes(type);
                  return (
                    <li key={type}>
                      <label className="filters__check">
                        <input
                          type="checkbox"
                          checked={on}
                          onChange={() =>
                            setFilters({ entityTypes: toggle(filters.entityTypes, type) })
                          }
                        />
                        <span
                          className="filters__swatch"
                          style={{ background: typeColorVar(type) }}
                          aria-hidden="true"
                        />
                        <span className="filters__label">{type.replace(/_/g, " ")}</span>
                        <span className="filters__count">{facets.types.get(type)}</span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            </section>
          )}

          {/* Statuses */}
          {statusRows.length > 1 && (
            <section className="filters__group">
              <h3 className="filters__title">Status</h3>
              <div className="filters__chips">
                {statusRows.map((status) => {
                  const on = filters.statuses.includes(status);
                  return (
                    <button
                      key={status}
                      type="button"
                      className={`filters__chip${on ? " filters__chip--on" : ""}`}
                      aria-pressed={on}
                      onClick={() => setFilters({ statuses: toggle(filters.statuses, status) })}
                    >
                      {status.replace(/_/g, " ")}
                      <span className="filters__chipcount">{facets.statuses.get(status)}</span>
                    </button>
                  );
                })}
              </div>
            </section>
          )}

          {/* Relation types */}
          {relationRows.length > 0 && (
            <section className="filters__group">
              <h3 className="filters__title">Relation type</h3>
              <ul className="filters__list">
                {relationRows.map((rel) => {
                  const on = filters.relationTypes.includes(rel);
                  return (
                    <li key={rel}>
                      <label className="filters__check">
                        <input
                          type="checkbox"
                          checked={on}
                          onChange={() =>
                            setFilters({ relationTypes: toggle(filters.relationTypes, rel) })
                          }
                        />
                        <span
                          className="filters__swatch filters__swatch--edge"
                          style={{ background: `var(--edge-${edgeFamily(rel)})` }}
                          aria-hidden="true"
                        />
                        <span className="filters__label">{rel.replace(/_/g, " ")}</span>
                        <span className="filters__count">{facets.relations.get(rel)}</span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
