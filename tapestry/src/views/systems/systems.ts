/**
 * systems — the pure model helpers the Systems (causal-loop) view renders from.
 *
 * No Sigma, no DOM: a causal-only subgraph, a loop's ordered edge keys, the
 * leverage-point → target index, and the flow-animation intensity curve. Each is
 * unit-tested in isolation (vitest + happy-dom); the view (Systems.tsx) wires the
 * graph into Sigma and drives colour/animation from these functions.
 *
 * The causal subgraph mirrors `buildGraph`'s discipline — deterministic seeded
 * positions (shared with the Explorer so a node sits in the same place across
 * views), and dangling relations silently skipped.
 */
import Graph from "graphology";
import type { TapestryBundleRaw } from "../../lib/data";
import { edgeFamily, initialPosition, resolveEdgeColor, resolveTypeColor } from "../explorer/buildGraph";

/** A feedback loop, mirroring the bundle's `analytics.loops[]` shape. */
export interface LoopInfo {
  id: string | number | null;
  name: string;
  classification: "reinforcing" | "balancing";
  netPolarity: string;
  memberCount: number;
  path: string[];
  memberIds: string[];
}

/** A Meadows leverage point resolved onto the causal variable it acts on. */
export interface LeverageMark {
  level: number | null;
  meadowsName: string | null;
  pointName: string;
}

/** Edge width per relation strength — mirrors `buildGraph`'s STRENGTH_SIZE. */
const STRENGTH_SIZE: Record<string, number> = {
  weak: 1,
  moderate: 1.5,
  strong: 2.5,
  foundational: 3.5,
};

/**
 * A directed multigraph of only the causal slice of the weave: every relation
 * whose family is `causal` (causes / enables / requires / inhibits / amplifies /
 * dampens) plus the entities those relations touch. Non-causal edges — and any
 * entity carrying none — are left out entirely, so the diagram reads as a pure
 * causal-loop model. Node fill follows entity type; the edge colour is seeded to
 * the causal family tint and re-tinted by polarity in the view.
 */
export function buildCausalGraph(bundle: TapestryBundleRaw): Graph {
  const graph = new Graph({ multi: true, type: "directed" });

  const entityById = new Map<string, Record<string, unknown>>();
  for (const entity of bundle.entities) {
    const id = entity.id as string | undefined;
    if (id != null && !entityById.has(id)) entityById.set(id, entity);
  }

  // First pass: the causal relations and the entities they connect.
  const causalRelations: Record<string, unknown>[] = [];
  const causalEndpoints = new Set<string>();
  for (const relation of bundle.relations) {
    const relationType = (relation.relationType as string) ?? "related_to";
    if (edgeFamily(relationType) !== "causal") continue;
    const from = relation.from as string | undefined;
    const to = relation.to as string | undefined;
    if (from == null || to == null) continue;
    causalRelations.push(relation);
    causalEndpoints.add(from);
    causalEndpoints.add(to);
  }

  // Only entities that carry a causal edge become nodes.
  for (const id of causalEndpoints) {
    const entity = entityById.get(id);
    if (entity == null || graph.hasNode(id)) continue;
    const entityType = (entity.entityType as string) ?? "concept";
    const { x, y } = initialPosition(id);
    graph.addNode(id, {
      label: (entity.name as string) ?? id,
      entityType,
      degree: 0,
      size: 6,
      color: resolveTypeColor(entityType),
      x,
      y,
    });
  }

  // Add causal edges, skipping any whose endpoint fell outside the node set.
  for (const relation of causalRelations) {
    const id = relation.id as string;
    const from = relation.from as string;
    const to = relation.to as string;
    if (id == null || !graph.hasNode(from) || !graph.hasNode(to) || graph.hasEdge(id)) continue;
    const relationType = (relation.relationType as string) ?? "causes";
    const strength = (relation.strength as string) ?? "moderate";
    graph.addEdgeWithKey(id, from, to, {
      relationType,
      polarity: (relation.polarity as string | null) ?? null,
      strength,
      size: STRENGTH_SIZE[strength] ?? 1.5,
      color: resolveEdgeColor(relationType),
      type: "arrow",
    });
  }

  // Node size scales with connectivity, once every edge has landed.
  graph.forEachNode((node) => {
    const degree = graph.degree(node);
    graph.setNodeAttribute(node, "degree", degree);
    graph.setNodeAttribute(node, "size", 5 + 2 * Math.sqrt(degree));
  });

  return graph;
}

