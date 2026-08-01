/**
 * DetailPanel — the full document behind the selected node.
 *
 * Reads the raw entity from the bundle (the graph carries only visual channels)
 * and lays out its name, type, observations, confidence, provenance, status, and
 * a clickable list of its relations. Every optional field is guarded — an entity
 * may have no confidence, no provenance, or no observations. Choosing a connected
 * entity hands its id to `onNavigate`, which selects it and pans the camera.
 */
import { useMemo } from "react";
import { useBundle, useGraph } from "../../lib/BundleContext";
import { useTapestry } from "../../state/store";
import { typeColorVar } from "../../design/palette";
import { edgeFamily } from "./buildGraph";

interface Confidence {
  score?: number;
  basis?: string;
  lastEvaluated?: string;
}

interface Provenance {
  sourceType?: string | null;
  sourceId?: string | null;
  externalRef?: string | null;
  extractionDate?: string | null;
  extractor?: string | null;
  extractionMethod?: string | null;
}

type EntityDoc = Record<string, unknown>;

/** Connection derived from an incident edge: the relation and the other endpoint. */
interface Connection {
  edge: string;
  neighborId: string;
  neighborName: string;
  neighborType: string;
  relationType: string;
  outgoing: boolean;
}

function humanize(value: string): string {
  return value.replace(/_/g, " ");
}

function formatDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().slice(0, 10);
}

/** A single-value confidence meter: a score bar plus its basis and date. */
function ConfidenceGauge({ confidence }: { confidence: Confidence }) {
  const score = typeof confidence.score === "number" ? confidence.score : null;
  if (score === null) return null;
  const pct = Math.round(Math.max(0, Math.min(1, score)) * 100);
  return (
    <section className="detail__section">
      <h3 className="detail__label">Confidence</h3>
      <div className="gauge">
        <span className="gauge__score">{score.toFixed(2)}</span>
        <div
          className="gauge__track"
          role="meter"
          aria-valuenow={score}
          aria-valuemin={0}
          aria-valuemax={1}
          aria-label="Confidence score"
        >
          <span className="gauge__fill" style={{ width: `${pct}%` }} />
        </div>
      </div>
      <dl className="detail__meta">
        {confidence.basis && (
          <div className="detail__metarow">
            <dt>Basis</dt>
            <dd>{humanize(confidence.basis)}</dd>
          </div>
        )}
        {formatDate(confidence.lastEvaluated) && (
          <div className="detail__metarow">
            <dt>Evaluated</dt>
            <dd>{formatDate(confidence.lastEvaluated)}</dd>
          </div>
        )}
      </dl>
    </section>
  );
}

