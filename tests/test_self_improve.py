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
from theloom.model import EntityCreate
from theloom.store.multigraph import MultiGraph


def _fake_simulate_change(_input: Any, _multi: Any) -> dict[str, Any]:
    """Deterministic 'improves' verdict so ranking never filters the proposal."""
    return {
        "result": {
            "verdict": {"data": {"classification": "improves", "reasons": ["stub"]}},
            "blastRadius": {"data": {"affected": 1}},
        }
    }


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
