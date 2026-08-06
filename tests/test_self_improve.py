"""Self-Improve composite: autoApply must not swallow relation-write failures.

The apply section used to create each proposal's relations in a bare
try/except pass — a failing write (e.g. a stale targetId whose entity was
retracted between propose and apply) vanished with no trace, breaching the
"every mutation is honestly accounted for" invariant. It must now be reported
back to the caller instead of disappearing.
"""

from __future__ import annotations

from typing import Any

import pytest

from theloom.composites.self_improve import SelfImproveInput, self_improve
from theloom.errors import OperationError
from theloom.model import EntityCreate
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph


def _fake_simulate_change(_input: Any, _multi: Any) -> dict[str, Any]:
    """Deterministic 'improves' verdict so ranking never filters the proposal."""
    return {
        "result": {
            "verdict": {"data": {"classification": "improves", "reasons": ["stub"]}},
            "blastRadius": {"data": {"affected": 1}},
        }
    }


def _proposal(name: str, target_id: str) -> dict[str, Any]:
    """A minimal proposal with one gate-passing relation to ``target_id``."""
    return {
        "entity": {"name": name, "entityType": "concept", "observations": ["proposed obs"]},
        "relations": [
            {"targetId": target_id, "relationType": "related_to", "direction": "outgoing"},
        ],
        "rationale": f"test proposal {name}",
        "capabilityViolation": None,
        "confidence": 0.9,
        "strategy": "pattern_completion",
    }


