/**
 * replay — the pure temporal-replay engine behind the Chronicle scrubber and
 * diff mode: a timeline built from `temporal.events`, an as-of state
 * projection, and a two-instant diff.
 *
 * No Sigma, no DOM. This is `read_entity_as_of` semantics reimplemented
 * client-side over the shipped event log: given the *current* graphology model
 * (the same one the Explorer renders) and the bundle's event stream, `stateAt`
 * answers "what was visible, and in what status, at instant t" without ever
 * mutating the graph — the view layer (Chronicle.tsx) turns that answer into
 * reducers (hidden / dimmed / restyled).
 *
 * Event payload shapes (verified against `theloom/store/events.py` /
 * `falkor.py` and the `tapestry-dev` fixture): `entity_created {entity}`,
 * `entity_updated {entity, previous}`, `entity_status_changed {entity,
 * previous}`, `entity_deleted {entity}`, `relation_created {relation}`,
 * `relation_updated {relation, previous}`, `relation_deleted {relation}`,
 * `entities_merged {primary, secondary, previousPrimary, previousSecondary,
 * redirectedRelations, supersedesRelation}`. Each `TapestryEventRaw` is
 * `{id, at, type, payload}` with `at` a canonical ISO string (either the
 * `Z`-suffixed or `+00:00`-suffixed form — both are `Date.parse`-safe).
 */
import type Graph from "graphology";
import type { TapestryBundleRaw } from "../../lib/data";

/** One normalized row for the Chronicle event list. */
export interface ChronicleEvent {
  t: number;
  type: string;
  kind: "node" | "edge";
  id: string;
  label: string;
}

/** A `{time, status}` sample — one entry per status-changing event, sorted by time. */
export interface StatusSample {
  t: number;
  status: string;
}

/** The event log reshaped into lookups `stateAt`/`diffStates` read directly. */
export interface Timeline {
  nodeCreated: Map<string, number>;
  nodeRemoved: Map<string, number>;
  edgeCreated: Map<string, number>;
  edgeRemoved: Map<string, number>;
  nodeStatus: Map<string, StatusSample[]>;
  nodeUpdated: Map<string, number[]>;
  events: ChronicleEvent[];
  start: number;
  end: number;
}

/** The replay projection at one instant. */
export interface ChronicleState {
  visibleNodes: Set<string>;
  visibleEdges: Set<string>;
  statusById: Map<string, string>;
}

/** Node ids that changed between two instants, classified by how. */
export interface Diff {
  added: Set<string>;
  invalidated: Set<string>;
  changed: Set<string>;
}

type EventPayload = {
  entity?: { id?: string; status?: string };
  relation?: { id?: string; from?: string; to?: string };
};

type TapestryEventRaw = {
  id: string;
  at: string;
  type: string;
  payload: EventPayload;
};

function pushNodeUpdated(nodeUpdated: Map<string, number[]>, id: string, t: number): void {
  const list = nodeUpdated.get(id);
  if (list) list.push(t);
  else nodeUpdated.set(id, [t]);
}

function pushNodeStatus(nodeStatus: Map<string, StatusSample[]>, id: string, sample: StatusSample): void {
  const list = nodeStatus.get(id);
  if (list) list.push(sample);
  else nodeStatus.set(id, [sample]);
}

/**
 * One pass over `bundle.temporal.events`, building the id→ms maps `stateAt`
 * and `diffStates` read, plus the normalized, time-sorted event list for the
 * Chronicle's `EventList`. `entities_merged` / `relation_updated` are recorded
 * in `events` (so the stream is complete) but have no state effect here — a
 * merge's node/relation redirection is a structural rewrite the replay engine
 * does not attempt to reverse-project.
 */
