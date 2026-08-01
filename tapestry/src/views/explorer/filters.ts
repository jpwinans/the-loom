/**
 * filters — the non-destructive visibility calculation for the Explorer.
 *
 * `applyFilters` never mutates the graph: it returns the sets of node and edge
 * keys that survive the active `Filters`, which Sigma's `nodeReducer` /
 * `edgeReducer` translate into `hidden: true` for everything outside them.
 * Colours follow the entity, never a filter — hiding is the only visual change.
 *
 * Node rules (a node must pass ALL active facets):
 *   - entityTypes: empty ⇒ every type passes; else the node's type must be listed.
 *   - statuses:    empty ⇒ every status passes; else the node's status must be listed.
 *   - confidenceMin: a numeric confidence must be ≥ the floor. A node with no
 *     confidence (null/absent) PASSES any floor — absence of evidence is not a
 *     reason to hide a node.
 * Edge rule: an edge is visible only when BOTH endpoints are visible AND its
 * relationType passes (empty relationTypes ⇒ every type passes).
 */
import type Graph from "graphology";
import type { Filters } from "../../state/store";

export interface Visibility {
  visibleNodes: Set<string>;
  visibleEdges: Set<string>;
}

function nodePasses(attr: Record<string, unknown>, filters: Filters): boolean {
  const { entityTypes, statuses, confidenceMin } = filters;

  if (entityTypes.length > 0 && !entityTypes.includes(attr.entityType as string)) {
    return false;
  }
  if (statuses.length > 0 && !statuses.includes(attr.status as string)) {
    return false;
  }

  const confidence = attr.confidence;
  // Null / absent confidence passes any floor; a number must clear it.
  if (typeof confidence === "number" && confidence < confidenceMin) {
    return false;
  }

  return true;
}

export function applyFilters(graph: Graph, filters: Filters): Visibility {
  const visibleNodes = new Set<string>();
  graph.forEachNode((node, attr) => {
    if (nodePasses(attr, filters)) visibleNodes.add(node);
  });

  const { relationTypes } = filters;
  const visibleEdges = new Set<string>();
  graph.forEachEdge((edge, attr, source, target) => {
    if (!visibleNodes.has(source) || !visibleNodes.has(target)) return;
    if (relationTypes.length > 0 && !relationTypes.includes(attr.relationType as string)) {
      return;
    }
    visibleEdges.add(edge);
  });

  return { visibleNodes, visibleEdges };
}
