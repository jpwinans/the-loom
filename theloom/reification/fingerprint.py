"""Reusable structural fingerprinting.

Weisfeiler-Leman ego fingerprints over a LoomGraph: every node gets a hash of
its rooted neighborhood up to ``depth`` hops. Nodes sharing a hash form a
structural pattern group. The hashing logic is identical to the copy inlined in
``theloom/operations/reification.py`` (reify-patterns) — this module extracts it
so the entity-proposal engine can reuse it. reify-patterns itself is left
untouched to preserve its established output.

Both functions are pure (no graph mutation, no side effects).
"""

from __future__ import annotations

import hashlib
from typing import Any

from theloom.graph.hydrate import LoomGraph, hydrate_graph

Doc = dict[str, Any]

MAX_DEPTH_LIMIT = 10
DEFAULT_MAX_DEPTH = 2


def _sha16(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def neighborhood_meta(graph: LoomGraph, node_id: str) -> dict[str, list[str]]:
    """Depth-1 metadata: per-edge relation types (duplicates kept) and neighbor
    entity types (deduped by neighbor node id), all sorted."""
    incoming: list[str] = []
    outgoing: list[str] = []
    neighbor_types: list[str] = []
    seen: set[str] = set()
    for edge_id in graph.in_edge_ids(node_id):
        incoming.append(str(graph.edge_docs[edge_id]["relationType"]))
        source = graph.edge_source(edge_id)
        if source not in seen:
            seen.add(source)
            neighbor_types.append(str(graph.node_docs[source]["entityType"]))
    for edge_id in graph.out_edge_ids(node_id):
        outgoing.append(str(graph.edge_docs[edge_id]["relationType"]))
        target = graph.edge_target(edge_id)
        if target not in seen:
            seen.add(target)
            neighbor_types.append(str(graph.node_docs[target]["entityType"]))
    return {
        "incomingRelationTypes": sorted(incoming),
        "outgoingRelationTypes": sorted(outgoing),
        "neighborEntityTypes": sorted(neighbor_types),
    }


def _hash_at_depth(graph: LoomGraph, node_id: str, depth: int, cache: dict[str, str]) -> str:
    key = f"{node_id}:{depth}"
    if key in cache:
        return cache[key]
    entity_type = str(graph.node_docs[node_id]["entityType"])
    if depth == 0:
        canonical = entity_type
    elif depth == 1:
        meta = neighborhood_meta(graph, node_id)
        canonical = "|".join(
            [
                entity_type,
                f"in:{','.join(meta['incomingRelationTypes'])}",
                f"out:{','.join(meta['outgoingRelationTypes'])}",
                f"neighbors:{','.join(meta['neighborEntityTypes'])}",
            ]
        )
    else:
        self_hash = _hash_at_depth(graph, node_id, depth - 1, cache)
        neighbor_hashes = sorted(
            _hash_at_depth(graph, neighbor, depth - 1, cache)
            for neighbor in graph.neighbors(node_id)
        )
        canonical = f"{self_hash}|{','.join(neighbor_hashes)}"
    digest = _sha16(canonical)
    cache[key] = digest
    return digest


def compute_fingerprint(
    graph: LoomGraph,
    node_id: str,
    depth: int = 2,
    cache: dict[str, str] | None = None,
) -> str:
    """Structural fingerprint hash for a single node. ``depth`` is clamped to
    [0, MAX_DEPTH_LIMIT]. Pass a shared ``cache`` to memoize across many calls."""
    effective_depth = min(max(depth, 0), MAX_DEPTH_LIMIT)
    effective_cache = cache if cache is not None else {}
    return _hash_at_depth(graph, node_id, effective_depth, effective_cache)


def describe_fingerprint(info: dict[str, Any]) -> str:
    """Deterministic human-readable description of a fingerprint's metadata."""
    incoming = sorted(info["incomingRelationTypes"])
    outgoing = sorted(info["outgoingRelationTypes"])
    neighbors = sorted(info["neighborEntityTypes"])
    if not incoming and not outgoing and not neighbors:
        return f"isolated {info['entityType']}"
    parts = [str(info["entityType"])]
    if incoming:
        parts.append(f"with incoming [{', '.join(incoming)}]")
    if outgoing:
        parts.append(f"{'and' if incoming else 'with'} outgoing [{', '.join(outgoing)}]")
    if neighbors:
        parts.append(f"connected to [{', '.join(neighbors)}]")
    return " ".join(parts)


def group_by_fingerprint(
    entities: list[Doc],
    relations: list[Doc],
    min_occurrences: int = 2,
    max_patterns: int = 20,
    max_depth: int = 2,
) -> list[Doc]:
    """Group entities by structural fingerprint.

    Hydrates ``entities`` + ``relations`` into a working graph, computes every
    node's fingerprint (shared memoization cache), keeps groups with at least
    ``min_occurrences`` members, sorts by (count desc, fingerprint asc) and
    truncates to ``max_patterns``. Group shape:
    ``{fingerprint, description, info, entityIds, count}``."""
    graph = hydrate_graph(entities, relations)
    if graph.order == 0:
        return []

    effective_depth = min(max(max_depth, 0), MAX_DEPTH_LIMIT)
    cache: dict[str, str] = {}
    buckets: dict[str, Doc] = {}
    for node_id in graph.nodes():
        digest = compute_fingerprint(graph, node_id, effective_depth, cache)
        bucket = buckets.get(digest)
        if bucket is None:
            buckets[digest] = {
                "info": {
                    "entityType": graph.node_docs[node_id]["entityType"],
                    **neighborhood_meta(graph, node_id),
                },
                "entityIds": [node_id],
            }
        else:
            bucket["entityIds"].append(node_id)

    groups: list[Doc] = [
        {
            "fingerprint": digest,
            "description": describe_fingerprint(bucket["info"]),
            "info": bucket["info"],
            "entityIds": bucket["entityIds"],
            "count": len(bucket["entityIds"]),
        }
        for digest, bucket in buckets.items()
        if len(bucket["entityIds"]) >= min_occurrences
    ]
    groups.sort(key=lambda g: (-int(g["count"]), str(g["fingerprint"])))
    return groups[:max_patterns]
