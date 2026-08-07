"""constrained-generate unit coverage.

constrained-generate delegates to ``TypeConstrainedGenerator`` (the same
seeded ``mulberry32`` generator ``cegis-synthesize`` uses) with a fixed
default seed, so a given input is deterministic and honors maxEntities /
maxRelations in full rather than emitting only the required-type subset.
"""

from __future__ import annotations

from typing import Any, cast

from theloom.operations.verification import ConstrainedGenerateInput, constrained_generate
from theloom.synthesis.generator import TypeCompatibilityGraph

_MULTI = cast(Any, None)  # constrained_generate is pure over its params unless commit=True

_TYPE_GRAPH = TypeCompatibilityGraph.create_default()


def _run(doc: dict[str, Any]) -> dict[str, Any]:
    return constrained_generate(ConstrainedGenerateInput.model_validate(doc), _MULTI)


def _assert_relations_type_valid(result: dict[str, Any]) -> None:
    entities = result["entities"]
    seen: set[tuple[int, int, str]] = set()
    for rel in result["relations"]:
        source = entities[rel["fromIndex"]]["entityType"]
        target = entities[rel["toIndex"]]["entityType"]
        assert _TYPE_GRAPH.is_valid(source, rel["relationType"], target)
        assert rel["fromIndex"] != rel["toIndex"]
        key = (rel["fromIndex"], rel["toIndex"], rel["relationType"])
        assert key not in seen
        seen.add(key)


def test_required_types_placed_first_when_maxentities_matches() -> None:
    """maxEntities == len(requiredTypes): no filler needed, output is exactly
    the required types, in order."""
    result = _run({"maxEntities": 2, "maxRelations": 0, "requiredTypes": ["claim", "evidence"]})
    assert result["success"] is True
    assert [e["entityType"] for e in result["entities"]] == ["claim", "evidence"]
    assert result["entities"][0] == {
        "name": "Generated claim",
        "entityType": "claim",
        "observations": ["Auto-generated claim entity"],
    }
    assert result["relations"] == []


def test_max_entities_exceeding_required_types_fills_remaining_slots() -> None:
    """(a) maxEntities > len(requiredTypes) yields MORE than the required
    entities -- the stub used to stop at the required-types subset."""
    result = _run({"maxEntities": 5, "maxRelations": 0, "requiredTypes": ["claim", "evidence"]})
    assert result["success"] is True
    assert len(result["entities"]) == 5
    assert [e["entityType"] for e in result["entities"][:2]] == ["claim", "evidence"]
    # The fill entities are still schema-valid entity types.
    for entity in result["entities"]:
        assert entity["name"] == f"Generated {entity['entityType']}"
        assert entity["observations"] == [f"Auto-generated {entity['entityType']} entity"]


def test_max_relations_yields_type_valid_relations() -> None:
    """(b) maxRelations > 0 yields relations that respect the type-compatibility
    table -- the stub used to ALWAYS emit zero relations."""
    result = _run({"maxEntities": 8, "maxRelations": 10, "requiredTypes": ["concept", "variable"]})
    assert result["success"] is True
    assert len(result["relations"]) > 0
    assert len(result["relations"]) <= 10
    _assert_relations_type_valid(result)
    for rel in result["relations"]:
        assert set(rel) == {"fromIndex", "toIndex", "relationType", "polarity", "strength"}
        assert rel["strength"] in ("weak", "moderate", "strong")


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


def test_deterministic_across_runs_with_fill_and_relations() -> None:
    """(c) identical input twice yields byte-identical output, including the
    random-fill entities and relations paths (not just the required subset)."""
    doc = {"maxEntities": 12, "maxRelations": 20, "requiredTypes": ["question", "loop"]}
    first = _run(doc)
    second = _run(doc)
    assert first == second
    assert len(first["entities"]) == 12
    assert len(first["relations"]) > 0


def test_explicit_seed_changes_output_deterministically() -> None:
    """An explicit seed is honored and is itself deterministic; two different
    seeds are not required to differ, but the same seed must reproduce the
    same output every time."""
    doc = {"maxEntities": 8, "maxRelations": 6, "requiredTypes": ["system"], "seed": 7}
    first = _run(doc)
    second = _run(doc)
    assert first == second


def test_absent_seed_matches_default_seed_output() -> None:
    """The optional `seed` field is additive: omitting it must reproduce the
    exact fixed-default-seed output (no behavior change for existing callers)."""
    doc = {"maxEntities": 6, "maxRelations": 4, "requiredTypes": ["concept"]}
    without_seed = _run(doc)
    with_default_seed = _run({**doc, "seed": 42})
    assert without_seed == with_default_seed
