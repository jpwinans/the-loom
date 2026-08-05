"""Simulate-Change composite.

Applies hypothetical mutations to a temporary clone of the graph, snapshots
structure before and after, and diffs them into
centrality/loop/component/blast-radius/entropy deltas plus a verdict. The
original graph is never modified (dry-run guarantee).

Fully deterministic. Snapshot/clone/mutate steps run UNwrapped (a failure fails
the whole composite); only the eight diff sections use :func:`time_section`.
The temp graph lives under a ``sim-<uuid>`` name and is dropped in ``finally``.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from math import log2
from typing import Any, Literal

from theloom.composites.framework import build_composite_result, time_section
from theloom.graph.hydrate import hydrate_graph
from theloom.model import ALL_ENTITY_STATUSES, EntityCreate, EntityFilter, RelationCreate
from theloom.operations.analysis import (
    DetectComponentsInput,
    DetectLoopsInput,
    detect_components,
    detect_loops,
)
from theloom.operations.common import CommandInput
from theloom.operations.reification import hash_at_depth
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph


class SimulationMutation(CommandInput):
    type: Literal[
        "createEntity", "updateEntity", "deleteEntity", "createRelation", "deleteRelation"
    ]
    payload: dict[str, Any]


class SimulateChangeInput(CommandInput):
    mutations: list[SimulationMutation]
    graph: str | None = None


def _shannon_entropy(group_sizes: list[int]) -> float:
    total = sum(group_sizes)
    if total == 0:
        return 0.0
    entropy = 0.0
    for size in group_sizes:
        if size > 0:
            p = size / total
            entropy -= p * log2(p)
    return entropy


def _capture_snapshot(multi: MultiGraph, graph: str | None) -> dict[str, Any]:
    store = multi.get_store(graph)
    entities = [e.model_dump(by_alias=True, exclude_unset=True) for e in store.list_entities()]
    relations = [r.model_dump(by_alias=True, exclude_unset=True) for r in store.list_relations()]

    raw_degree: dict[str, int] = {e["id"]: 0 for e in entities}
    for relation in relations:
        raw_degree[relation["from"]] = raw_degree.get(relation["from"], 0) + 1
        raw_degree[relation["to"]] = raw_degree.get(relation["to"], 0) + 1

    loop_result = detect_loops(DetectLoopsInput(graph=graph, persist=False), multi)
    component_result = detect_components(DetectComponentsInput(graph=graph), multi)

    loops = [
        {
            "name": loop["name"],
            "classification": loop["classification"],
            "memberCount": loop["memberCount"],
            "memberIds": loop["memberIds"],
            "signature": ",".join(sorted(loop["memberIds"])),
        }
        for loop in loop_result["loops"]
    ]

    graph_obj = hydrate_graph(entities, relations)
    cache: dict[str, str] = {}
    hash_counts: dict[str, int] = {}
    for node_id in graph_obj.nodes():
        h = hash_at_depth(graph_obj, node_id, 2, cache)
        hash_counts[h] = hash_counts.get(h, 0) + 1

    return {
        "centralityScores": raw_degree,
        "loops": loops,
        "componentCount": component_result["summary"]["componentCount"],
        "entityCount": len(entities),
        "fingerprintGroupSizes": list(hash_counts.values()),
    }


def _clone_to_temp(multi: MultiGraph, source_graph: str | None) -> str:
    temp_name = f"sim-{uuid.uuid4().hex}"
    multi.create_graph(temp_name)
    source = multi.get_store(source_graph)
    temp: FalkorGraphStore = multi.get_store(temp_name)
    all_statuses = EntityFilter.model_validate({"statusFilter": list(ALL_ENTITY_STATUSES)})
    for entity in source.list_entities(all_statuses):
        temp.import_entity_doc(entity.model_dump(by_alias=True, exclude_unset=True))
    for relation in source.list_relations():
        temp.import_relation_doc(relation.model_dump(by_alias=True, exclude_unset=True))
    return temp_name


def _apply_mutations(mutations: list[SimulationMutation], store: FalkorGraphStore) -> list[str]:
    last_created: str | None = None
    created_ids: list[str] = []
    for mutation in mutations:
        payload = mutation.payload
        if mutation.type == "createEntity":
            entity = store.create_entity(EntityCreate.model_validate(payload))
            last_created = entity.id
            created_ids.append(entity.id)
        elif mutation.type == "updateEntity":
            updates = {k: v for k, v in payload.items() if k != "id"}
            store.update_entity(payload["id"], updates)
        elif mutation.type == "deleteEntity":
            store.delete_entity(payload["id"])
        elif mutation.type == "createRelation":
            relation_payload = dict(payload)
            if relation_payload.get("from") == "__LAST_CREATED__":
                if not last_created:
                    raise ValueError(
                        'Cannot use __LAST_CREATED__ in "from" — no entity has been created yet'
                    )
                relation_payload["from"] = last_created
            if relation_payload.get("to") == "__LAST_CREATED__":
                if not last_created:
                    raise ValueError(
                        'Cannot use __LAST_CREATED__ in "to" — no entity has been created yet'
                    )
                relation_payload["to"] = last_created
            store.create_relation(RelationCreate.model_validate(relation_payload))
        elif mutation.type == "deleteRelation":
            store.delete_relation(payload["from"], payload["to"])
    return created_ids


def _centrality_delta(before: dict[str, int], after: dict[str, int]) -> list[dict[str, Any]]:
    # Iterate the dicts in insertion order (a plain Python set does not preserve
    # it) — the output array order is significant.
    before_ids = set(before)
    after_ids = set(after)
    deltas: list[dict[str, Any]] = []
    for entity_id in before:
        if entity_id in after_ids:
            if before[entity_id] != after[entity_id]:
                deltas.append(
                    {"entityId": entity_id, "before": before[entity_id], "after": after[entity_id]}
                )
        else:
            deltas.append({"entityId": entity_id, "before": before[entity_id], "after": 0})
    for entity_id in after:
        if entity_id not in before_ids:
            deltas.append({"entityId": entity_id, "before": 0, "after": after[entity_id]})
    return deltas


def _loop_diff(
    before_loops: list[dict[str, Any]], after_loops: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    before_sigs = {loop["signature"] for loop in before_loops}
    after_sigs = {loop["signature"] for loop in after_loops}

    def _view(loop: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": loop["name"],
            "classification": loop["classification"],
            "memberCount": loop["memberCount"],
            "memberIds": loop["memberIds"],
        }

    broken = [_view(loop) for loop in before_loops if loop["signature"] not in after_sigs]
    created = [_view(loop) for loop in after_loops if loop["signature"] not in before_sigs]
    return broken, created


def _verdict(
    centrality_delta: list[dict[str, Any]],
    broken_loops: list[dict[str, Any]],
    new_loops: list[dict[str, Any]],
    component_changes: dict[str, int],
) -> dict[str, Any]:
    reasons: list[str] = []
    degrade_score = 0
    improve_score = 0

    if broken_loops:
        degrade_score += len(broken_loops)
        reasons.append(f"{len(broken_loops)} feedback loop(s) broken by the mutations")
    if new_loops:
        improve_score += len(new_loops)
        reasons.append(f"{len(new_loops)} new feedback loop(s) created by the mutations")

    new_isolated = sum(1 for d in centrality_delta if d["before"] == 0 and d["after"] == 0)
    component_increase = component_changes["after"] - component_changes["before"]
    if component_increase > 0:
        structural_splits = component_increase - new_isolated
        if structural_splits > 0:
            degrade_score += structural_splits
            reasons.append(
                f"Component count increased from {component_changes['before']} to "
                f"{component_changes['after']} (graph fragmentation)"
            )
    elif component_increase < 0:
        improve_score += abs(component_increase)
        reasons.append(
            f"Component count decreased from {component_changes['before']} to "
            f"{component_changes['after']} (improved connectivity)"
        )

    existing = [d for d in centrality_delta if d["before"] > 0 and d["after"] > 0]
    net_change = sum(d["after"] - d["before"] for d in existing)
    if net_change > 0:
        improve_score += 1
        reasons.append("Net centrality increased for existing entities")
    elif net_change < 0:
        degrade_score += 1
        reasons.append("Net centrality decreased for existing entities")

    if degrade_score > 0 and degrade_score >= improve_score:
        classification = "degrades"
    elif improve_score > 0 and improve_score > degrade_score:
        classification = "improves"
    else:
        classification = "neutral"
        if not reasons:
            reasons.append("No significant structural changes detected")
    return {"classification": classification, "reasons": reasons}


def simulate_change(params: SimulateChangeInput, multi: MultiGraph) -> dict[str, Any]:
    start = time.perf_counter()
    temp_name: str | None = None
    try:
        # Before/after must read the same graph, or the diff compares two
        # unrelated states.
        before = _capture_snapshot(multi, params.graph)
        temp_name = _clone_to_temp(multi, params.graph)
        created_ids = _apply_mutations(params.mutations, multi.get_store(temp_name))
        after = _capture_snapshot(multi, temp_name)

        centrality_delta = time_section(
            lambda: _centrality_delta(before["centralityScores"], after["centralityScores"])
        )
        broken, created = _loop_diff(before["loops"], after["loops"])
        broken_loops = time_section(lambda: broken)
        new_loops = time_section(lambda: created)
        component_changes = time_section(
            lambda: {"before": before["componentCount"], "after": after["componentCount"]}
        )

        def _blast_radius() -> dict[str, Any]:
            affected: set[str] = set()
            if centrality_delta["data"]:
                for entry in centrality_delta["data"]:
                    affected.add(entry["entityId"])
            for mutation in params.mutations:
                payload = mutation.payload
                if payload.get("id"):
                    affected.add(payload["id"])
                if payload.get("from") and payload["from"] != "__LAST_CREATED__":
                    affected.add(payload["from"])
                if payload.get("to") and payload["to"] != "__LAST_CREATED__":
                    affected.add(payload["to"])
            for created_id in created_ids:
                affected.add(created_id)
            return {"affected": len(affected), "total": after["entityCount"]}

        blast_radius = time_section(_blast_radius)
        component_count_reduction = time_section(
            lambda: before["componentCount"] - after["componentCount"]
        )
        wl_entropy_delta = time_section(
            lambda: (
                _shannon_entropy(after["fingerprintGroupSizes"])
                - _shannon_entropy(before["fingerprintGroupSizes"])
            )
        )
        verdict = time_section(
            lambda: _verdict(
                centrality_delta["data"] or [],
                broken_loops["data"] or [],
                new_loops["data"] or [],
                component_changes["data"] or {"before": 0, "after": 0},
            )
        )

        sections = {
            "centralityDelta": centrality_delta,
            "brokenLoops": broken_loops,
            "newLoops": new_loops,
            "componentChanges": component_changes,
            "blastRadius": blast_radius,
            "componentCountReduction": component_count_reduction,
            "wlEntropyDelta": wl_entropy_delta,
            "verdict": verdict,
        }
        total_ms = round((time.perf_counter() - start) * 1000)
        return build_composite_result(sections, total_ms)
    finally:
        if temp_name is not None:
            with contextlib.suppress(Exception):
                multi.delete_graph(temp_name)  # cleanup best-effort
