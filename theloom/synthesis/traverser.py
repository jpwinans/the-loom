"""Synthesis traversal.

Adaptive mode's priority queue is a sorted array: re-sort
by descending confidence every pop (stable — FIFO among equals), `visited`
shared ACROSS regions, and the visit reason embeds the accumulated confidence
formatted with JS `toFixed(3)` semantics (round-half-up on the exact double).
Dedup keeps the first evidence unit per entity, merging passages/relations.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from theloom.synthesis.links import get_source_passages
from theloom.synthesis.selector import DocStore
from theloom.timeutil import iso_now

Doc = dict[str, Any]

VITERBI_STRENGTH_MAP = {"foundational": 0.95, "strong": 0.9, "moderate": 0.7, "weak": 0.4}


def viterbi_weight(relation: Doc) -> float:
    return float(VITERBI_STRENGTH_MAP.get(relation.get("strength") or "", 0.5))


def _to_fixed_3(value: float) -> str:
    """JS Number.prototype.toFixed(3): quantize the exact binary double,
    ties round away from zero (positive inputs here)."""
    return str(Decimal(value).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def compute_viterbi_confidence(entity: Doc, relations: list[Doc]) -> float:
    confidence_field = entity.get("confidence") or {}
    confidence = float(confidence_field.get("score", 1.0))
    for rel in relations:
        confidence = confidence * viterbi_weight(rel)
    return confidence


class ProvenanceCollector:
    def __init__(self, mode: str) -> None:
        self._steps: list[Doc] = []
        self._started_at = iso_now()
        self._mode = mode
        self._entity_count = 0
        self._relation_count = 0
        self._finalized = False

    def add_step(
        self, action: str, entity_id: str | None, relation_id: str | None, reason: str
    ) -> None:
        if self._finalized:
            raise RuntimeError("ProvenanceCollector already finalized")
        self._steps.append(
            {
                "timestamp": iso_now(),
                "action": action,
                "entityId": entity_id,
                "relationId": relation_id,
                "reason": reason,
            }
        )

    def visit_entity(self, entity_id: str, reason: str) -> None:
        self.add_step("visit_entity", entity_id, None, reason)
        self._entity_count += 1

    def traverse_relation(self, relation_id: str, reason: str) -> None:
        self.add_step("traverse_relation", None, relation_id, reason)
        self._relation_count += 1

    def skip_entity(self, entity_id: str, reason: str) -> None:
        self.add_step("skip_entity", entity_id, None, reason)

    def finalize(self) -> Doc:
        self._finalized = True
        return {
            "steps": list(self._steps),
            "startedAt": self._started_at,
            "completedAt": iso_now(),
            "traversalMode": self._mode,
            "entityCount": self._entity_count,
            "relationCount": self._relation_count,
        }


def _region_adjacency(region: Doc, relation_map: dict[str, Doc]) -> dict[str, list[str]]:
    adjacency: dict[str, list[str]] = {}
    for rel_id in region["relationIds"]:
        rel = relation_map.get(rel_id)
        if rel is not None:
            adjacency.setdefault(rel["from"], []).append(rel_id)
            adjacency.setdefault(rel["to"], []).append(rel_id)
    return adjacency


def _systematic(
    plan: Doc,
    entity_map: dict[str, Doc],
    relation_map: dict[str, Doc],
    provenance: ProvenanceCollector,
) -> list[Doc]:
    evidence_units: list[Doc] = []
    for region in plan["regions"]:
        provenance.add_step("enter_region", None, None, f"Entering region {region['id']}")
        adjacency = _region_adjacency(region, relation_map)
        for entity_id in region["entityIds"]:
            entity = entity_map.get(entity_id)
            if entity is None:
                provenance.skip_entity(entity_id, "Entity not found in store")
                continue
            provenance.visit_entity(entity_id, "Systematic visit in region order")
            entity_relations = [
                relation_map[rid] for rid in adjacency.get(entity_id, []) if rid in relation_map
            ]
            for rel in entity_relations:
                provenance.traverse_relation(
                    rel["id"],
                    f"Relation {rel['relationType']} from {rel['from']} to {rel['to']}",
                )
            evidence_units.append(
                {
                    "entityId": entity_id,
                    "entity": entity,
                    "relations": entity_relations,
                    "viterbiConfidence": compute_viterbi_confidence(entity, entity_relations),
                    "sourcePassages": get_source_passages(entity_id),
                    "regionId": region["id"],
                }
            )
    return evidence_units


def _adaptive(
    plan: Doc,
    entity_map: dict[str, Doc],
    relation_map: dict[str, Doc],
    provenance: ProvenanceCollector,
) -> list[Doc]:
    evidence_units: list[Doc] = []
    visited: set[str] = set()
    for region in plan["regions"]:
        provenance.add_step(
            "enter_region", None, None, f"Entering region {region['id']} (adaptive)"
        )
        region_entity_set = set(region["entityIds"])
        adjacency = _region_adjacency(region, relation_map)
        queue: list[Doc] = [{"entityId": region["centerEntityId"], "confidence": 1.0}]

        while queue:
            queue.sort(key=lambda item: -item["confidence"])  # stable, like JS sort
            head = queue.pop(0)
            entity_id, confidence = head["entityId"], head["confidence"]
            if entity_id in visited or entity_id not in region_entity_set:
                continue
            visited.add(entity_id)
            entity = entity_map.get(entity_id)
            if entity is None:
                provenance.skip_entity(entity_id, "Entity not found")
                continue
            provenance.visit_entity(
                entity_id, f"Adaptive visit (confidence: {_to_fixed_3(confidence)})"
            )
            entity_relations = [
                relation_map[rid] for rid in adjacency.get(entity_id, []) if rid in relation_map
            ]
            for rel in entity_relations:
                provenance.traverse_relation(
                    rel["id"], f"Following {rel['relationType']} (adaptive)"
                )
                neighbor = rel["to"] if rel["from"] == entity_id else rel["from"]
                if neighbor not in visited:
                    queue.append(
                        {"entityId": neighbor, "confidence": confidence * viterbi_weight(rel)}
                    )
            evidence_units.append(
                {
                    "entityId": entity_id,
                    "entity": entity,
                    "relations": entity_relations,
                    "viterbiConfidence": compute_viterbi_confidence(entity, entity_relations),
                    "sourcePassages": get_source_passages(entity_id),
                    "regionId": region["id"],
                }
            )
    return evidence_units


def _deduplicate(units: list[Doc]) -> list[Doc]:
    seen: dict[str, Doc] = {}
    for unit in units:
        existing = seen.get(unit["entityId"])
        if existing is not None:
            merged_passages = list(
                dict.fromkeys([*existing["sourcePassages"], *unit["sourcePassages"]])
            )
            existing_rel_ids = {r["id"] for r in existing["relations"]}
            merged_relations = [
                *existing["relations"],
                *[r for r in unit["relations"] if r["id"] not in existing_rel_ids],
            ]
            seen[unit["entityId"]] = {
                **existing,
                "sourcePassages": merged_passages,
                "relations": merged_relations,
            }
        else:
            seen[unit["entityId"]] = unit
    return list(seen.values())


def traverse_synthesis(plan: Doc, store: DocStore, mode: str | None = None) -> Doc:
    mode = mode or "systematic"
    provenance = ProvenanceCollector(mode)

    all_entities = store.list_entities()
    all_relations = store.list_relations()
    planned_entity_ids = {eid for region in plan["regions"] for eid in region["entityIds"]}
    planned_relation_ids = {rid for region in plan["regions"] for rid in region["relationIds"]}
    entity_map = {e["id"]: e for e in all_entities if e["id"] in planned_entity_ids}
    relation_map = {r["id"]: r for r in all_relations if r["id"] in planned_relation_ids}

    if mode == "systematic":
        evidence_units = _systematic(plan, entity_map, relation_map, provenance)
    else:
        evidence_units = _adaptive(plan, entity_map, relation_map, provenance)

    return {
        "evidenceUnits": _deduplicate(evidence_units),
        "provenance": provenance.finalize(),
        "regionOrder": [region["id"] for region in plan["regions"]],
    }
