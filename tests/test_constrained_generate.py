"""constrained-generate unit coverage (non-golden by construction).

Remaining slots are filled from a time-seeded PRNG (non-deterministic), so the
command has no golden fixtures; this build emits a stable required-types subset.
These tests pin that contract.
"""

from __future__ import annotations

from typing import Any, cast

from theloom.operations.verification import ConstrainedGenerateInput, constrained_generate

_MULTI = cast(Any, None)  # constrained_generate is pure over its params


def _run(doc: dict[str, Any]) -> dict[str, Any]:
    return constrained_generate(ConstrainedGenerateInput.model_validate(doc), _MULTI)


def test_required_types_emitted_in_order() -> None:
    result = _run({"maxEntities": 5, "maxRelations": 0, "requiredTypes": ["claim", "evidence"]})
    assert result["success"] is True
    assert [e["entityType"] for e in result["entities"]] == ["claim", "evidence"]
    assert result["entities"][0] == {
        "name": "Generated claim",
        "entityType": "claim",
        "observations": ["Auto-generated claim entity"],
    }
    assert result["relations"] == []


def test_zero_entities_guard_in_process() -> None:
    """maxEntities=0 is schema-rejected on the wire (the schema bounds it >0);
    the handler's guard covers in-process callers."""
    from theloom.model import EntityType

    params = ConstrainedGenerateInput.model_construct(
        max_entities=0, max_relations=0, required_types=[EntityType.CLAIM]
    )
    result = constrained_generate(params, _MULTI)
    assert result["success"] is False
    assert result["failureReason"] == "Cannot satisfy required types with maxEntities=0"
    empty = ConstrainedGenerateInput.model_construct(
        max_entities=0, max_relations=0, required_types=None
    )
    assert constrained_generate(empty, _MULTI) == {
        "success": True,
        "entities": [],
        "relations": [],
    }


def test_too_many_required_types_fails() -> None:
    result = _run({"maxEntities": 1, "maxRelations": 0, "requiredTypes": ["claim", "evidence"]})
    assert result["success"] is False
    assert result["failureReason"] == "Required 2 types but maxEntities is 1"


def test_deterministic_across_runs() -> None:
    doc = {"maxEntities": 4, "maxRelations": 2, "requiredTypes": ["concept", "variable"]}
    assert _run(doc) == _run(doc)
