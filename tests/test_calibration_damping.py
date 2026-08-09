"""Acceptance (d) from claude-desires.md #14: propagate-credit's
``dampingFactor: "calibrated"`` resolves damping per hop from the hop's
SOURCE author's measured reliability, so two authors with different planted
track records propagate differently -- plus the INSUFFICIENT_DATA fallback
for an author with no resolved history.
"""

from __future__ import annotations

from typing import Any

import pytest

from theloom.operations.calibration import ResolveClaimInput, resolve_claim
from theloom.operations.entity import CreateEntityInput, create_entity
from theloom.operations.epistemic import PropagateCreditInput, propagate_credit
from theloom.operations.relations import CreateRelationInput, create_relation
from theloom.store.multigraph import MultiGraph


def resolve(
    multi: MultiGraph, claim_id: str, resolution: str, evidence: str = "e"
) -> dict[str, Any]:
    result = resolve_claim(
        ResolveClaimInput.model_validate(
            {"claimId": claim_id, "resolution": resolution, "evidence": evidence}
        ),
        multi,
    )
    assert isinstance(result, dict)
    return result


def _entity(multi: MultiGraph, name: str, **overrides: object) -> dict[str, Any]:
    base: dict[str, object] = {
        "name": name,
        "entityType": "claim",
        "observations": [f"observation about {name}"],
    }
    base.update(overrides)
    result = create_entity(CreateEntityInput.model_validate(base), multi)
    assert isinstance(result, dict)
    return result


def _supports(multi: MultiGraph, from_id: str, to_id: str) -> None:
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


def _plant_track_record(
    multi: MultiGraph, *, session: str, n_confirmed: int, n_refuted: int, score: float
) -> None:
    for i in range(n_confirmed):
        c = _entity(
            multi,
            f"{session}-confirmed-{i}",
            confidence={"score": score, "basis": "direct_observation"},
            session=session,
        )
        resolve(multi, c["id"], "confirmed")
    for i in range(n_refuted):
        c = _entity(
            multi,
            f"{session}-refuted-{i}",
            confidence={"score": score, "basis": "direct_observation"},
            session=session,
        )
        resolve(multi, c["id"], "refuted")


def test_acceptance_d_calibrated_damping_differs_by_author_track_record(multi: MultiGraph) -> None:
    # Alice: 5/5 confirmed at 0.9 -> Brier = (0.9-1)^2 = 0.01 -> reliability = 0.99
    _plant_track_record(multi, session="alice", n_confirmed=5, n_refuted=0, score=0.9)
    # Bob: 1/5 confirmed, 4/5 refuted, all asserted 0.9 ->
    # Brier = [(0.9-1)^2*1 + (0.9-0)^2*4] / 5 = (0.01 + 3.24) / 5 = 0.65 -> reliability = 0.35
    _plant_track_record(multi, session="bob", n_confirmed=1, n_refuted=4, score=0.9)

    trigger_alice = _entity(
        multi,
        "trigger-alice",
        confidence={"score": 0.5, "basis": "direct_observation"},
        session="alice",
    )
    target_alice = _entity(
        multi, "target-alice", confidence={"score": 0.5, "basis": "direct_observation"}
    )
    _supports(multi, trigger_alice["id"], target_alice["id"])

    trigger_bob = _entity(
        multi,
        "trigger-bob",
        confidence={"score": 0.5, "basis": "direct_observation"},
        session="bob",
    )
    target_bob = _entity(
        multi, "target-bob", confidence={"score": 0.5, "basis": "direct_observation"}
    )
    _supports(multi, trigger_bob["id"], target_bob["id"])

    alice_result = propagate_credit(
        PropagateCreditInput.model_validate(
            {
                "entityIds": [trigger_alice["id"]],
                "delta": 0.4,
                "dampingFactor": "calibrated",
                "dryRun": True,
            }
        ),
        multi,
    )["items"][0]
    bob_result = propagate_credit(
        PropagateCreditInput.model_validate(
            {
                "entityIds": [trigger_bob["id"]],
                "delta": 0.4,
                "dampingFactor": "calibrated",
                "dryRun": True,
            }
        ),
        multi,
    )["items"][0]

    alice_change = alice_result["changes"][0]
    bob_change = bob_result["changes"][0]

    assert alice_change["dampingApplied"] == pytest.approx(0.99)
    assert bob_change["dampingApplied"] == pytest.approx(0.35)
    assert alice_change["dampingApplied"] != bob_change["dampingApplied"]

    # hop_delta = (1/1) * damping * delta * strength(moderate=0.7) * polarity(+1)
    assert alice_change["newConfidence"] == pytest.approx(0.5 + 0.99 * 0.4 * 0.7)
    assert bob_change["newConfidence"] == pytest.approx(0.5 + 0.35 * 0.4 * 0.7)
    assert alice_change["newConfidence"] != pytest.approx(bob_change["newConfidence"])

    # dryRun still carries its own DRY_RUN notice, but neither author had to
    # fall back -- both had full planted history, so no INSUFFICIENT_DATA.
    alice_codes = {n["code"] for n in alice_result["notices"]}
    bob_codes = {n["code"] for n in bob_result["notices"]}
    assert alice_codes == {"DRY_RUN"}
    assert bob_codes == {"DRY_RUN"}