/**
 * The loop's directed edge keys in path order. A loop `path` of `[a, b, c, a]`
 * yields the keys of `a→b`, `b→c`, `c→a` (the first edge found for each
 * consecutive pair). Pairs with no edge in the causal subgraph are skipped, so a
 * loop that partly runs over non-causal relations still returns its causal legs.
 *
 * Uses `outEdges(from, to)` (directed, source→target), not `edges(from, to)`:
 * graphology's undirected `edges(a, b)` returns edges in BOTH directions
 * (reverse-first, verified) so it can pick the wrong-way edge — and the loop's
 * direction is load-bearing for the flow animation.
 */
export function loopEdgeKeys(loop: LoopInfo, graph: Graph): string[] {
  const keys: string[] = [];
  for (let i = 0; i < loop.path.length - 1; i += 1) {
    const from = loop.path[i];
    const to = loop.path[i + 1];
    if (!graph.hasNode(from) || !graph.hasNode(to)) continue;
    const edges = graph.outEdges(from, to);
    if (edges.length > 0) keys.push(edges[0]);
  }
  return keys;
}

/**
 * Each leverage point mapped onto the entity it acts on:
 * `targetEntityId → {level, meadowsName, pointName}`. Leverage-point docs come
 * from `analytics.leveragePoints` when present (they carry the parsed
 * `_metadata`), else from entities typed `leverage_point`; the target is resolved
 * through the `leverage_point --part_of--> target` relation.
 */
export function leverageTargets(bundle: TapestryBundleRaw): Map<string, LeverageMark> {
  const marks = new Map<string, LeverageMark>();

  const analytics = bundle.analytics as
    | { leveragePoints?: Record<string, unknown>[] }
    | undefined;
  const points: Record<string, unknown>[] =
    analytics?.leveragePoints && analytics.leveragePoints.length > 0
      ? analytics.leveragePoints
      : bundle.entities.filter((e) => (e.entityType as string) === "leverage_point");

  // Index each part_of edge by its source id → target id.
  const partOfTarget = new Map<string, string>();
  for (const rel of bundle.relations) {
    if ((rel.relationType as string) === "part_of") {
      const from = rel.from as string | undefined;
      const to = rel.to as string | undefined;
      if (from != null && to != null) partOfTarget.set(from, to);
    }
  }

  for (const point of points) {
    const id = point.id as string | undefined;
    if (id == null) continue;
    const target = partOfTarget.get(id);
    if (target == null) continue;
    const metadata = point._metadata as { level?: number; meadowsName?: string } | undefined;
    marks.set(target, {
      level: metadata?.level ?? null,
      meadowsName: metadata?.meadowsName ?? null,
      pointName: (point.name as string) ?? id,
    });
  }

  return marks;
}

/**
 * A traveling pulse in `[0, 1]` for the flow animation. A raised-cosine bump
 * centred on `phase * count` (fractional edge position), using the wrapped
 * circular distance around the cycle, so exactly one edge peaks at a time and its
 * neighbours glow less. `phase` runs 0→1 over one lap; at `phase = 1` the bump
 * has travelled a full cycle back onto edge 0.
 */
export function flowIntensity(index: number, count: number, phase: number): number {
  if (count <= 0) return 0;
  const center = phase * count;
  const raw = Math.abs(index - center);
  const m = ((raw % count) + count) % count; // non-negative modulo
  const wrapped = Math.min(m, count - m); // circular distance, in [0, count/2]
  const frac = wrapped / count; // in [0, 0.5]
  return 0.5 * (1 + Math.cos(2 * Math.PI * frac));
}