function ProvenanceBlock({ provenance }: { provenance: Provenance }) {
  const rows: [string, string][] = [];
  const push = (label: string, value: string | null | undefined) => {
    if (value) rows.push([label, value]);
  };
  push("Source", provenance.sourceType ? humanize(provenance.sourceType) : null);
  push("Method", provenance.extractionMethod ? humanize(provenance.extractionMethod) : null);
  push("Extractor", provenance.extractor ?? null);
  push("Reference", provenance.externalRef ?? null);
  push("Source id", provenance.sourceId ?? null);
  push("Extracted", formatDate(provenance.extractionDate));
  if (rows.length === 0) return null;
  return (
    <section className="detail__section">
      <h3 className="detail__label">Provenance</h3>
      <dl className="detail__meta">
        {rows.map(([label, value]) => (
          <div key={label} className="detail__metarow">
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export function DetailPanel({ onNavigate }: { onNavigate: (id: string) => void }) {
  const bundle = useBundle();
  const graph = useGraph();
  const selection = useTapestry((s) => s.selection);
  const select = useTapestry((s) => s.select);

  const byId = useMemo(() => {
    const map = new Map<string, EntityDoc>();
    for (const e of bundle.entities) {
      const id = e.id as string | undefined;
      if (id) map.set(id, e);
    }
    return map;
  }, [bundle]);

  const connections = useMemo<Connection[]>(() => {
    if (!selection || !graph.hasNode(selection)) return [];
    const out: Connection[] = [];
    graph.forEachEdge(selection, (edge, attr, source, target) => {
      const outgoing = source === selection;
      const neighborId = outgoing ? target : source;
      out.push({
        edge,
        neighborId,
        neighborName: (graph.getNodeAttribute(neighborId, "label") as string) ?? neighborId,
        neighborType: (graph.getNodeAttribute(neighborId, "entityType") as string) ?? "concept",
        relationType: (attr.relationType as string) ?? "related_to",
        outgoing,
      });
    });
    return out;
  }, [selection, graph]);

  if (!selection) return null;

  const doc = byId.get(selection);
  const nodeExists = graph.hasNode(selection);
  const name =
    (doc?.name as string) ??
    (nodeExists ? (graph.getNodeAttribute(selection, "label") as string) : selection);
  const entityType =
    (doc?.entityType as string) ??
    (nodeExists ? (graph.getNodeAttribute(selection, "entityType") as string) : "concept");
  const observations = Array.isArray(doc?.observations) ? (doc.observations as string[]) : [];
  const confidence = (doc?.confidence as Confidence | undefined) ?? undefined;
  const provenance = (doc?.provenance as Provenance | undefined) ?? undefined;
  const status = (doc?.status as string) ?? "active";
  const statusReason =
    (doc?.statusReason as string | null | undefined) ??
    (doc?.changeReason as string | null | undefined) ??
    null;

  return (
    <aside className="detail" aria-label={`Details for ${name}`}>
      <div className="detail__top">
        <span className="detail__chip">
          <span
            className="detail__chipdot"
            style={{ background: typeColorVar(entityType) }}
            aria-hidden="true"
          />
          {humanize(entityType)}
        </span>
        <button
          type="button"
          className="detail__close"
          aria-label="Close details"
          onClick={() => select(null)}
        >
          <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true">
            <path
              d="M4 4l8 8M12 4l-8 8"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
            />
          </svg>
        </button>
      </div>

      <h2 className="detail__name">{name}</h2>

      <div className="detail__statusrow">
        <span className={`detail__status detail__status--${status}`}>{humanize(status)}</span>
        {statusReason && <span className="detail__reason">{humanize(statusReason)}</span>}
      </div>

      <div className="detail__scroll">
        {observations.length > 0 && (
          <section className="detail__section">
            <h3 className="detail__label">Observations</h3>
            <ul className="detail__obs">
              {observations.map((obs, i) => (
                <li key={i}>{obs}</li>
              ))}
            </ul>
          </section>
        )}

        {confidence && <ConfidenceGauge confidence={confidence} />}

        {provenance && <ProvenanceBlock provenance={provenance} />}

        <section className="detail__section">
          <h3 className="detail__label">
            Relations
            <span className="detail__labelcount">{connections.length}</span>
          </h3>
          {connections.length === 0 ? (
            <p className="detail__empty">No connected entities.</p>
          ) : (
            <ul className="detail__neighbors">
              {connections.map((c) => (
                <li key={c.edge}>
                  <button
                    type="button"
                    className="detail__neighbor"
                    onClick={() => onNavigate(c.neighborId)}
                  >
                    <span
                      className="detail__rel"
                      style={{ color: `var(--edge-${edgeFamily(c.relationType)})` }}
                    >
                      {c.outgoing ? "→" : "←"} {humanize(c.relationType)}
                    </span>
                    <span className="detail__neighborname">
                      <span
                        className="detail__chipdot"
                        style={{ background: typeColorVar(c.neighborType) }}
                        aria-hidden="true"
                      />
                      {c.neighborName}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        {!doc && (
          <p className="detail__empty">
            This entity is in the graph but not in the loaded document set.
          </p>
        )}
      </div>
    </aside>
  );
}
