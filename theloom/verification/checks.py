"""Verification guards, invariants, GraphSpec, capabilities, and AC-3
propagation over wire docs.

Guards here operate on entity/relation wire docs (the list-guard-violations /
check-consistency surface), distinct from the mutation-gate helpers in
guards.py. Violation messages, invariant logic, and the AC-3 worklist (LIFO)
are all deterministic so output is stable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from theloom.model import ALL_ENTITY_TYPES, CAUSAL_RELATION_TYPES

Doc = dict[str, Any]

_CAUSAL_NAMES = [t.value for t in CAUSAL_RELATION_TYPES]
_ENTITY_TYPE_NAMES = [t.value for t in ALL_ENTITY_TYPES]


def _effective_status(entity: Doc) -> str:
    return entity.get("status") or "active"


# =============================================================================
# Guards (operate on {entity}/{relation} contexts; store optional)
# =============================================================================


def guard_confidence_bounds(entity: Doc, store: Any = None) -> list[Doc]:
    confidence = entity.get("confidence")
    if not confidence:
        return []
    score = confidence.get("score")
    if score is None:
        return []
    if score != score or score < 0 or score > 1:
        return [
            {
                "code": "CONFIDENCE_OUT_OF_BOUNDS",
                "message": f"Confidence score must be between 0 and 1, got: {score}",
                "severity": "error",
                "path": "confidence.score",
            }
        ]
    return []


def guard_entity_type(entity: Doc, store: Any = None) -> list[Doc]:
    et = entity.get("entityType")
    if et is None:
        return []
    if et not in _ENTITY_TYPE_NAMES:
        return [
            {
                "code": "INVALID_ENTITY_TYPE",
                "message": (
                    f"Invalid entity type '{et}', must be one of: {', '.join(_ENTITY_TYPE_NAMES)}"
                ),
                "severity": "error",
                "path": "entityType",
            }
        ]
    return []


def guard_observations_required(entity: Doc, store: Any = None) -> list[Doc]:
    observations = entity.get("observations")
    if observations is None:
        return []
    if not isinstance(observations, list) or len(observations) == 0:
        return [
            {
                "code": "OBSERVATIONS_REQUIRED",
                "message": "Entity must have at least one observation",
                "severity": "warning",
                "path": "observations",
            }
        ]
    return []


def guard_causal_polarity(relation: Doc, store: Any = None) -> list[Doc]:
    relation_type = relation.get("relationType")
    if not relation_type or relation_type not in _CAUSAL_NAMES:
        return []
    polarity = relation.get("polarity")
    if polarity not in ("+", "-"):
        shown = "undefined" if polarity is None else str(polarity)
        return [
            {
                "code": "CAUSAL_MISSING_POLARITY",
                "message": (
                    f"Causal relation type '{relation_type}' requires polarity "
                    f"('+' or '-'), got: {shown}"
                ),
                "severity": "error",
                "path": "polarity",
            }
        ]
    return []


def guard_no_self_loop(relation: Doc, store: Any = None) -> list[Doc]:
    from_id, to_id = relation.get("from"), relation.get("to")
    if from_id is None or to_id is None:
        return []
    if from_id == to_id:
        return [
            {
                "code": "SELF_LOOP",
                "message": (
                    f"Relation cannot reference the same entity as source and target: '{from_id}'"
                ),
                "severity": "error",
                "path": "from",
            }
        ]
    return []


def guard_no_duplicate_relation(relation: Doc, store: Any = None) -> list[Doc]:
    from_id, to_id = relation.get("from"), relation.get("to")
    if store is None or from_id is None or to_id is None:
        return []
    if store.read_relation(from_id, to_id) is not None:
        return [
            {
                "code": "DUPLICATE_RELATION",
                "message": f"A relation already exists from '{from_id}' to '{to_id}'",
                "severity": "error",
                "path": "from",
            }
        ]
    return []


ENTITY_GUARDS: dict[str, Callable[..., list[Doc]]] = {
    "confidenceBounds": guard_confidence_bounds,
    "entityType": guard_entity_type,
    "observationsRequired": guard_observations_required,
}
RELATION_GUARDS: dict[str, Callable[..., list[Doc]]] = {
    "causalPolarity": guard_causal_polarity,
    "noSelfLoop": guard_no_self_loop,
    "noDuplicateRelation": guard_no_duplicate_relation,
}


# =============================================================================
# Cycle detection (findCycleNodes)
# =============================================================================


def find_cycle_nodes(adjacency: dict[str, list[str]], all_nodes: list[str]) -> set[str]:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(all_nodes, WHITE)
    parent: dict[str, str | None] = {}
    cycle_nodes: set[str] = set()

    def dfs(node: str) -> None:
        color[node] = GRAY
        for neighbor in adjacency.get(node, []):
            if neighbor not in color:
                continue
            if color[neighbor] == GRAY:
                cycle_nodes.add(neighbor)
                cur: str | None = node
                while cur is not None and cur != neighbor:
                    cycle_nodes.add(cur)
                    cur = parent.get(cur)
            elif color[neighbor] == WHITE:
                parent[neighbor] = node
                dfs(neighbor)
        color[node] = BLACK

    for node in all_nodes:
        if color[node] == WHITE:
            parent[node] = None
            dfs(node)
    return cycle_nodes


# =============================================================================
# The 5 default invariants — each returns a PropertyResult
# =============================================================================


def _property_result(name: str, level: str, violations: list[Doc], checked: int) -> Doc:
    return {
        "name": name,
        "level": level,
        "pass": len(violations) == 0,
        "checked": checked,
        "violations": violations,
    }


def inv_claims_need_evidence(entities: list[Doc], relations: list[Doc], store: Any) -> Doc:
    claims = [
        e
        for e in entities
        if e.get("entityType") == "claim" and _effective_status(e) in ("active", "investigating")
    ]
    violations: list[Doc] = []
    for claim in claims:
        supporting = [
            r for r in relations if r["to"] == claim["id"] and r["relationType"] == "supports"
        ]
        if not supporting:
            violations.append(
                {
                    "elementId": claim["id"],
                    "message": (
                        f"Claim '{claim['name']}' ({claim['id']}) has no supporting evidence"
                    ),
                }
            )
    return _property_result("claimsNeedEvidence", "subgraph", violations, len(claims))


def inv_no_causal_cycles(entities: list[Doc], relations: list[Doc], store: Any) -> Doc:
    causal = [r for r in relations if r["relationType"] in _CAUSAL_NAMES]
    if not causal:
        return _property_result("noCausalCycles", "graph", [], 0)
    loop_ids = {e["id"] for e in entities if e.get("entityType") == "loop"}
    adjacency: dict[str, list[str]] = {}
    all_nodes: dict[str, None] = {}
    for rel in causal:
        all_nodes.setdefault(rel["from"])
        all_nodes.setdefault(rel["to"])
        if rel["to"] not in loop_ids:
            adjacency.setdefault(rel["from"], []).append(rel["to"])
    cycle_nodes = find_cycle_nodes(adjacency, list(all_nodes))
    violations = [
        {"elementId": n, "message": f"Entity '{n}' is involved in an unintentional causal cycle"}
        for n in all_nodes
        if n in cycle_nodes
    ]
    return _property_result("noCausalCycles", "graph", violations, len(causal))


def inv_part_of_acyclic(entities: list[Doc], relations: list[Doc], store: Any) -> Doc:
    part_of = [r for r in relations if r["relationType"] == "part_of"]
    if not part_of:
        return _property_result("partOfAcyclic", "graph", [], 0)
    adjacency: dict[str, list[str]] = {}
    all_nodes: dict[str, None] = {}
    for rel in part_of:
        all_nodes.setdefault(rel["from"])
        all_nodes.setdefault(rel["to"])
        adjacency.setdefault(rel["from"], []).append(rel["to"])
    cycle_nodes = find_cycle_nodes(adjacency, list(all_nodes))
    violations = [
        {"elementId": n, "message": f"Entity '{n}' is involved in a part_of cycle"}
        for n in all_nodes
        if n in cycle_nodes
    ]
    return _property_result("partOfAcyclic", "graph", violations, len(part_of))


def inv_superseded_inert(entities: list[Doc], relations: list[Doc], store: Any) -> Doc:
    superseded = [e for e in entities if e.get("status") == "superseded"]
    violations: list[Doc] = []
    for entity in superseded:
        outgoing = [r for r in relations if r["from"] == entity["id"]]
        if any(r["relationType"] in _CAUSAL_NAMES for r in outgoing):
            violations.append(
                {
                    "elementId": entity["id"],
                    "message": (
                        f"Superseded entity '{entity['name']}' ({entity['id']}) has "
                        "outgoing causal relations"
                    ),
                }
            )
    return _property_result("supersededInert", "subgraph", violations, len(superseded))


def inv_retracted_isolated(entities: list[Doc], relations: list[Doc], store: Any) -> Doc:
    retracted = [e for e in entities if e.get("status") == "retracted"]
    violations: list[Doc] = []
    for entity in retracted:
        rels = [r for r in relations if r["from"] == entity["id"] or r["to"] == entity["id"]]
        if rels:
            violations.append(
                {
                    "elementId": entity["id"],
                    "message": (
                        f"Retracted entity '{entity['name']}' ({entity['id']}) has "
                        f"{len(rels)} active relation(s)"
                    ),
                }
            )
    return _property_result("retractedIsolated", "subgraph", violations, len(retracted))


BUILTIN_INVARIANTS: dict[str, Callable[..., Doc]] = {
    "claimsNeedEvidence": inv_claims_need_evidence,
    "noCausalCycles": inv_no_causal_cycles,
    "partOfAcyclic": inv_part_of_acyclic,
    "supersededInert": inv_superseded_inert,
    "retractedIsolated": inv_retracted_isolated,
}
DEFAULT_INVARIANT_NAMES = list(BUILTIN_INVARIANTS)
