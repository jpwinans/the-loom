"""Substrate algorithm tests — verified on known graphs.

The golden tests are the heavyweight gate; these pin the delicate details:
Johnson rotation (cycles root at the least vertex), loop classification and
naming, bidirectional tie-breaking, LIFO all-paths order, and component
traversal orders.
"""

from __future__ import annotations

from typing import Any

from theloom.graph.analytics import (
    connected_components,
    degree_centrality,
    pagerank_centrality,
    strongly_connected_components,
)
from theloom.graph.cycles import (
    classify_loop,
    find_all_cycles,
    find_circuits,
    find_cycle_paths,
    generate_loop_name,
    has_cycle,
)
from theloom.graph.hydrate import hydrate_graph
from theloom.graph.paths import bidirectional, bounded_all_simple_paths


def ent(node_id: str, name: str | None = None, entity_type: str = "variable") -> dict[str, Any]:
    return {"id": node_id, "name": name or node_id.upper(), "entityType": entity_type}


def rel(
    edge_id: str,
    from_id: str,
    to_id: str,
    relation_type: str = "causes",
    polarity: str | None = "+",
) -> dict[str, Any]:
    return {
        "id": edge_id,
        "from": from_id,
        "to": to_id,
        "relationType": relation_type,
        "polarity": polarity,
    }


TRIANGLE_ENTITIES = [ent("a"), ent("b"), ent("c")]
TRIANGLE_RELATIONS = [
    rel("r1", "a", "b"),
    rel("r2", "b", "c"),
    rel("r3", "c", "a", polarity="-"),
]


def test_find_circuits_roots_cycles_at_least_vertex() -> None:
    # 0->1->2->0 and 1->2->1 (two circuits) — rotation starts at least index.
    edges = [[1], [2], [0, 1]]
    circuits = find_circuits(edges)
    assert [0, 1, 2, 0] in circuits
    assert [1, 2, 1] in circuits
    assert len(circuits) == 2


def test_find_all_cycles_only_causal_and_closed() -> None:
    relations = [*TRIANGLE_RELATIONS, rel("r4", "a", "c", "related_to", None)]
    cycles = find_all_cycles(TRIANGLE_ENTITIES, relations)
    assert cycles == [["a", "b", "c", "a"]]


def test_classify_and_name_balancing_loop() -> None:
    analysis = classify_loop(["a", "b", "c", "a"], TRIANGLE_RELATIONS)
    assert analysis["classification"] == "balancing"  # one negative
    assert analysis["netPolarity"] == "-"
    assert analysis["polarityChain"] == ["+", "+", "-"]
    assert analysis["memberCount"] == 3
    name = generate_loop_name(analysis, {e["id"]: e for e in TRIANGLE_ENTITIES})
    assert name == "A-B-C Balancing Loop"


def test_cycle_paths_and_has_cycle() -> None:
    graph = hydrate_graph(TRIANGLE_ENTITIES, TRIANGLE_RELATIONS)
    assert has_cycle(graph)
    assert find_cycle_paths(graph) == [["a", "b", "c", "a"]]
    acyclic = hydrate_graph(TRIANGLE_ENTITIES, TRIANGLE_RELATIONS[:2])
    assert not has_cycle(acyclic)


def test_bidirectional_shortest_path_and_none() -> None:
    entities = [ent(x) for x in "abcd"]
    relations = [rel("r1", "a", "b"), rel("r2", "b", "d"), rel("r3", "a", "c"), rel("r4", "c", "d")]
    graph = hydrate_graph(entities, relations)
    path = bidirectional(graph, "a", "d")
    assert path is not None and len(path) == 3 and path[0] == "a" and path[-1] == "d"
    assert bidirectional(graph, "d", "a") is None  # directed
    assert bidirectional(graph, "a", "a") == ["a"]


def test_bounded_all_simple_paths_lifo_order_and_truncation() -> None:
    entities = [ent(x) for x in "abcd"]
    relations = [rel("r1", "a", "b"), rel("r2", "b", "d"), rel("r3", "a", "c"), rel("r4", "c", "d")]
    graph = hydrate_graph(entities, relations)
    result = bounded_all_simple_paths(graph, "a", "d", max_depth=5, max_paths=1000, timeout_ms=5000)
    # LIFO: the last-pushed neighbor (c) is explored first.
    assert result["paths"] == [["a", "c", "d"], ["a", "b", "d"]]
    assert result["truncated"] is False
    truncated = bounded_all_simple_paths(graph, "a", "d", max_depth=5, max_paths=1, timeout_ms=5000)
    assert truncated["truncated"] is True and truncated["truncationReason"] == "maxPaths"


def test_components_orders() -> None:
    entities = [ent(x) for x in "abcde"]
    relations = [rel("r1", "a", "b"), rel("r2", "b", "a"), rel("r3", "c", "d")]
    graph = hydrate_graph(entities, relations)
    weak = connected_components(graph)
    assert weak == [["a", "b"], ["c", "d"], ["e"]]
    strong = strongly_connected_components(graph)
    assert ["e"] in strong and ["d"] in strong
    assert next(c for c in strong if len(c) == 2) in (["a", "b"], ["b", "a"])


def test_degree_and_pagerank_values() -> None:
    graph = hydrate_graph(TRIANGLE_ENTITIES, TRIANGLE_RELATIONS)
    degrees = degree_centrality(graph)
    assert degrees == {"a": 1.0, "b": 1.0, "c": 1.0}  # each degree 2 / (3-1)
    ranks = pagerank_centrality(graph)
    assert abs(sum(ranks.values()) - 1.0) < 1e-9
    assert abs(ranks["a"] - 1 / 3) < 1e-6  # symmetric cycle => uniform
