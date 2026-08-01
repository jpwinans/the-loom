"""Approximate subgraph matching.

Documented decision: this is deliberately *approximate* matching, NOT
rustworkx VF2. Type constraints are scored (not filtered), missing edges are
penalized by a topology score, and duplicate entity-sets are collapsed keeping
the FIRST mapping found — so exact isomorphism would change observable
behavior. Search order (constraint-count
node ordering, matching-type-first candidates in entity insertion order,
recursive backtracking) is part of the contract because dedup keeps the first
mapping encountered.
"""

from __future__ import annotations

import time
from typing import Any

from theloom.graph.hydrate import Doc, LoomGraph, hydrate_graph

MAX_PATTERN_NODES = 20
MAX_PATTERN_EDGES = 50
MAX_RESULTS_DEFAULT = 20
MAX_RESULTS_LIMIT = 100
DEFAULT_TIMEOUT_MS = 5000
DEFAULT_MIN_SIMILARITY = 0.5
DEFAULT_NODE_TYPE_WEIGHT = 0.4
DEFAULT_EDGE_TYPE_WEIGHT = 0.3
DEFAULT_TOPOLOGY_WEIGHT = 0.3


def validate_pattern(pattern: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    nodes = pattern.get("nodes") or []
    if not nodes:
        errors.append("Pattern must have at least one node")
        return errors
    if len(nodes) > MAX_PATTERN_NODES:
        errors.append(f"Pattern has {len(nodes)} nodes, maximum is {MAX_PATTERN_NODES}")
    node_ids: set[str] = set()
    for node in nodes:
        if node["id"] in node_ids:
            errors.append(f"Duplicate pattern node ID: '{node['id']}'")
        node_ids.add(node["id"])
    edges = pattern.get("edges") or []
    if edges:
        if len(edges) > MAX_PATTERN_EDGES:
            errors.append(f"Pattern has {len(edges)} edges, maximum is {MAX_PATTERN_EDGES}")
        for edge in edges:
            if edge["from"] not in node_ids:
                errors.append(f"Edge references non-existent pattern node '{edge['from']}'")
            if edge["to"] not in node_ids:
                errors.append(f"Edge references non-existent pattern node '{edge['to']}'")
            if edge["from"] == edge["to"]:
                errors.append(f"Self-loop edge not allowed: '{edge['from']}' -> '{edge['to']}'")
    return errors


def _node_type_score(
    pattern: dict[str, Any], mapping: dict[str, str], entity_map: dict[str, Doc]
) -> float:
    constrained = [n for n in pattern["nodes"] if n.get("entityType") is not None]
    if not constrained:
        return 1.0
    matched = 0
    for p_node in constrained:
        entity_id = mapping.get(p_node["id"])
        if not entity_id:
            continue
        entity = entity_map.get(entity_id)
        if entity and entity["entityType"] == p_node["entityType"]:
            matched += 1
    return matched / len(constrained)


def _edge_type_score(
    pattern: dict[str, Any],
    mapping: dict[str, str],
    relation_index: dict[str, list[Doc]],
) -> float:
    constrained = [e for e in pattern.get("edges") or [] if e.get("relationType") is not None]
    if not constrained:
        return 1.0
    matched = 0
    for p_edge in constrained:
        from_entity = mapping.get(p_edge["from"])
        to_entity = mapping.get(p_edge["to"])
        if not from_entity or not to_entity:
            continue
        rels = relation_index.get(f"{from_entity}->{to_entity}", [])
        if any(r["relationType"] == p_edge["relationType"] for r in rels):
            matched += 1
    return matched / len(constrained)


def _topology_score(pattern: dict[str, Any], mapping: dict[str, str], graph: LoomGraph) -> float:
    edges = pattern.get("edges") or []
    if not edges:
        return 1.0
    matched = 0
    for p_edge in edges:
        from_entity = mapping.get(p_edge["from"])
        to_entity = mapping.get(p_edge["to"])
        if not from_entity or not to_entity:
            continue
        # A two-node adjacency query is any-direction, so the topology score
        # counts reverse edges too.
        if (
            graph.has_node(from_entity)
            and graph.has_node(to_entity)
            and graph.has_any_edge(from_entity, to_entity)
        ):
            matched += 1
    return matched / len(edges)


def find_subgraph_matches(
    entities: list[Doc],
    relations: list[Doc],
    pattern: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    opts = options or {}
    resolved = {
        "nodeTypeWeight": opts.get("nodeTypeWeight", DEFAULT_NODE_TYPE_WEIGHT),
        "edgeTypeWeight": opts.get("edgeTypeWeight", DEFAULT_EDGE_TYPE_WEIGHT),
        "topologyWeight": opts.get("topologyWeight", DEFAULT_TOPOLOGY_WEIGHT),
        "minSimilarity": opts.get("minSimilarity", DEFAULT_MIN_SIMILARITY),
        "maxResults": min(opts.get("maxResults", MAX_RESULTS_DEFAULT), MAX_RESULTS_LIMIT),
        "timeoutMs": opts.get("timeoutMs", DEFAULT_TIMEOUT_MS),
    }

    errors = validate_pattern(pattern)
    if errors:
        raise ValueError(f"Invalid subgraph pattern: {'; '.join(errors)}")

    entity_map: dict[str, Doc] = {e["id"]: e for e in entities}
    relation_index: dict[str, list[Doc]] = {}
    for relation in relations:
        relation_index.setdefault(f"{relation['from']}->{relation['to']}", []).append(relation)
    graph = hydrate_graph(entities, relations)

    edges = pattern.get("edges") or []
    has_edges = len(edges) > 0

    # Order pattern nodes: constrained-by-fewest-candidates first (stable).
    counts: list[tuple[str, int]] = []
    for p_node in pattern["nodes"]:
        if p_node.get("entityType"):
            count = sum(1 for e in entity_map.values() if e["entityType"] == p_node["entityType"])
            counts.append((p_node["id"], count))
        else:
            counts.append((p_node["id"], len(entity_map)))
    ordered_node_ids = [c[0] for c in sorted(counts, key=lambda c: c[1])]
    pattern_node_map = {n["id"]: n for n in pattern["nodes"]}

    def candidates_for(p_node: dict[str, Any]) -> list[str]:
        if not p_node.get("entityType"):
            return list(entity_map)
        matching = [i for i, e in entity_map.items() if e["entityType"] == p_node["entityType"]]
        non_matching = [i for i, e in entity_map.items() if e["entityType"] != p_node["entityType"]]
        return [*matching, *non_matching]

    def is_consistent(pattern_node_id: str, candidate_id: str, mapping: dict[str, str]) -> bool:
        if candidate_id in mapping.values():
            return False
        for edge in edges:
            if edge["from"] == pattern_node_id and edge["to"] in mapping:
                mapped_to = mapping[edge["to"]]
                if (
                    graph.has_node(candidate_id)
                    and graph.has_node(mapped_to)
                    and not graph.has_any_edge(candidate_id, mapped_to)
                ):
                    return False
            if edge["to"] == pattern_node_id and edge["from"] in mapping:
                mapped_from = mapping[edge["from"]]
                if (
                    graph.has_node(candidate_id)
                    and graph.has_node(mapped_from)
                    and not graph.has_any_edge(mapped_from, candidate_id)
                ):
                    return False
        return True

    matches: list[dict[str, Any]] = []
    seen_mappings: set[str] = set()
    start = time.monotonic()

    def timed_out() -> bool:
        return bool((time.monotonic() - start) * 1000 > resolved["timeoutMs"])

    def backtrack(depth: int, mapping: dict[str, str]) -> None:
        if timed_out():
            return
        if depth == len(ordered_node_ids):
            key = ",".join(sorted(mapping.values()))
            if key in seen_mappings:
                return
            seen_mappings.add(key)
            breakdown = {
                "nodeTypeScore": _node_type_score(pattern, mapping, entity_map),
                "edgeTypeScore": _edge_type_score(pattern, mapping, relation_index),
                "topologyScore": _topology_score(pattern, mapping, graph),
            }
            total_weight = (
                resolved["nodeTypeWeight"] + resolved["edgeTypeWeight"] + resolved["topologyWeight"]
            )
            score = (
                (
                    breakdown["nodeTypeScore"] * resolved["nodeTypeWeight"]
                    + breakdown["edgeTypeScore"] * resolved["edgeTypeWeight"]
                    + breakdown["topologyScore"] * resolved["topologyWeight"]
                )
                / total_weight
                if total_weight
                else 0.0
            )
            if score < resolved["minSimilarity"]:
                return
            relation_ids: dict[str, None] = {}
            for edge in edges:
                from_entity = mapping.get(edge["from"])
                to_entity = mapping.get(edge["to"])
                if not from_entity or not to_entity:
                    continue
                for rel in relation_index.get(f"{from_entity}->{to_entity}", []):
                    if not edge.get("relationType") or rel["relationType"] == edge["relationType"]:
                        relation_ids.setdefault(rel["id"])
            matches.append(
                {
                    "score": score,
                    "nodeMapping": dict(mapping),
                    "entityIds": list(dict.fromkeys(mapping.values())),
                    "relationIds": list(relation_ids),
                    "scoreBreakdown": breakdown,
                }
            )
            return

        pattern_node_id = ordered_node_ids[depth]
        p_node = pattern_node_map[pattern_node_id]
        for candidate_id in candidates_for(p_node):
            if timed_out():
                return
            consistent = (
                is_consistent(pattern_node_id, candidate_id, mapping)
                if has_edges
                else (pattern_node_id not in mapping and candidate_id not in mapping.values())
            )
            if consistent:
                mapping[pattern_node_id] = candidate_id
                backtrack(depth + 1, mapping)
                del mapping[pattern_node_id]

    backtrack(0, {})
    matches.sort(key=lambda m: -float(m["score"]))
    truncated = len(matches) > resolved["maxResults"]
    final = matches[: resolved["maxResults"]]
    return {
        "matches": final,
        "matchCount": len(final),
        "truncated": truncated,
        "pattern": pattern,
        "options": resolved,
    }
