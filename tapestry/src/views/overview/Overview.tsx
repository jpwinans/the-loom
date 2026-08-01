/**
 * Overview — the dashboard view: a read-only roll-up of the whole weave.
 *
 * Six headline tiles, then three panels — Composition (entity- and relation-type
 * distributions), Graph health (integrity + coverage), and Confidence (a ten-bin
 * histogram) — and finally the Most-central table. Every number comes from one
 * `computeOverviewStats` pass over the bundle; nothing here mutates state.
 *
 * Colour follows the same job-based system as the Explorer (dataviz): entity
 * identity reads the `--type-*` tokens, relation family the `--edge-*` tokens,
 * magnitude/rank the brand accent (as the confidence gauge already does), and
 * health state the reserved status tokens — always paired with an icon and label,
 * never colour alone. Charts are hand-rolled SVG-free bars using those tokens.
 *
 * The analytics section is optional: when it is absent the centrality, component,
 * loop, and leverage readouts degrade to an em dash or an empty-state note rather
 * than erroring. Clicking a central entity selects it and jumps to the Explorer.
 */
import { useCallback, useMemo } from "react";
import { useBundle } from "../../lib/BundleContext";
import { useTapestry } from "../../state/store";
import { ENTITY_TYPES, typeColorVar } from "../../design/palette";
import { edgeFamily } from "../explorer/buildGraph";
import { computeOverviewStats } from "./stats";
import "./Overview.css";

type Severity = "good" | "warning" | "serious" | "info";

const FAMILY_ORDER: Record<string, number> = { structural: 0, epistemic: 1, causal: 2 };

function humanize(value: string): string {
  return value.replace(/_/g, " ");
}

function typeRank(type: string): number {
  const i = (ENTITY_TYPES as readonly string[]).indexOf(type);
  return i === -1 ? ENTITY_TYPES.length : i;
}

/** A tally sorted for display: descending by count, canonical order breaking ties. */
function sortByCount(
  counts: Record<string, number>,
  tiebreak: (a: string, b: string) => number,
): [string, number][] {
  return Object.entries(counts).sort(
    ([ka, va], [kb, vb]) => vb - va || tiebreak(ka, kb),
  );
}

function CheckIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M3.5 8.5l3 3 6-7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function AlertIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M8 2.2l6 11H2z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M8 6.5v3.2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="8" cy="11.6" r="0.85" fill="currentColor" />
    </svg>
  );
}

function DotIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="2.4" fill="currentColor" />
    </svg>
  );
}

function PrintIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M4.5 6V2.5h7V6" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <rect x="2.3" y="6" width="11.4" height="5" rx="1" stroke="currentColor" strokeWidth="1.3" />
      <rect x="4.5" y="9.3" width="7" height="4.2" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}

function severityIcon(severity: Severity) {
  if (severity === "good") return <CheckIcon />;
  if (severity === "info") return <DotIcon />;
  return <AlertIcon />;
}

interface Tile {
  key: string;
  value: string;
  label: string;
  hint?: string;
}

interface HealthItem {
  key: string;
  label: string;
  count: number;
  severity: Severity;
  hint: string;
}

