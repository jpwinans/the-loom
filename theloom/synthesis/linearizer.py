"""Graph linearization.

Kahn's algorithm over CAUSAL edges only, with a stable descending core-number
re-sort of the queue at every pop; unreached (cyclic) nodes append afterwards
in the same order. Segment key order matters: `raw` format serializes these
dicts verbatim into the output text via JSON.stringify semantics.
"""

from __future__ import annotations

from typing import Any

from theloom.synthesis.links import get_links_for_entity

Doc = dict[str, Any]

ALL_CAUSAL_TYPES = ("causes", "enables", "requires", "inhibits", "amplifies", "dampens")


def topological_sort(
    entities: list[Doc], relations: list[Doc], core_numbers: dict[str, int]
) -> list[str]:
    entity_ids: dict[str, None] = {e["id"]: None for e in entities}
    if not entity_ids:
        return []

    in_degree: dict[str, int] = {eid: 0 for eid in entity_ids}
    out_edges: dict[str, list[str]] = {eid: [] for eid in entity_ids}
    for rel in relations:
        if rel["from"] not in entity_ids or rel["to"] not in entity_ids:
            continue
        if rel["relationType"] in ALL_CAUSAL_TYPES:
            out_edges[rel["from"]].append(rel["to"])
            in_degree[rel["to"]] = in_degree.get(rel["to"], 0) + 1

    result: list[str] = []
    queue = [eid for eid, deg in in_degree.items() if deg == 0]
    while queue:
        queue.sort(key=lambda eid: -(core_numbers.get(eid) or 0))  # stable
        node_id = queue.pop(0)
        result.append(node_id)
        for neighbor in out_edges.get(node_id, []):
            new_deg = in_degree.get(neighbor, 1) - 1
            in_degree[neighbor] = new_deg
            if new_deg == 0:
                queue.append(neighbor)

    result_set = set(result)
    remaining = [eid for eid in entity_ids if eid not in result_set]
    remaining.sort(key=lambda eid: -(core_numbers.get(eid) or 0))
    result.extend(remaining)
    return result


def build_segments(
    sorted_entity_ids: list[str],
    entity_map: dict[str, Doc],
    relations: list[Doc],
    core_numbers: dict[str, int],
) -> list[Doc]:
    entity_set = set(sorted_entity_ids)
    segments: list[Doc] = []
    for entity_id in sorted_entity_ids:
        entity = entity_map.get(entity_id)
        if entity is None:
            continue
        incoming = [r for r in relations if r["to"] == entity_id and r["from"] in entity_set]
        outgoing = [r for r in relations if r["from"] == entity_id and r["to"] in entity_set]
        source_passages = [
            link["evidence"]
            for link in get_links_for_entity(entity_id)
            if len(link.get("evidence", "")) > 0
        ]
        segments.append(
            {
                "entity": entity,
                "incomingRelations": incoming,
                "outgoingRelations": outgoing,
                "sourcePassages": source_passages,
                "coreNumber": core_numbers.get(entity_id, 0),
            }
        )
    return segments


def linearize_graph(
    entities: list[Doc],
    relations: list[Doc],
    core_numbers: dict[str, int],
    format: str,
    region_id: str,
) -> Doc:
    sorted_ids = topological_sort(entities, relations, core_numbers)
    entity_map = {e["id"]: e for e in entities}

    if format == "causal_chain":
        causal_relations = [r for r in relations if r["relationType"] in ALL_CAUSAL_TYPES]
        causal_entity_ids: set[str] = set()
        for r in causal_relations:
            causal_entity_ids.add(r["from"])
            causal_entity_ids.add(r["to"])
        causal_sorted = [eid for eid in sorted_ids if eid in causal_entity_ids]
        segments = build_segments(causal_sorted, entity_map, causal_relations, core_numbers)
    elif format == "evidence_map":
        with_sources = [eid for eid in sorted_ids if get_links_for_entity(eid)]
        without_sources = [eid for eid in sorted_ids if not get_links_for_entity(eid)]
        segments = build_segments(
            [*with_sources, *without_sources], entity_map, relations, core_numbers
        )
    else:
        segments = build_segments(sorted_ids, entity_map, relations, core_numbers)

    return {"segments": segments, "format": format, "regionId": region_id}
