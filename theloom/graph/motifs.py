"""Frequent subgraph mining — exact enumeration-order implementation.

The signature grouping, instance lists, and motif ids all depend on a
canonical enumeration: nodes visited in sorted order, expansion
candidates sorted lexicographically, only-expand-to->=start pruning, LIFO
partial-subgraph stack. Motif instance node ids and edge keys are sorted.
"""

from __future__ import annotations

import time
from typing import Any

from theloom.graph.hydrate import LoomGraph


def _canonicalize(
    graph: LoomGraph,
    node_ids: list[str],
    edge_keys: list[str],
    use_node_types: bool,
    use_edge_types: bool,
) -> dict[str, Any]:
    def node_type(node_id: str) -> str:
        return str(graph.node_docs[node_id]["entityType"]) if use_node_types else "*"

    sorted_node_types = sorted(node_type(nid) for nid in node_ids)
    edge_triples: list[str] = []
    edge_type_labels: list[str] = []
    desc_parts: list[str] = []
    for edge_key in edge_keys:
        source = graph.edge_source(edge_key)
        target = graph.edge_target(edge_key)
        edge_type = str(graph.edge_docs[edge_key]["relationType"]) if use_edge_types else "*"
        edge_triples.append(f"{node_type(source)}:{edge_type}:{node_type(target)}")
        edge_type_labels.append(edge_type)
        desc_parts.append(f"{node_type(source)} -{edge_type}-> {node_type(target)}")
    edge_triples.sort()
    signature = f"N{len(node_ids)}E{len(edge_keys)}|{','.join(sorted_node_types)}|" + "|".join(
        edge_triples
    )
    description = ", ".join(desc_parts) if desc_parts else ", ".join(sorted_node_types)
    return {
        "signature": signature,
        "nodeTypes": sorted_node_types,
        "edgeTypes": sorted(edge_type_labels),
        "description": description,
    }


def _enumerate_subgraphs(
    graph: LoomGraph,
    start_node: str,
    max_size: int,
    node_filter: set[str] | None,
    edge_filter: set[str] | None,
) -> list[tuple[list[str], list[str]]]:
    results: list[tuple[list[str], list[str]]] = []
    if node_filter and str(graph.node_docs[start_node]["entityType"]) not in node_filter:
        return results
    if max_size >= 1:
        results.append(([start_node], []))
    if max_size < 2:
        return results

    stack: list[tuple[set[str], list[str], list[str]]] = [({start_node}, [start_node], [])]
    while stack:
        nodes, node_ids, edge_keys = stack.pop()
        if len(node_ids) >= max_size:
            continue
        expansion: dict[str, list[str]] = {}
        for nid in node_ids:
            for edge_key in graph.node_edges(nid):
                source = graph.edge_source(edge_key)
                target = graph.edge_target(edge_key)
                neighbor = target if source == nid else source
                if neighbor in nodes:
                    continue
                if (
                    edge_filter
                    and str(graph.edge_docs[edge_key]["relationType"]) not in edge_filter
                ):
                    continue
                if node_filter and str(graph.node_docs[neighbor]["entityType"]) not in node_filter:
                    continue
                if neighbor < start_node:
                    continue
                expansion.setdefault(neighbor, []).append(edge_key)
        for neighbor in sorted(expansion):
            connections = expansion[neighbor]
            new_nodes = {*nodes, neighbor}
            new_node_ids = sorted([*node_ids, neighbor])
            new_edge_keys = sorted({*edge_keys, *connections})
            results.append((new_node_ids, new_edge_keys))
            if len(new_node_ids) < max_size:
                stack.append((new_nodes, new_node_ids, new_edge_keys))
    return results


def find_frequent_subgraphs(
    graph: LoomGraph,
    frequency_threshold: int,
    max_motif_size: int,
    use_node_types: bool,
    use_edge_types: bool,
    node_type_filter: list[str] | None,
    edge_type_filter: list[str] | None,
    timeout_ms: int,
    max_instances: int,
) -> dict[str, Any]:
    node_filter = set(node_type_filter) if node_type_filter else None
    edge_filter = set(edge_type_filter) if edge_type_filter else None

    signature_map: dict[str, dict[str, Any]] = {}
    start = time.monotonic()
    truncated = False

    for node in sorted(graph.nodes()):
        if (time.monotonic() - start) * 1000 > timeout_ms:
            truncated = True
            break
        for node_ids, edge_keys in _enumerate_subgraphs(
            graph, node, max_motif_size, node_filter, edge_filter
        ):
            if (time.monotonic() - start) * 1000 > timeout_ms:
                truncated = True
                break
            if len(node_ids) == 1 and not edge_keys:
                continue
            canon = _canonicalize(graph, node_ids, edge_keys, use_node_types, use_edge_types)
            existing = signature_map.get(canon["signature"])
            instance = {"entityIds": node_ids, "relationIds": edge_keys}
            if existing:
                existing["totalCount"] += 1
                if len(existing["instances"]) < max_instances:
                    existing["instances"].append(instance)
            else:
                signature_map[canon["signature"]] = {
                    "nodeTypes": canon["nodeTypes"],
                    "edgeTypes": canon["edgeTypes"],
                    "description": canon["description"],
                    "nodeCount": len(node_ids),
                    "edgeCount": len(edge_keys),
                    "instances": [instance],
                    "totalCount": 1,
                }
        if truncated:
            break

    motifs = [
        {
            "patternId": "",
            "occurrenceCount": data["totalCount"],
            "patternDescription": data["description"],
            "nodeCount": data["nodeCount"],
            "edgeCount": data["edgeCount"],
            "nodeTypes": data["nodeTypes"],
            "edgeTypes": data["edgeTypes"],
            "instances": data["instances"],
        }
        for data in signature_map.values()
        if data["totalCount"] >= frequency_threshold
    ]
    motifs.sort(key=lambda m: -int(m["occurrenceCount"]))
    for i, motif in enumerate(motifs):
        motif["patternId"] = f"motif-{i}"

    return {"motifs": motifs, "truncated": truncated}