export function Overview() {
  const bundle = useBundle();
  const select = useTapestry((s) => s.select);
  const setView = useTapestry((s) => s.setView);

  const stats = useMemo(() => computeOverviewStats(bundle), [bundle]);

  const typeById = useMemo(() => {
    const map = new Map<string, string>();
    for (const entity of bundle.entities) {
      const id = entity.id as string | undefined;
      if (id) map.set(id, (entity.entityType as string) ?? "concept");
    }
    return map;
  }, [bundle]);

  const openInExplorer = useCallback(
    (id: string) => {
      select(id);
      setView("explorer");
    },
    [select, setView],
  );

  const analytics = bundle.analytics as
    | { components?: unknown[]; loops?: unknown[]; leveragePoints?: unknown[] }
    | undefined;
  const hasAnalytics = analytics != null;
  const count = (list: unknown[] | undefined): string =>
    list ? list.length.toLocaleString() : "—";

  const entityCount = bundle.entities.length;
  const relationCount = bundle.relations.length;
  const unscoredPct = entityCount > 0 ? Math.round((stats.unscoredCount / entityCount) * 100) : 0;

  const tiles: Tile[] = [
    { key: "entities", value: entityCount.toLocaleString(), label: "Entities" },
    { key: "relations", value: relationCount.toLocaleString(), label: "Relations" },
    { key: "components", value: count(analytics?.components), label: "Components" },
    { key: "loops", value: count(analytics?.loops), label: "Feedback loops" },
    { key: "leverage", value: count(analytics?.leveragePoints), label: "Leverage points" },
    {
      key: "unscored",
      value: `${unscoredPct}%`,
      label: "Unscored",
      hint: `${stats.unscoredCount.toLocaleString()} of ${entityCount.toLocaleString()}`,
    },
  ];

  const health: HealthItem[] = [
    {
      key: "contradictions",
      label: "Contradictions",
      count: stats.contradictionCount,
      severity: stats.contradictionCount > 0 ? "warning" : "good",
      hint: "claims in direct opposition",
    },
    {
      key: "dangling",
      label: "Dangling relations",
      count: stats.danglingRelationCount,
      severity: stats.danglingRelationCount > 0 ? "serious" : "good",
      hint: "an endpoint outside this scope",
    },
    {
      key: "unscored",
      label: "Unscored entities",
      count: stats.unscoredCount,
      severity: stats.unscoredCount > 0 ? "info" : "good",
      hint: "no confidence score yet",
    },
  ];

  const typeRows = sortByCount(stats.typeCounts, (a, b) => typeRank(a) - typeRank(b));
  const relationRows = sortByCount(
    stats.relationTypeCounts,
    (a, b) =>
      (FAMILY_ORDER[edgeFamily(a)] ?? 3) - (FAMILY_ORDER[edgeFamily(b)] ?? 3) ||
      a.localeCompare(b),
  );
  const typeMax = Math.max(1, ...typeRows.map(([, v]) => v));
  const relationMax = Math.max(1, ...relationRows.map(([, v]) => v));

  const histMax = Math.max(1, ...stats.confidenceHistogram);
  const centralMax = stats.topCentral[0]?.score ?? 1;

  return (
    <section
      id="panel-overview"
      className="overview"
      role="tabpanel"
      aria-labelledby="tab-overview"
      tabIndex={0}
    >
      <div className="overview__inner">
        <header className="overview__head">
          <div className="overview__headtext">
            <h1 className="overview__title">Overview</h1>
            <p className="overview__lede">
              The shape of {bundle.meta.title ?? bundle.meta.graph} at a glance — its
              composition, health, and most connected ideas.
            </p>
          </div>
          <button
            type="button"
            className="overview__print"
            title="Print or save as PDF — the dashboard's WYSIWYG export"
            onClick={() => window.print()}
          >
            <PrintIcon />
            Print
          </button>
        </header>

        <div className="overview__tiles">
          {tiles.map((tile) => (
            <div key={tile.key} className="ov-tile">
              <span className="ov-tile__value">{tile.value}</span>
              <span className="ov-tile__label">{tile.label}</span>
              {tile.hint && <span className="ov-tile__hint">{tile.hint}</span>}
            </div>
          ))}
        </div>

        <div className="overview__panels">
          <section className="ov-card" aria-labelledby="ov-composition">
            <h2 id="ov-composition" className="ov-card__title">
              Composition
            </h2>

            <div className="ov-card__block">
              <p className="ov-card__eyebrow">Entity types</p>
              {typeRows.length === 0 ? (
                <p className="ov-empty">No entities in this scope.</p>
              ) : (
                <ul className="ov-bars">
                  {typeRows.map(([type, value]) => (
                    <li key={type} className="ov-bar">
                      <span className="ov-bar__key">
                        <span
                          className="ov-bar__swatch"
                          style={{ background: typeColorVar(type) }}
                          aria-hidden="true"
                        />
                        <span className="ov-bar__name">{humanize(type)}</span>
                      </span>
                      <span className="ov-bar__track">
                        <span
                          className="ov-bar__fill"
                          style={{ width: `${(value / typeMax) * 100}%`, background: typeColorVar(type) }}
                        />
                      </span>
                      <span className="ov-bar__value">{value.toLocaleString()}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="ov-card__block">
              <p className="ov-card__eyebrow">Relation types</p>
              {relationRows.length === 0 ? (
                <p className="ov-empty">No relations in this scope.</p>
              ) : (
                <>
                  <ul className="ov-bars">
                    {relationRows.map(([rel, value]) => (
                      <li key={rel} className="ov-bar">
                        <span className="ov-bar__key">
                          <span
                            className="ov-bar__swatch ov-bar__swatch--edge"
                            style={{ background: `var(--edge-${edgeFamily(rel)})` }}
                            aria-hidden="true"
                          />
                          <span className="ov-bar__name">{humanize(rel)}</span>
                        </span>
                        <span className="ov-bar__track">
                          <span
                            className="ov-bar__fill"
                            style={{
                              width: `${(value / relationMax) * 100}%`,
                              background: `var(--edge-${edgeFamily(rel)})`,
                            }}
                          />
                        </span>
                        <span className="ov-bar__value">{value.toLocaleString()}</span>
                      </li>
                    ))}
                  </ul>
                  <ul className="ov-legend" aria-label="Relation families">
                    {(["structural", "epistemic", "causal"] as const).map((family) => (
                      <li key={family} className="ov-legend__item">
                        <span
                          className="ov-legend__swatch"
                          style={{ background: `var(--edge-${family})` }}
                          aria-hidden="true"
                        />
                        {family}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          </section>

          <div className="overview__side">
            <section className="ov-card" aria-labelledby="ov-health">
              <h2 id="ov-health" className="ov-card__title">
                Graph health
              </h2>
              <ul className="ov-health">
                {health.map((item) => (
                  <li key={item.key} className={`ov-health__row ov-health__row--${item.severity}`}>
                    <span className="ov-health__icon" aria-hidden="true">
                      {severityIcon(item.severity)}
                    </span>
                    <span className="ov-health__text">
                      <span className="ov-health__label">{item.label}</span>
                      <span className="ov-health__hint">{item.hint}</span>
                    </span>
                    <span className="ov-health__count">{item.count.toLocaleString()}</span>
                  </li>
                ))}
              </ul>
            </section>

            <section className="ov-card" aria-labelledby="ov-confidence">
              <h2 id="ov-confidence" className="ov-card__title">
                Confidence
              </h2>
              {stats.scoredCount === 0 ? (
                <p className="ov-empty">No entities carry a confidence score yet.</p>
              ) : (
                <>
                  <div
                    className="ov-hist"
                    role="img"
                    aria-label={`Confidence distribution across ${stats.scoredCount} scored entities`}
                  >
                    {stats.confidenceHistogram.map((n, i) => {
                      const lo = (i / 10).toFixed(1);
                      const hi = ((i + 1) / 10).toFixed(1);
                      return (
                        <div key={i} className="ov-hist__bin" title={`${n} in ${lo}–${hi}`}>
                          <span className="ov-hist__bar" style={{ height: `${(n / histMax) * 100}%` }} />
                        </div>
                      );
                    })}
                  </div>
                  <div className="ov-hist__axis" aria-hidden="true">
                    <span>0</span>
                    <span>0.5</span>
                    <span>1</span>
                  </div>
                  <p className="ov-hist__foot">
                    {stats.scoredCount.toLocaleString()} scored · {stats.unscoredCount.toLocaleString()} unscored
                  </p>
                </>
              )}
            </section>
          </div>
        </div>

        <section className="ov-card ov-card--wide" aria-labelledby="ov-central">
          <h2 id="ov-central" className="ov-card__title">
            Most central
          </h2>
          <p className="ov-card__eyebrow">Top entities by PageRank — select one to open it in the Explorer</p>
          {stats.topCentral.length === 0 ? (
            <p className="ov-empty">
              {hasAnalytics
                ? "No entities to rank yet."
                : "Re-export with the analytics section to rank entities by centrality."}
            </p>
          ) : (
            <ol className="ov-central">
              {stats.topCentral.map((node, i) => {
                const type = typeById.get(node.id) ?? "concept";
                return (
                  <li key={node.id}>
                    <button
                      type="button"
                      className="ov-central__row"
                      onClick={() => openInExplorer(node.id)}
                    >
                      <span className="ov-central__rank">{i + 1}</span>
                      <span className="ov-central__ident">
                        <span
                          className="ov-central__dot"
                          style={{ background: typeColorVar(type) }}
                          aria-hidden="true"
                        />
                        <span className="ov-central__name">{node.name}</span>
                        <span className="ov-central__type">{humanize(type)}</span>
                      </span>
                      <span className="ov-central__track">
                        <span
                          className="ov-central__fill"
                          style={{ width: `${(node.score / centralMax) * 100}%` }}
                        />
                      </span>
                      <span className="ov-central__score">{node.score.toFixed(3)}</span>
                    </button>
                  </li>
                );
              })}
            </ol>
          )}
        </section>
      </div>
    </section>
  );
}
