"""Subgraph extraction — pure filters."""

from __future__ import annotations

from typing import Any

from theloom.graph.hydrate import Doc
from theloom.model import CAUSAL_RELATION_TYPES

_CAUSAL = {t.value for t in CAUSAL_RELATION_TYPES}


def extract_causal_subgraph(
    entities: list[Doc], relations: list[Doc]
) -> tuple[list[Doc], list[Doc]]:
    causal_relations = [r for r in relations if r["relationType"] in _CAUSAL]
    causal_ids: set[str] = set()
    for relation in causal_relations:
        causal_ids.add(relation["from"])
        causal_ids.add(relation["to"])
    return [e for e in entities if e["id"] in causal_ids], causal_relations


def extract_ego_subgraph(
    entities: list[Doc], relations: list[Doc], center_id: str, depth: int = 1
) -> tuple[list[Doc], list[Doc]] | None:
    if not any(e["id"] == center_id for e in entities):
        return None
    visited = {center_id}
    relation_ids: set[str] = set()
    current_level = {center_id}
    for _ in range(depth):
        next_level: set[str] = set()
        for entity_id in current_level:
            for relation in relations:
                if entity_id in (relation["from"], relation["to"]):
                    relation_ids.add(relation["id"])
                    neighbor = relation["to"] if relation["from"] == entity_id else relation["from"]
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_level.add(neighbor)
        current_level = next_level
    return (
        [e for e in entities if e["id"] in visited],
        [r for r in relations if r["id"] in relation_ids],
    )


def extract_typed_subgraph(
    entities: list[Doc],
    relations: list[Doc],
    entity_type: str | None,
    relation_type: str | None,
) -> tuple[list[Doc], list[Doc]]:
    if relation_type and not entity_type:
        filtered_relations = [r for r in relations if r["relationType"] == relation_type]
        referenced: set[str] = set()
        for relation in filtered_relations:
            referenced.add(relation["from"])
            referenced.add(relation["to"])
        return [e for e in entities if e["id"] in referenced], filtered_relations

    filtered_entities = (
        [e for e in entities if e["entityType"] == entity_type] if entity_type else list(entities)
    )
    entity_ids = {e["id"] for e in filtered_entities}
    if relation_type:
        filtered_relations = [
            r
            for r in relations
            if r["relationType"] == relation_type
            and r["from"] in entity_ids
            and r["to"] in entity_ids
        ]
    else:
        filtered_relations = [
            r for r in relations if r["from"] in entity_ids and r["to"] in entity_ids
        ]
    return filtered_entities, filtered_relations


def format_subgraph_output(
    entities: list[Doc], relations: list[Doc], output_mode: str = "full"
) -> dict[str, Any]:
    if output_mode == "lightweight":
        return {
            "entities": [
                {"id": e["id"], "name": e["name"], "entityType": e["entityType"]} for e in entities
            ],
            "relations": [
                {
                    "from": r["from"],
                    "to": r["to"],
                    "relationType": r["relationType"],
                    "polarity": r.get("polarity"),
                    "strength": r.get("strength"),
                }
                for r in relations
            ],
        }
    if output_mode == "stats":
        entities_by_type: dict[str, int] = {}
        for entity in entities:
            entities_by_type[entity["entityType"]] = (
                entities_by_type.get(entity["entityType"], 0) + 1
            )
        relations_by_type: dict[str, int] = {}
        for relation in relations:
            relations_by_type[relation["relationType"]] = (
                relations_by_type.get(relation["relationType"], 0) + 1
            )
        return {
            "entity_count": len(entities),
            "relation_count": len(relations),
            "entities_by_type": entities_by_type,
            "relations_by_type": relations_by_type,
        }
    return {"entities": entities, "relations": relations}