def test_calibrated_damping_falls_back_and_notices_when_author_has_no_history(
    multi: MultiGraph,
) -> None:
    trigger = _entity(
        multi,
        "trigger-carol",
        confidence={"score": 0.5, "basis": "direct_observation"},
        session="carol",
    )
    target = _entity(
        multi, "target-carol", confidence={"score": 0.5, "basis": "direct_observation"}
    )
    _supports(multi, trigger["id"], target["id"])

    result = propagate_credit(
        PropagateCreditInput.model_validate(
            {
                "entityIds": [trigger["id"]],
                "delta": 0.4,
                "dampingFactor": "calibrated",
                "dryRun": True,
            }
        ),
        multi,
    )["items"][0]

    change = result["changes"][0]
    assert change["dampingApplied"] == pytest.approx(0.5)  # the ordinary constant fallback
    codes = {n["code"] for n in result["notices"]}
    assert "INSUFFICIENT_DATA" in codes
    assert any("carol" in n["message"] for n in result["notices"])


def test_constant_damping_still_works_unchanged(multi: MultiGraph) -> None:
    trigger = _entity(
        multi, "constant-trigger", confidence={"score": 0.5, "basis": "direct_observation"}
    )
    target = _entity(
        multi, "constant-target", confidence={"score": 0.5, "basis": "direct_observation"}
    )
    _supports(multi, trigger["id"], target["id"])

    result = propagate_credit(
        PropagateCreditInput.model_validate(
            {"entityIds": [trigger["id"]], "delta": 0.4, "dampingFactor": 0.6, "dryRun": True}
        ),
        multi,
    )["items"][0]
    change = result["changes"][0]
    assert change["dampingApplied"] == pytest.approx(0.6)
    assert change["newConfidence"] == pytest.approx(0.5 + 0.6 * 0.4 * 0.7)


def test_damping_factor_rejects_out_of_range_number(multi: MultiGraph) -> None:
    """'calibrated' is the only accepted string (enforced by the schema
    itself, a pydantic-level rejection); an out-of-range *number* is the
    operation's own runtime bounds check, unchanged by adding the
    'calibrated' sentinel."""
    trigger = _entity(
        multi, "bad-damping-trigger", confidence={"score": 0.5, "basis": "direct_observation"}
    )
    from theloom.errors import ValidationError

    with pytest.raises(ValidationError):
        propagate_credit(
            PropagateCreditInput.model_validate(
                {"entityIds": [trigger["id"]], "delta": 0.4, "dampingFactor": 1.5}
            ),
            multi,
        )
