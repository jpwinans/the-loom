"""The in-memory working graph.

LoomGraph provides directed multigraph semantics with deterministic
iteration: node iteration in insertion order, parallel edges keyed by
relation id in insertion order, deduplicated neighbor iteration (out-edge
order, then in-edge order), and a dangling-edge skip during hydration. All
graph algorithms take a LoomGraph.

Documents are wire dicts (as served by the store), not models — the algorithms
read a handful of fields (entityType, relationType, polarity, name) and echo
docs into their outputs.
"""

from __future__ import annotations

from typing import Any

Doc = dict[str, Any]


class LoomGraph:
    """A tiny insertion-ordered directed multigraph over wire docs."""

    def __init__(self) -> None:
        self.node_docs: dict[str, Doc] = {}
        self.edge_docs: dict[str, Doc] = {}  # edge id -> relation doc (has from/to)
        self._out: dict[str, list[str]] = {}  # node -> [edge ids] in insertion order
        self._in: dict[str, list[str]] = {}

    # -- construction ----------------------------------------------------------

    def add_node(self, doc: Doc) -> None:
        node_id = doc["id"]
        self.node_docs[node_id] = doc
        self._out.setdefault(node_id, [])
        self._in.setdefault(node_id, [])

    def add_edge(self, doc: Doc) -> None:
        self.edge_docs[doc["id"]] = doc
        self._out[doc["from"]].append(doc["id"])
        self._in[doc["to"]].append(doc["id"])

    # -- queries ----------------------------------------------------------------

    @property
    def order(self) -> int:
        return len(self.node_docs)

    @property
    def size(self) -> int:
        return len(self.edge_docs)

    def nodes(self) -> list[str]:
        return list(self.node_docs)

    def has_node(self, node_id: str) -> bool:
        return node_id in self.node_docs

    def out_edge_ids(self, node_id: str) -> list[str]:
        return self._out.get(node_id, [])

    def in_edge_ids(self, node_id: str) -> list[str]:
        return self._in.get(node_id, [])

    def edge_target(self, edge_id: str) -> str:
        target: str = self.edge_docs[edge_id]["to"]
        return target

    def edge_source(self, edge_id: str) -> str:
        source: str = self.edge_docs[edge_id]["from"]
        return source

    def out_neighbors(self, node_id: str) -> list[str]:
        """Deduplicated targets in out-edge insertion order (outboundNeighbors)."""
        seen: dict[str, None] = {}
        for edge_id in self._out.get(node_id, []):
            seen.setdefault(self.edge_docs[edge_id]["to"])
        return list(seen)

    def in_neighbors(self, node_id: str) -> list[str]:
        """Deduplicated sources in in-edge insertion order (inboundNeighbors)."""
        seen: dict[str, None] = {}
        for edge_id in self._in.get(node_id, []):
            seen.setdefault(self.edge_docs[edge_id]["from"])
        return list(seen)

    def neighbors(self, node_id: str) -> list[str]:
        """Union of IN then OUT neighbors, deduplicated — IN-edge endpoints are
        visited before OUT-edge endpoints, which fixes the neighbor iteration
        order."""
        seen: dict[str, None] = {}
        for neighbor in self.in_neighbors(node_id):
            seen.setdefault(neighbor)
        for neighbor in self.out_neighbors(node_id):
            seen.setdefault(neighbor)
        return list(seen)

    def has_directed_edge(self, from_id: str, to_id: str) -> bool:
        return any(self.edge_docs[edge_id]["to"] == to_id for edge_id in self._out.get(from_id, []))

    def has_any_edge(self, a: str, b: str) -> bool:
        """Edge in either direction — a two-node adjacency query is treated as
        MIXED (undirected), not directed."""
        return self.has_directed_edge(a, b) or self.has_directed_edge(b, a)

    def node_edges(self, node_id: str) -> list[str]:
        """All edge ids attached to a node: out-edge order then in-edge order."""
        return [*self._out.get(node_id, []), *self._in.get(node_id, [])]


def hydrate_graph(entities: list[Doc], relations: list[Doc]) -> LoomGraph:
    """Build the working graph, skipping dangling relations (entity listing is
    status-filtered while relation listing returns all)."""
    graph = LoomGraph()
    for entity in entities:
        graph.add_node(entity)
    for relation in relations:
        if relation["from"] in graph.node_docs and relation["to"] in graph.node_docs:
            graph.add_edge(relation)
    return graph
