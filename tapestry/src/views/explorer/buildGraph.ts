/**
 * buildGraph — turn a raw TapestryBundle into a renderable graphology model.
 *
 * A directed multigraph whose node/edge attributes carry every visual channel
 * Sigma reads: node fill by entity type, node size by connectivity, edge tint by
 * relation family, edge width by strength. Colours are stored as concrete values
 * (resolved from the CSS token layer, with a headless fallback) so the graph is
 * self-describing; `resolveGraphColors()` re-derives them when the theme changes.
 *
 * Mirrors the store's `hydrate_graph` in one respect: relations whose endpoints
 * are missing are silently skipped rather than dropped as errors.
 */
import Graph from "graphology";
import louvain from "graphology-communities-louvain";
import { ENTITY_TYPES } from "../../design/palette";
import type { TapestryBundleRaw } from "../../lib/data";

// ---- Encoding maps ---------------------------------------------------------

/** Edge width per relation strength (graph units; Sigma scales by camera). */
const STRENGTH_SIZE: Record<string, number> = {
  weak: 1,
  moderate: 1.5,
  strong: 2.5,
  foundational: 3.5,
};

/**
 * Relation families, per the RelationType docstring in `theloom/model.py`:
 *   structural — connective tissue, no polarity → recessive neutral thread
 *   epistemic  — reasoning about claims, no polarity → cool blue-slate
 *   causal     — force/flow, carries polarity → warm amber accent
 * Blue↔amber is the canonical CVD-safe pair; the neutral is low-chroma, so all
 * three separate for colour-vision deficiency. Colour is a family cue, never the
 * sole encoding — arrows show direction and the edge carries its type on hover.
 */
export type EdgeFamily = "structural" | "epistemic" | "causal";

const EDGE_FAMILY: Record<string, EdgeFamily> = {
  related_to: "structural",
  instance_of: "structural",
  part_of: "structural",
  sources: "structural",
  calls: "structural",
  references: "structural",
  crystallized_from: "structural",
  supports: "epistemic",
  contradicts: "epistemic",
  questions: "epistemic",
  supersedes: "epistemic",
  causes: "causal",
  enables: "causal",
  requires: "causal",
  inhibits: "causal",
  amplifies: "causal",
  dampens: "causal",
};

export function edgeFamily(relationType: string): EdgeFamily {
  return EDGE_FAMILY[relationType] ?? "structural";
}

// ---- Colour resolution -----------------------------------------------------
//
// The live app reads the theme-aware `--type-*` / `--edge-*` custom properties
// off the document root. These fallbacks mirror `tokens.css :root` (light) so a
// valid colour is always present in headless/test contexts where CSS vars do
// not resolve, and to avoid a flash of grey before the first resolve pass.

const TYPE_FALLBACK: Record<string, string> = {
  concept: "#2f95e9",
  claim: "#096b97",
  source: "#1eb4b0",
  question: "#978df9",
  evidence: "#018463",
  pattern: "#8a3e96",
  insight: "#ad8d11",
  tension: "#c85047",
  convergence: "#e879b9",
  system: "#088735",
  variable: "#7ba82f",
  loop: "#14bac5",
  leverage_point: "#dd9314",
  event: "#a14206",
  procedure: "#eb882e",
  hypothesis: "#577ddf",
  inference_rule: "#b03851",
  inference_trace: "#a9371d",
  research_session: "#7547ab",
};

const EDGE_FALLBACK: Record<EdgeFamily, string> = {
  structural: "#9096a3",
  epistemic: "#3f6fb0",
  causal: "#c67a1e",
};

const MUTED_FALLBACK = "#767b88";

function readCssVar(name: string): string {
  if (typeof document === "undefined") return "";
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Concrete fill for an entity type; unknown/legacy types fall back to muted ink. */
export function resolveTypeColor(entityType: string): string {
  const known = (ENTITY_TYPES as readonly string[]).includes(entityType);
  const live = readCssVar(known ? `--type-${entityType}` : "--color-text-3");
  if (live) return live;
  return (known ? TYPE_FALLBACK[entityType] : undefined) ?? MUTED_FALLBACK;
}

/** Concrete tint for a relation, by its family. */
export function resolveEdgeColor(relationType: string): string {
  const family = edgeFamily(relationType);
  return readCssVar(`--edge-${family}`) || EDGE_FALLBACK[family];
}

/**
 * Re-derive node and edge colours from the current CSS token values. Call this
 * after the resolved theme changes, then `sigma.refresh()`.
 */
export function resolveGraphColors(graph: Graph): void {
  graph.forEachNode((node, attr) => {
    graph.setNodeAttribute(node, "color", resolveTypeColor(attr.entityType as string));
  });
  graph.forEachEdge((edge, attr) => {
    graph.setEdgeAttribute(edge, "color", resolveEdgeColor(attr.relationType as string));
  });
}

// ---- Deterministic initial layout ------------------------------------------
//
// FA2 needs a non-degenerate starting configuration. Seeding each node's start
// position on a hash of its id makes the pre-layout state reproducible run to
// run (deterministic screenshots, stable deep-links) without a stored layout.

function hashSeed(id: string): number {
  let h = 2166136261 >>> 0; // FNV-1a
  for (let i = 0; i < id.length; i += 1) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function initialPosition(id: string): { x: number; y: number } {
  const rng = mulberry32(hashSeed(id));
  const angle = rng() * Math.PI * 2;
  const radius = Math.sqrt(rng()) * 100; // uniform over a disc
  return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
}

// ---- Assembly --------------------------------------------------------------

function readConfidence(doc: Record<string, unknown>): number | null {
  const conf = doc.confidence as { score?: number } | null | undefined;
  return conf && typeof conf.score === "number" ? conf.score : null;
}

export function buildGraph(bundle: TapestryBundleRaw): Graph {
  const graph = new Graph({ multi: true, type: "directed" });

  for (const entity of bundle.entities) {
    const id = entity.id as string;
    if (id == null || graph.hasNode(id)) continue;
    const entityType = (entity.entityType as string) ?? "concept";
    const { x, y } = initialPosition(id);
    graph.addNode(id, {
      label: (entity.name as string) ?? id,
      entityType,
      confidence: readConfidence(entity),
      status: (entity.status as string) ?? "active",
      degree: 0,
      community: 0,
      size: 3,
      color: resolveTypeColor(entityType),
      x,
      y,
    });
  }

  for (const relation of bundle.relations) {
    const id = relation.id as string;
    const from = relation.from as string;
    const to = relation.to as string;
    // Skip dangling relations — an endpoint outside the scoped node set.
    if (id == null || !graph.hasNode(from) || !graph.hasNode(to) || graph.hasEdge(id)) continue;
    const relationType = (relation.relationType as string) ?? "related_to";
    const strength = (relation.strength as string) ?? "moderate";
    graph.addEdgeWithKey(id, from, to, {
      relationType,
      polarity: (relation.polarity as string | null) ?? null,
      strength,
      confidence: readConfidence(relation),
      size: STRENGTH_SIZE[strength] ?? 1.5,
      color: resolveEdgeColor(relationType),
      type: "arrow",
    });
  }

  // Node size scales with connectivity — computed once every edge has landed.
  graph.forEachNode((node) => {
    const degree = graph.degree(node);
    graph.setNodeAttribute(node, "degree", degree);
    graph.setNodeAttribute(node, "size", 3 + 2 * Math.sqrt(degree));
  });

  // Louvain needs at least one edge; an edgeless scope keeps community 0.
  if (graph.size > 0) louvain.assign(graph);

  return graph;
}