def _patch_proposals(monkeypatch: pytest.MonkeyPatch, proposals: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(
        "theloom.composites.self_improve.propose_entities_op",
        lambda store, opts: {
            "proposals": proposals,
            "strategyCounts": {"pattern_completion": len(proposals), "llm_reasoning": 0},
        },
    )


def test_autoapply_reports_failed_relation_write_instead_of_swallowing(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = multi.get_store()
    target = store.create_entity(
        EntityCreate.model_validate(
            {"name": "Existing Target", "entityType": "concept", "observations": ["obs"]}
        )
    )

    proposal = {
        "entity": {
            "name": "Proposed Thing",
            "entityType": "concept",
            "observations": ["proposed obs"],
        },
        "relations": [
            {"targetId": target.id, "relationType": "related_to", "direction": "outgoing"},
            {
                "targetId": "does-not-exist",
                "relationType": "related_to",
                "direction": "outgoing",
            },
        ],
        "rationale": "test proposal",
        "capabilityViolation": None,
        "confidence": 0.9,
        "strategy": "pattern_completion",
    }

    monkeypatch.setattr(
        "theloom.composites.self_improve.propose_entities_op",
        lambda store, opts: {
            "proposals": [proposal],
            "strategyCounts": {"pattern_completion": 1, "llm_reasoning": 0},
        },
    )
    monkeypatch.setattr("theloom.composites.self_improve.simulate_change", _fake_simulate_change)

    result = self_improve(
        SelfImproveInput.model_validate({"autoApply": True, "applyTopN": 1}),
        multi,
    )

    applied = result["applied"]
    assert len(applied) == 1
    assert len(applied[0]["createdRelationIds"]) == 1

    failed_writes = result["failedWrites"]
    assert len(failed_writes) == 1
    failure = failed_writes[0]
    assert failure["targetId"] == "does-not-exist"
    assert failure["relationType"] == "related_to"
    assert "reason" in failure and failure["reason"]

    # The successful relation really landed and is event-logged like any
    # other write — the failure of its sibling did not roll it back.
    relations = store.list_relations()
    assert any(r.from_ == applied[0]["createdEntityId"] and r.to == target.id for r in relations)


def test_autoapply_causal_relation_gets_polarity_default(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proposal's causal relation must go through the same gated write path
    as create-relation — including CAUSAL_POLARITY_DEFAULTS — not a bare
    store call with polarity hardcoded to None."""
    store = multi.get_store()
    target = store.create_entity(
        EntityCreate.model_validate(
            {"name": "Causal Target", "entityType": "concept", "observations": ["obs"]}
        )
    )

    proposal = {
        "entity": {
            "name": "Causal Proposal",
            "entityType": "concept",
            "observations": ["proposed obs"],
        },
        "relations": [
            {"targetId": target.id, "relationType": "causes", "direction": "outgoing"},
        ],
        "rationale": "test causal proposal",
        "capabilityViolation": None,
        "confidence": 0.9,
        "strategy": "pattern_completion",
    }

    monkeypatch.setattr(
        "theloom.composites.self_improve.propose_entities_op",
        lambda store, opts: {
            "proposals": [proposal],
            "strategyCounts": {"pattern_completion": 1, "llm_reasoning": 0},
        },
    )
    monkeypatch.setattr("theloom.composites.self_improve.simulate_change", _fake_simulate_change)

    result = self_improve(
        SelfImproveInput.model_validate({"autoApply": True, "applyTopN": 1}),
        multi,
    )

    applied = result["applied"]
    assert len(applied) == 1
    assert result["failedWrites"] == []

    created_entity_id = applied[0]["createdEntityId"]
    relations = store.list_relations()
    causal_edges = [r for r in relations if r.from_ == created_entity_id and r.to == target.id]
    assert len(causal_edges) == 1
    # "causes" -> "+" is CAUSAL_POLARITY_DEFAULTS, an independently known
    # domain literal, not a value recomputed the way the code computes it.
    assert causal_edges[0].polarity == "+"


def test_autoapply_rejects_relation_that_violates_gate(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proposal relation pointed at a retracted entity must be blocked by
    the relation gate and reported in failedWrites, not written anyway."""
    store = multi.get_store()
    target = store.create_entity(
        EntityCreate.model_validate(
            {"name": "Retracted Target", "entityType": "concept", "observations": ["obs"]}
        )
    )
    store.delete_entity(target.id)  # soft delete -> status 'retracted'

    proposal = {
        "entity": {
            "name": "Gate Violation Proposal",
            "entityType": "concept",
            "observations": ["proposed obs"],
        },
        "relations": [
            {"targetId": target.id, "relationType": "related_to", "direction": "outgoing"},
        ],
        "rationale": "test gate violation",
        "capabilityViolation": None,
        "confidence": 0.9,
        "strategy": "pattern_completion",
    }

    monkeypatch.setattr(
        "theloom.composites.self_improve.propose_entities_op",
        lambda store, opts: {
            "proposals": [proposal],
            "strategyCounts": {"pattern_completion": 1, "llm_reasoning": 0},
        },
    )
    monkeypatch.setattr("theloom.composites.self_improve.simulate_change", _fake_simulate_change)

    result = self_improve(
        SelfImproveInput.model_validate({"autoApply": True, "applyTopN": 1}),
        multi,
    )

    applied = result["applied"]
    assert len(applied) == 1
    assert applied[0]["createdRelationIds"] == []

    failed_writes = result["failedWrites"]
    assert len(failed_writes) == 1
    assert failed_writes[0]["targetId"] == target.id
    assert failed_writes[0]["relationType"] == "related_to"
    assert "retracted" in failed_writes[0]["reason"]

    # The rejected relation must not exist at all — the gate is a real block,
    # not a warning appended after the fact.
    relations = store.list_relations()
    assert not any(r.to == target.id for r in relations)


def test_simulation_failure_ranks_as_degrades_and_is_never_auto_applied(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proposal whose simulation raises must rank with the degraders, not
    the neutrals: 'could not evaluate' must never outrank 'evaluated badly',
    and an un-simulatable proposal must never be auto-applied."""
    store = multi.get_store()
    target = store.create_entity(
        EntityCreate.model_validate(
            {"name": "Sim Target", "entityType": "concept", "observations": ["obs"]}
        )
    )
    good = _proposal("Good Proposal", target.id)
    bad = _proposal("Bad Proposal", target.id)
    _patch_proposals(monkeypatch, [good, bad])

    def fake_sim(sim_input: Any, _multi: Any) -> dict[str, Any]:
        if sim_input.mutations[0].payload["name"] == "Bad Proposal":
            raise RuntimeError("simulation blew up")
        return _fake_simulate_change(sim_input, _multi)

    monkeypatch.setattr("theloom.composites.self_improve.simulate_change", fake_sim)

    result = self_improve(
        SelfImproveInput.model_validate({"autoApply": True, "applyTopN": 5}),
        multi,
    )

    sim_data = result["composite"]["result"]["simulate"]["data"]
    assert sim_data["improves"] == 1
    assert sim_data["degrades"] == 1
    assert sim_data["neutral"] == 0

    # The un-simulatable proposal is filtered out of the ranking entirely.
    ranked_names = [r["proposal"]["entity"]["name"] for r in result["proposals"]]
    assert ranked_names == ["Good Proposal"]
    assert result["composite"]["result"]["rank"]["data"]["filteredCount"] == 1

    applied_names = [a["ranked"]["proposal"]["entity"]["name"] for a in result["applied"]]
    assert applied_names == ["Good Proposal"]

    entity_names = [e.name for e in store.list_entities()]
    assert "Good Proposal" in entity_names
    assert "Bad Proposal" not in entity_names


def test_autoapply_rolls_back_entity_when_relation_batch_write_fails(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relation WRITE failure (post-gate, e.g. a race lost at commit) must
    not leave the just-created entity behind: the saga removes it and the
    failure is reported, so an entity without its proposed relations is never
    a silent resting state."""
    store = multi.get_store()
    target = store.create_entity(
        EntityCreate.model_validate(
            {"name": "Batch Target", "entityType": "concept", "observations": ["obs"]}
        )
    )
    _patch_proposals(monkeypatch, [_proposal("Proposed Thing", target.id)])
    monkeypatch.setattr("theloom.composites.self_improve.simulate_change", _fake_simulate_change)

    def boom(self: FalkorGraphStore, specs: Any) -> Any:
        raise OperationError("simulated relation write failure")

    monkeypatch.setattr(FalkorGraphStore, "create_relations", boom)

    result = self_improve(
        SelfImproveInput.model_validate({"autoApply": True, "applyTopN": 1}),
        multi,
    )

    assert result["applied"] == []
    assert result["failedWrites"] == []  # the gate passed; this was a write failure

    apply_failures = result["applyFailures"]
    assert len(apply_failures) == 1
    failure = apply_failures[0]
    assert failure["proposalEntityName"] == "Proposed Thing"
    assert failure["stage"] == "createRelations"
    assert "simulated relation write failure" in failure["reason"]
    assert failure["rolledBackEntityId"]

    # No orphan: the entity created just before the failed batch is gone.
    entity_names = [e.name for e in store.list_entities()]
    assert "Proposed Thing" not in entity_names


def test_autoapply_double_failure_reports_the_stranded_entity_id(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the relation batch fails AND the compensating delete fails, an
    orphan entity remains — and the report must carry its id under
    ``strandedEntityId`` so an operator can clean it up (names are not
    unique). ``rolledBackEntityId: None`` keeps meaning 'nothing was rolled
    back'."""
    store = multi.get_store()
    target = store.create_entity(
        EntityCreate.model_validate(
            {"name": "Batch Target", "entityType": "concept", "observations": ["obs"]}
        )
    )
    _patch_proposals(monkeypatch, [_proposal("Stranded Proposal", target.id)])
    monkeypatch.setattr("theloom.composites.self_improve.simulate_change", _fake_simulate_change)

    def boom_batch(self: FalkorGraphStore, specs: Any) -> Any:
        raise OperationError("simulated relation write failure")

    def boom_delete(self: FalkorGraphStore, entity_id: str, hard: bool = False) -> Any:
        raise OperationError("store unreachable during rollback")

    monkeypatch.setattr(FalkorGraphStore, "create_relations", boom_batch)
    monkeypatch.setattr(FalkorGraphStore, "delete_entity", boom_delete)

    result = self_improve(
        SelfImproveInput.model_validate({"autoApply": True, "applyTopN": 1}),
        multi,
    )

    apply_failures = result["applyFailures"]
    assert len(apply_failures) == 1
    failure = apply_failures[0]
    assert failure["rolledBackEntityId"] is None  # nothing was rolled back
    assert "store unreachable during rollback" in failure["rollbackError"]
    stranded_id = failure["strandedEntityId"]

    # The orphan really is in the graph, and the id in the report finds it.
    monkeypatch.undo()
    stranded = store.read_entity_doc(stranded_id)
    assert stranded is not None
    assert stranded["name"] == "Stranded Proposal"


def test_autoapply_reports_credit_propagation_failure(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Credit-propagation failure must surface in creditFailures — never be
    silently suppressed — while the applied entity and relations stand."""
    store = multi.get_store()
    target = store.create_entity(
        EntityCreate.model_validate(
            {"name": "Credit Target", "entityType": "concept", "observations": ["obs"]}
        )
    )
    _patch_proposals(monkeypatch, [_proposal("Credited Proposal", target.id)])
    monkeypatch.setattr("theloom.composites.self_improve.simulate_change", _fake_simulate_change)

    def failing_credit(_params: Any, _multi: Any) -> dict[str, Any]:
        raise RuntimeError("credit propagation exploded")

    monkeypatch.setattr("theloom.composites.self_improve.propagate_credit", failing_credit)

    result = self_improve(
        SelfImproveInput.model_validate({"autoApply": True, "applyTopN": 1}),
        multi,
    )

    applied = result["applied"]
    assert len(applied) == 1
    assert len(applied[0]["createdRelationIds"]) == 1

    credit_failures = result["creditFailures"]
    assert len(credit_failures) == 1
    assert credit_failures[0]["entityId"] == applied[0]["createdEntityId"]
    assert credit_failures[0]["proposalEntityName"] == "Credited Proposal"
    assert "credit propagation exploded" in credit_failures[0]["reason"]

    # The applied entity and its relation survive a credit failure.
    relations = store.list_relations()
    assert any(r.from_ == applied[0]["createdEntityId"] and r.to == target.id for r in relations)
