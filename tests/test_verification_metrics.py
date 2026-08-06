"""Unit tests for theloom.verification.metrics — the coverage/coupling
violation generators shared between the check-capabilities command and the
CapabilitySpec DSL. Pins that both live downward-only in the verification
package (no import of theloom.operations) and produce identical output
regardless of which caller invokes them.
"""

from __future__ import annotations

from theloom.verification.metrics import capability_result, coupling, coverage


def _entity(entity_id: str, name: str, entity_type: str) -> dict[str, object]:
    return {"id": entity_id, "name": name, "entityType": entity_type}


def _relation(from_id: str, to_id: str, relation_type: str) -> dict[str, object]:
    return {"id": f"{from_id}-{to_id}", "from": from_id, "to": to_id, "relationType": relation_type}


def test_capability_result_pass_and_fail() -> None:
    assert capability_result("x", [])["pass"] is True
    assert capability_result("x", [{"violationType": "coverage"}])["pass"] is False


def test_coverage_flags_parent_with_no_linked_child() -> None:
    entities = [
        _entity("p1", "System A", "system"),
        _entity("p2", "System B", "system"),
        _entity("c1", "Procedure X", "procedure"),
    ]
    relations = [_relation("p1", "c1", "supports")]

    result = coverage(entities, relations, "system", "procedure", "supports")

    assert result["pass"] is False
    assert len(result["violations"]) == 1
    violation = result["violations"][0]
    assert violation["elementId"] == "p2"
    assert violation["violationType"] == "coverage"
    assert violation["capabilityName"] == "coverage(system->procedure via supports)"


def test_coverage_passes_when_every_parent_has_a_child() -> None:
    entities = [_entity("p1", "System A", "system"), _entity("c1", "Procedure X", "procedure")]
    relations = [_relation("p1", "c1", "supports")]

    result = coverage(entities, relations, "system", "procedure", "supports")

    assert result["pass"] is True
    assert result["violations"] == []


def test_coupling_flags_entities_above_threshold() -> None:
    entities = [_entity("hub", "Hub", "system"), _entity("leaf", "Leaf", "system")]
    relations = [_relation("hub", "leaf", "causes")]

    result = coupling(entities, relations, "degree", 0.0)

    assert result["pass"] is False
    element_ids = {v["elementId"] for v in result["violations"]}
    assert "hub" in element_ids


def test_coupling_empty_graph_passes() -> None:
    result = coupling([], [], "degree", 0.0)
    assert result["pass"] is True
    assert result["violations"] == []
