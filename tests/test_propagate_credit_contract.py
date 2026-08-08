"""TL-482 (Agent Contract, epic TL-477): propagate-credit's dry-run default
made a persisted write indistinguishable from a simulation.

Before this fix, ``_propagate_one`` read ``options.get("dryRun", True)`` — so
a bare ``propagate-credit`` call, exactly what a caller reads as "the"
command from ``--help``/``COMMANDS.md``, silently simulated: it returned
computed ``newConfidence`` values in ``changes`` that were never written, with
nothing distinguishing that response from a real one. Probe reproduction
(TL-482): propagate-credit without ``dryRun`` returned computed
``newConfidence`` values; re-reading the entities showed baseline scores
unchanged; re-running with ``dryRun: false`` produced byte-identical output,
after which reads confirmed persistence.

Fix: the operation-level default is now ``dryRun: false`` (persist) —
consistent with the other mutating epistemic commands already in this file
(``postmortem-evaluate``, ``session-changelog`` both persist unless told
``dryRun: true``), so "the default path" a caller exercises without reading
past the first line of docs does not silently simulate. Every response — dry
or real — now carries the shared ``applied`` marker (true iff at least one
entity's confidence was actually written this call), and a simulated run also
carries a ``DRY_RUN`` notice naming the flag to flip.

These tests pin, in both directions, that ``applied`` matches what the store
actually holds after the call — the exact re-read the Phase 3 acceptance test
performs.
"""

from __future__ import annotations

from typing import Any

import pytest

from theloom.operations.entity import CreateEntityInput, create_entity
from theloom.operations.epistemic import PropagateCreditInput, propagate_credit
from theloom.operations.relations import CreateRelationInput, create_relation
from theloom.store.multigraph import MultiGraph

CONF = {"score": 0.5, "basis": "direct_observation"}


def _entity(multi: MultiGraph, name: str, **overrides: object) -> dict[str, Any]:
    base: dict[str, object] = {
        "name": name,
        "entityType": "claim",
        "observations": [f"observation about {name}"],
        "confidence": CONF,
    }
    base.update(overrides)
    result = create_entity(CreateEntityInput.model_validate(base), multi)
    assert isinstance(result, dict)
    return result


def _link(multi: MultiGraph, from_id: str, to_id: str) -> None:
    create_relation(
        CreateRelationInput.model_validate(
            {
                "from": from_id,
                "to": to_id,
                "relationType": "supports",
                "polarity": None,
                "strength": "moderate",
                "evidence": None,
            }
        ),
        multi,
    )


def _stored_score(multi: MultiGraph, entity_id: str) -> float:
    store = multi.get_store(None)
    entity = store.read_entity(entity_id)
    assert entity is not None
    assert entity.confidence is not None
    return entity.confidence.score


def test_default_dry_run_is_false_and_persists(multi: MultiGraph) -> None:
    """No ``dryRun`` at all is 'the default path' the Phase 3 acceptance test
    exercises: it must not silently simulate."""
    trigger = _entity(multi, "Trigger A")
    target = _entity(multi, "Target A")
    _link(multi, trigger["id"], target["id"])

    baseline = _stored_score(multi, target["id"])

    propagated = propagate_credit(
        PropagateCreditInput.model_validate({"entityIds": [trigger["id"]], "delta": 0.2}),
        multi,
    )
    assert propagated["count"] == 1
    result = propagated["items"][0]

    assert result["applied"] is True
    assert "notices" not in result
    assert result["changes"], "expected at least one downstream confidence change"
    change = result["changes"][0]
    assert change["entityId"] == target["id"]

    after = _stored_score(multi, target["id"])
    assert after != baseline
    assert after == pytest.approx(change["newConfidence"])


def test_dry_run_true_does_not_persist_and_carries_notice(multi: MultiGraph) -> None:
    trigger = _entity(multi, "Trigger B")
    target = _entity(multi, "Target B")
    _link(multi, trigger["id"], target["id"])

    baseline = _stored_score(multi, target["id"])

    result = propagate_credit(
        PropagateCreditInput.model_validate(
            {"entityIds": [trigger["id"]], "delta": 0.2, "dryRun": True}
        ),
        multi,
    )["items"][0]

    assert result["applied"] is False
    assert result["changes"], "a dry run still computes the would-be changes"
    codes = {n["code"] for n in result["notices"]}
    assert "DRY_RUN" in codes

    after = _stored_score(multi, target["id"])
    assert after == baseline, "a dry run must not touch stored confidence"


def test_explicit_dry_run_false_matches_default_and_persists(multi: MultiGraph) -> None:
    trigger = _entity(multi, "Trigger C")
    target = _entity(multi, "Target C")
    _link(multi, trigger["id"], target["id"])

    result = propagate_credit(
        PropagateCreditInput.model_validate(
            {"entityIds": [trigger["id"]], "delta": 0.2, "dryRun": False}
        ),
        multi,
    )["items"][0]
    assert result["applied"] is True
    assert "notices" not in result
    after = _stored_score(multi, target["id"])
    assert after == pytest.approx(result["changes"][0]["newConfidence"])


def test_dry_run_with_no_confidence_changes_is_not_applied(multi: MultiGraph) -> None:
    """A trigger with no confidence at all propagates nothing — ``applied``
    must stay false even on a real (non-dry) run, since nothing was written."""
    trigger = _entity(multi, "No-confidence trigger", confidence=None)

    result = propagate_credit(
        PropagateCreditInput.model_validate({"entityIds": [trigger["id"]], "delta": 0.2}),
        multi,
    )["items"][0]
    assert result["changes"] == []
    assert result["applied"] is False


def test_dry_run_documented_in_schema() -> None:
    """The evaluator learns behavior only from --schema/--help/COMMANDS.md, so
    the default must be spelled out in the field description, not just left
    to code comments."""
    field = PropagateCreditInput.model_fields["dry_run"]
    assert field.description is not None
    assert "false" in field.description.lower()
    assert "persist" in field.description.lower()
