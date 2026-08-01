"""Semirings, weight extractors, and the traversal engine.

The traversal is a DFS with per-path backtracking, NOT Bellman-Ford: results
accumulate via semiring.plus while the stored path is replaced only when the
new single-path value strictly wins (plus(new, best) == new and new != best).
Ties keep the first-discovered path, so adjacency iteration order (the relations
array / store edge order) is part of the contract.

python-graphblas backs the closure/value mathematics conceptually (STACK.md);
the five semirings here use fixed, explicit operator tables because paths —
with their tie-breaking — are output-visible.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from theloom.store.falkor import FalkorGraphStore

Doc = dict[str, Any]
INF = float("inf")

DEFAULT_MAX_DEPTH = 10

TROPICAL_STRENGTH_MAP = {"foundational": 0.5, "strong": 1, "moderate": 2, "weak": 3}
VITERBI_STRENGTH_MAP = {"foundational": 0.95, "strong": 0.9, "moderate": 0.7, "weak": 0.4}
CAPACITY_STRENGTH_MAP = {"foundational": 4, "strong": 3, "moderate": 2, "weak": 1}


@dataclass(frozen=True)
class Semiring:
    name: str
    zero: Any
    one: Any
    plus: Callable[[Any, Any], Any]
    times: Callable[[Any, Any], Any]


BOOLEAN = Semiring("boolean", False, True, lambda a, b: a or b, lambda a, b: a and b)
TROPICAL = Semiring("tropical", INF, 0, min, lambda a, b: a + b)
VITERBI = Semiring("viterbi", 0, 1, max, lambda a, b: a * b)
COUNTING = Semiring("counting", 0, 1, lambda a, b: a + b, lambda a, b: a * b)
CAPACITY = Semiring("capacity", 0, INF, max, min)

Extractor = Callable[[Doc], Any]


def boolean_extractor(_: Doc) -> bool:
    return True


def tropical_extractor(relation: Doc) -> float:
    return float(TROPICAL_STRENGTH_MAP.get(relation.get("strength", ""), 2))


def uniform_tropical_extractor(_: Doc) -> float:
    return 1.0


def viterbi_extractor(relation: Doc) -> float:
    return float(VITERBI_STRENGTH_MAP.get(relation.get("strength", ""), 0.5))


def counting_extractor(_: Doc) -> float:
    return 1.0


def capacity_extractor(relation: Doc) -> float:
    return float(CAPACITY_STRENGTH_MAP.get(relation.get("strength", ""), 1))


# resolve_semiring throws on unknown semiring names.
OPERATION_SEMIRINGS: dict[str, tuple[Semiring, Extractor]] = {
    "boolean": (BOOLEAN, boolean_extractor),
    "tropical": (TROPICAL, tropical_extractor),
    "tropical-uniform": (TROPICAL, uniform_tropical_extractor),
    "viterbi": (VITERBI, viterbi_extractor),
    "counting": (COUNTING, counting_extractor),
    "capacity": (CAPACITY, capacity_extractor),
}


def resolve_semiring(name: str) -> tuple[Semiring, Extractor]:
    resolved = OPERATION_SEMIRINGS.get(name)
    if resolved is None:
        raise ValueError(
            f"Unknown semiring: {name}. Valid options: boolean, tropical, "
            "tropical-uniform, viterbi, counting, capacity"
        )
    return resolved


def resolve_semiring_router(name: str | None) -> tuple[Semiring, Extractor]:
    """Router-side semiring resolution — tropical-uniform gets the PLAIN
    tropical extractor here, and unknown/counting/capacity fall back to
    Tropical (no throw). An intentional quirk, kept."""
    if name == "boolean":
        return BOOLEAN, boolean_extractor
    if name == "viterbi":
        return VITERBI, viterbi_extractor
    if name in ("tropical", "tropical-uniform"):
        return TROPICAL, tropical_extractor
    return TROPICAL, tropical_extractor


# =============================================================================
# The traversal engine
# =============================================================================


def _build_adjacency(relations: list[Doc], direction: str) -> dict[str, list[Doc]]:
    adjacency: dict[str, list[Doc]] = {}
    for rel in relations:
        if direction in ("outgoing", "both"):
            adjacency.setdefault(rel["from"], []).append(rel)
        if direction in ("incoming", "both"):
            reversed_rel = {**rel, "from": rel["to"], "to": rel["from"]}
            adjacency.setdefault(rel["to"], []).append(reversed_rel)
    return adjacency


def _store_adjacency(store: FalkorGraphStore, node_id: str, direction: str) -> list[Doc]:
    """Lazy adjacency (lazySingleSourceTraversal): per-node store reads, with
    incoming edges reversed; 'both' excludes reversed self-loops.

    For 'both', edges are emitted as `[...natural, ...reversed]` — every natural
    (from==node) edge first, then every reversed incoming edge — NOT interleaved
    in storage order. This grouping is load-bearing: it fixes the DFS visitation
    order, which is the stable-sort tie order among equal-distance results.
    """
    docs = [
        r.model_dump(by_alias=True, exclude_unset=True)
        for r in store.get_relations(node_id, direction)  # type: ignore[arg-type]
    ]
    if direction == "outgoing":
        return docs
    if direction == "incoming":
        return [{**rel, "from": rel["to"], "to": rel["from"]} for rel in docs]
    # both: all naturals first, then all reversed incomings (self-loops excluded).
    natural = [rel for rel in docs if rel["from"] == node_id]
    reversed_incoming = [
        {**rel, "from": rel["to"], "to": rel["from"]}
        for rel in docs
        if rel["to"] == node_id and rel["from"] != node_id
    ]
    return [*natural, *reversed_incoming]


def _traverse(
    adjacency_of: Callable[[str], list[Doc]],
    source_id: str,
    semiring: Semiring,
    extractor: Extractor,
    mode: str,
    max_depth: int,
    relation_types: set[str] | None,
) -> dict[str, Doc]:
    results: dict[str, Doc] = {source_id: {"value": semiring.one, "path": []}}
    best_single: dict[str, Any] = {}

    def dfs(
        node: str,
        value: Any,
        path: list[Doc],
        visited_nodes: frozenset[str],
        visited_edges: frozenset[str],
        depth: int,
    ) -> None:
        if depth >= max_depth:
            return
        for rel in adjacency_of(node):
            if relation_types is not None and rel["relationType"] not in relation_types:
                continue
            target = rel["to"]
            if mode == "TRAIL":
                if rel["id"] in visited_edges:
                    continue
            elif mode in ("ACYCLIC", "SIMPLE") and target in visited_nodes:
                continue
            edge_weight = extractor(rel)
            new_value = semiring.times(value, edge_weight)
            step = {
                "from": node,
                "to": target,
                "relationId": rel["id"],
                "relationType": rel["relationType"],
            }
            new_path = [*path, step]
            existing = results.get(target)
            if existing is not None:
                combined = semiring.plus(existing["value"], new_value)
                if target in best_single:
                    current_best = best_single[target]
                    preferred = semiring.plus(new_value, current_best)
                    new_is_better = preferred == new_value and new_value != current_best
                else:
                    # Only the seeded source lacks a best value. JS computes
                    # plus(x, undefined): NaN for numeric semirings (never
                    # better); boolean `x || undefined` replaces when truthy.
                    new_is_better = semiring.name == "boolean" and new_value is True
                if new_is_better:
                    results[target] = {"value": combined, "path": new_path}
                    best_single[target] = new_value
                else:
                    results[target] = {"value": combined, "path": existing["path"]}
            else:
                results[target] = {"value": new_value, "path": new_path}
                best_single[target] = new_value
            dfs(
                target,
                new_value,
                new_path,
                visited_nodes | {target},
                visited_edges | {rel["id"]},
                depth + 1,
            )

    dfs(source_id, semiring.one, [], frozenset({source_id}), frozenset(), 0)
    return results


def lazy_single_source(
    store: FalkorGraphStore,
    source_id: str,
    semiring: Semiring,
    extractor: Extractor,
    mode: str = "ACYCLIC",
    max_depth: int = DEFAULT_MAX_DEPTH,
    relation_types: list[str] | None = None,
    direction: str = "outgoing",
) -> dict[str, Doc]:
    if store.read_entity(source_id) is None:
        return {}
    cache: dict[str, list[Doc]] = {}

    def adjacency_of(node: str) -> list[Doc]:
        if node not in cache:
            cache[node] = _store_adjacency(store, node, direction)
        return cache[node]

    return _traverse(
        adjacency_of,
        source_id,
        semiring,
        extractor,
        mode,
        max_depth,
        set(relation_types) if relation_types else None,
    )


def single_source(
    entities: list[Doc],
    relations: list[Doc],
    source_id: str,
    semiring: Semiring,
    extractor: Extractor,
    mode: str = "ACYCLIC",
    max_depth: int = DEFAULT_MAX_DEPTH,
    relation_types: list[str] | None = None,
    direction: str = "outgoing",
) -> dict[str, Doc]:
    if not any(e["id"] == source_id for e in entities):
        return {}
    adjacency = _build_adjacency(relations, direction)
    return _traverse(
        lambda node: adjacency.get(node, []),
        source_id,
        semiring,
        extractor,
        mode,
        max_depth,
        set(relation_types) if relation_types else None,
    )