export function buildTimeline(bundle: TapestryBundleRaw): Timeline {
  const nodeCreated = new Map<string, number>();
  const nodeRemoved = new Map<string, number>();
  const edgeCreated = new Map<string, number>();
  const edgeRemoved = new Map<string, number>();
  const nodeStatus = new Map<string, StatusSample[]>();
  const nodeUpdated = new Map<string, number[]>();
  const events: ChronicleEvent[] = [];

  const raw = (bundle.temporal?.events ?? []) as unknown as TapestryEventRaw[];

  for (const event of raw) {
    const t = Date.parse(event.at);
    if (Number.isNaN(t)) continue;
    const entityId = event.payload?.entity?.id;
    const relationId = event.payload?.relation?.id;

    switch (event.type) {
      case "entity_created": {
        if (entityId != null) {
          if (!nodeCreated.has(entityId)) nodeCreated.set(entityId, t);
          events.push({ t, type: event.type, kind: "node", id: entityId, label: entityId });
        }
        break;
      }
      case "entity_updated": {
        if (entityId != null) {
          pushNodeUpdated(nodeUpdated, entityId, t);
          events.push({ t, type: event.type, kind: "node", id: entityId, label: entityId });
        }
        break;
      }
      case "entity_status_changed": {
        if (entityId != null) {
          const status = event.payload?.entity?.status ?? "active";
          pushNodeStatus(nodeStatus, entityId, { t, status });
          events.push({ t, type: event.type, kind: "node", id: entityId, label: entityId });
        }
        break;
      }
      case "entity_deleted": {
        if (entityId != null) {
          if (!nodeRemoved.has(entityId)) nodeRemoved.set(entityId, t);
          events.push({ t, type: event.type, kind: "node", id: entityId, label: entityId });
        }
        break;
      }
      case "relation_created": {
        if (relationId != null) {
          if (!edgeCreated.has(relationId)) edgeCreated.set(relationId, t);
          events.push({ t, type: event.type, kind: "edge", id: relationId, label: relationId });
        }
        break;
      }
      case "relation_updated": {
        if (relationId != null) {
          events.push({ t, type: event.type, kind: "edge", id: relationId, label: relationId });
        }
        break;
      }
      case "relation_deleted": {
        if (relationId != null) {
          if (!edgeRemoved.has(relationId)) edgeRemoved.set(relationId, t);
          events.push({ t, type: event.type, kind: "edge", id: relationId, label: relationId });
        }
        break;
      }
      case "entities_merged": {
        // No per-node id in the {entity} shape — recorded for the event
        // stream only, with no state effect on visibility/status.
        events.push({ t, type: event.type, kind: "node", id: event.id, label: event.id });
        break;
      }
      default: {
        // Unknown event type: still surface it in the stream, no state effect.
        const id = entityId ?? relationId ?? event.id;
        const kind: "node" | "edge" = relationId != null ? "edge" : "node";
        events.push({ t, type: event.type, kind, id, label: id });
      }
    }
  }

  events.sort((a, b) => a.t - b.t);

  let start = events.length > 0 ? events[0].t : 0;
  let end = events.length > 0 ? events[events.length - 1].t : 0;
  if (start === end) end = start + 1; // degenerate single-instant guard

  return { nodeCreated, nodeRemoved, edgeCreated, edgeRemoved, nodeStatus, nodeUpdated, events, start, end };
}

/**
 * The replay projection at instant `t`: which nodes/edges of the *current*
 * graph were visible, and each node's effective status.
 *
 * A node is visible when its creation ms is `<= t` — or it has no creation
 * record at all (imported/migrated entities emit no `entity_created`, so
 * "unknown creation" is treated as present-from-start) — and it was not
 * removed at/before `t`. Effective status is the latest `nodeStatus` sample
 * with `time <= t`, else `"active"`. An edge is visible when created `<= t`
 * (or has no creation record), not removed, and both endpoints are visible.
 */
export function stateAt(timeline: Timeline, graph: Graph, t: number): ChronicleState {
  const visibleNodes = new Set<string>();
  const statusById = new Map<string, string>();

  graph.forEachNode((node) => {
    const createdAt = timeline.nodeCreated.get(node);
    if (createdAt != null && createdAt > t) return; // not yet born
    const removedAt = timeline.nodeRemoved.get(node);
    if (removedAt != null && removedAt <= t) return; // removed by t
    visibleNodes.add(node);

    const samples = timeline.nodeStatus.get(node);
    if (samples != null) {
      let status: string | null = null;
      for (const sample of samples) {
        if (sample.t <= t) status = sample.status;
        else break; // samples are appended in event order == time order
      }
      if (status != null) statusById.set(node, status);
    }
  });

  const visibleEdges = new Set<string>();
  graph.forEachEdge((edge, _attrs, source, target) => {
    const createdAt = timeline.edgeCreated.get(edge);
    if (createdAt != null && createdAt > t) return;
    const removedAt = timeline.edgeRemoved.get(edge);
    if (removedAt != null && removedAt <= t) return;
    if (!visibleNodes.has(source) || !visibleNodes.has(target)) return;
    visibleEdges.add(edge);
  });

  return { visibleNodes, visibleEdges, statusById };
}

/**
 * What changed between two instants `(t0, t1]`: **added** — created in the
 * window; **invalidated** — a status change to a non-`active` status in the
 * window; **changed** — updated in the window and not already `added`.
 */
export function diffStates(timeline: Timeline, t0: number, t1: number): Diff {
  const lo = Math.min(t0, t1);
  const hi = Math.max(t0, t1);

  const added = new Set<string>();
  for (const [id, t] of timeline.nodeCreated) {
    if (t > lo && t <= hi) added.add(id);
  }

  const invalidated = new Set<string>();
  for (const [id, samples] of timeline.nodeStatus) {
    for (const sample of samples) {
      if (sample.t > lo && sample.t <= hi && sample.status !== "active") {
        invalidated.add(id);
        break;
      }
    }
  }

  const changed = new Set<string>();
  for (const [id, times] of timeline.nodeUpdated) {
    if (added.has(id)) continue;
    if (times.some((t) => t > lo && t <= hi)) changed.add(id);
  }

  return { added, invalidated, changed };
}
