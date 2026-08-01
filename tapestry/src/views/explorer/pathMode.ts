/**
 * pathMode — shortest-path lookup between two entities for the Explorer's path
 * tool.
 *
 * `findPath` treats the graph as undirected for reachability (a causal edge
 * pointing the "wrong way" still connects two entities), but the returned edge
 * keys are the real directed edges the caller should highlight, one per hop.
 * `graphology-shortest-path`'s `bidirectional` walks strictly along
 * inbound/outbound edges, so an undirected mirror of the graph's topology is
 * built first and searched; each hop in the resulting node path is then mapped
 * back onto a concrete edge in the original graph, preferring the forward
 * direction (`a -> b`) and falling back to the reverse (`b -> a`) — a
 * deterministic choice since either edge equally represents the hop.
 */
import Graph from "graphology";
import { bidirectional } from "graphology-shortest-path/unweighted";

export interface Path {
  nodes: string[];
  edges: string[];
}

/** An undirected view of `graph`'s topology: same nodes, one plain edge per
 * connected pair (self-loops dropped — they never help pathfinding). */
function undirectedMirror(graph: Graph): Graph {
  const mirror = new Graph({ type: "undirected" });
  graph.forEachNode((node) => mirror.addNode(node));
  graph.forEachEdge((_edge, _attr, source, target) => {
    if (source === target) return;
    if (!mirror.hasEdge(source, target)) mirror.addEdge(source, target);
  });
  return mirror;
}

/** The directed edge key actually traversed between two adjacent nodes: the
 * first `a -> b` edge if one exists, else the first `b -> a` edge. */
function directedEdgeBetween(graph: Graph, a: string, b: string): string | null {
  const forward = graph.edges(a, b);
  if (forward.length > 0) return forward[0];
  const backward = graph.edges(b, a);
  if (backward.length > 0) return backward[0];
  return null;
}

export function findPath(graph: Graph, from: string, to: string): Path | null {
  if (!graph.hasNode(from) || !graph.hasNode(to)) return null;
  if (from === to) return { nodes: [from], edges: [] };

  const nodes = bidirectional(undirectedMirror(graph), from, to);
  if (!nodes) return null;

  const edges: string[] = [];
  for (let i = 0; i < nodes.length - 1; i += 1) {
    const key = directedEdgeBetween(graph, nodes[i], nodes[i + 1]);
    if (key === null) return null; // topology mismatch — shouldn't happen
    edges.push(key);
  }

  return { nodes, edges };
}
