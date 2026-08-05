"""Name-first addressing for entity-addressed reads.

Every entity-addressed read command accepts a symbol NAME instead of a UUID.
Resolution is exact-match-first (case-insensitive), falling back to a unique
case-insensitive substring match; ambiguity is refused with a candidate listing
so the agent can retry by id, and a miss is NOT_FOUND. Exactly one of the id
param or ``name`` is required.
"""

from __future__ import annotations

from typing import Any

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.composites.entity_deep_dive import EntityDeepDiveInput, entity_deep_dive
from theloom.errors import NotFoundError, ValidationError
from theloom.operations.analysis import FindShortestPathInput, find_shortest_path
from theloom.operations.common import resolve_entity_ref
from theloom.operations.entity import (
    CreateEntityInput,
    ReadEntityInput,
    UpdateEntityInput,
    create_entity,
    read_entity,
    update_entity,
)
from theloom.operations.relations import (
    CreateRelationInput,
    GetNeighborsInput,
    GetRelationsInput,
    create_relation,
    get_neighbors,
    get_relations,
)
from theloom.operations.synthesis import ExplainPathInput
from theloom.store.multigraph import MultiGraph


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


def ent(multi: MultiGraph, name: str, observations: list[str] | None = None) -> str:
    doc: dict[str, Any] = {
        "name": name,
        "entityType": "concept",
        "observations": observations if observations is not None else [name],
    }
    result = create_entity(CreateEntityInput.model_validate(doc), multi)
    return str(result["id"])


def set_status(multi: MultiGraph, entity_id: str, status: str) -> None:
    update_entity(
        UpdateEntityInput.model_validate({"id": entity_id, "status": status}),
        multi,
    )


def rel(multi: MultiGraph, from_id: str, to_id: str) -> None:
    create_relation(
        CreateRelationInput.model_validate(
            {
                "from": from_id,
                "to": to_id,
                "relationType": "related_to",
                "polarity": None,
                "strength": "moderate",
                "evidence": "test",
            }
        ),
        multi,
    )


# =============================================================================
# The shared resolver
# =============================================================================


def test_resolver_exact_match_is_case_insensitive(multi: MultiGraph) -> None:
    target = ent(multi, "RunPipeline")
    ent(multi, "RunPipelineHelper")
    store = multi.get_store(None)
    assert resolve_entity_ref(store, entity_id=None, name="runpipeline") == target


def test_resolver_unique_substring_match(multi: MultiGraph) -> None:
    target = ent(multi, "TapestryBundleAssembler")
    ent(multi, "unrelated")
    store = multi.get_store(None)
    assert resolve_entity_ref(store, entity_id=None, name="bundleassembler") == target


def test_resolver_ambiguous_substring_lists_candidates(multi: MultiGraph) -> None:
    a = ent(multi, "run_pipeline", ["File path: theloom/a.py", "does a thing"])
    b = ent(multi, "run_extraction", ["File path: theloom/b.py"])
    store = multi.get_store(None)
    with pytest.raises(ValidationError) as excinfo:
        resolve_entity_ref(store, entity_id=None, name="run")
    message = str(excinfo.value)
    assert excinfo.value.code == "VALIDATION_ERROR"
    for entity_id, name, path in (
        (a, "run_pipeline", "theloom/a.py"),
        (b, "run_extraction", "theloom/b.py"),
    ):
        assert f"{name} [concept] id={entity_id} File path: {path}" in message


def test_resolver_ambiguous_exact_match_refuses(multi: MultiGraph) -> None:
    """Two entities with the SAME name: exact match is ambiguous, not a guess."""
    a = ent(multi, "run")
    b = ent(multi, "run")
    store = multi.get_store(None)
    with pytest.raises(ValidationError) as excinfo:
        resolve_entity_ref(store, entity_id=None, name="run")
    assert a in str(excinfo.value)
    assert b in str(excinfo.value)


def test_resolver_missing_is_not_found(multi: MultiGraph) -> None:
    ent(multi, "something")
    store = multi.get_store(None)
    with pytest.raises(NotFoundError) as excinfo:
        resolve_entity_ref(store, entity_id=None, name="nothing-like-this")
    assert excinfo.value.code == "NOT_FOUND"


def test_resolver_requires_exactly_one(multi: MultiGraph) -> None:
    target = ent(multi, "solo")
    store = multi.get_store(None)
    with pytest.raises(ValidationError) as excinfo:
        resolve_entity_ref(store, entity_id=None, name=None)
    assert excinfo.value.code == "VALIDATION_ERROR"
    with pytest.raises(ValidationError):
        resolve_entity_ref(store, entity_id=target, name="solo")
    assert resolve_entity_ref(store, entity_id=target, name=None) == target


@pytest.mark.parametrize("status", ["superseded", "deprecated", "retracted", "investigating"])
def test_resolver_reaches_non_active_entities(multi: MultiGraph, status: str) -> None:
    """Status transitions are first-class state: a name must address the same
    entities an id does, whatever their status."""
    target = ent(multi, f"lifecycle_{status}")
    set_status(multi, target, status)
    store = multi.get_store(None)
    assert resolve_entity_ref(store, entity_id=None, name=f"lifecycle_{status}") == target


def test_resolver_prefers_active_over_non_active(multi: MultiGraph) -> None:
    """Same name, one active and one superseded: the live one wins rather than
    the pair reading as ambiguous."""
    old = ent(multi, "renderer")
    set_status(multi, old, "superseded")
    live = ent(multi, "renderer")
    store = multi.get_store(None)
    assert resolve_entity_ref(store, entity_id=None, name="renderer") == live


def test_non_active_entity_addressable_by_name_in_commands(multi: MultiGraph) -> None:
    """Name and id addressing agree for a superseded entity across commands."""
    a = ent(multi, "alpha")
    b = ent(multi, "beta")
    rel(multi, a, b)
    set_status(multi, a, "superseded")
    assert read_entity(ReadEntityInput.model_validate({"name": "alpha"}), multi)["id"] == a
    assert get_relations(
        GetRelationsInput.model_validate({"name": "alpha"}), multi
    ) == get_relations(GetRelationsInput.model_validate({"entityId": a}), multi)
    assert get_neighbors(
        GetNeighborsInput.model_validate({"name": "alpha"}), multi
    ) == get_neighbors(GetNeighborsInput.model_validate({"entityId": a}), multi)
    dive = entity_deep_dive(EntityDeepDiveInput.model_validate({"name": "alpha"}), multi)
    assert dive["result"]["entity"]["data"]["id"] == a


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_resolver_rejects_blank_name(multi: MultiGraph, blank: str) -> None:
    """A blank name is a missing name, not a match-everything query — even when
    the store holds exactly one entity that it could silently resolve to."""
    ent(multi, "solo")
    store = multi.get_store(None)
    with pytest.raises(ValidationError) as excinfo:
        resolve_entity_ref(store, entity_id=None, name=blank)
    assert excinfo.value.code == "VALIDATION_ERROR"
    assert "exactly one" in str(excinfo.value)


def test_blank_name_rejected_by_commands(multi: MultiGraph) -> None:
    ent(multi, "solo")
    with pytest.raises(ValidationError):
        read_entity(ReadEntityInput.model_validate({"name": ""}), multi)
    with pytest.raises(ValidationError):
        get_relations(GetRelationsInput.model_validate({"name": "  "}), multi)


def test_resolver_uses_server_side_filter(multi: MultiGraph, monkeypatch: Any) -> None:
    """The resolver narrows server-side — it never lists the whole graph."""
    target = ent(multi, "narrowed")
    store = multi.get_store(None)
    seen: list[Any] = []
    original = store.list_entities

    def spy(filter: Any = None) -> Any:
        seen.append(filter)
        return original(filter)

    monkeypatch.setattr(store, "list_entities", spy)
    assert resolve_entity_ref(store, entity_id=None, name="narrowed") == target
    assert seen and all(f is not None and f.name == "narrowed" for f in seen)


# =============================================================================
# Commands
# =============================================================================


def test_read_entity_by_name(multi: MultiGraph) -> None:
    target = ent(multi, "Widget")
    result = read_entity(ReadEntityInput.model_validate({"name": "widget"}), multi)
    assert result["id"] == target


def test_read_entity_requires_exactly_one(multi: MultiGraph) -> None:
    target = ent(multi, "Widget")
    with pytest.raises(ValidationError):
        read_entity(ReadEntityInput.model_validate({}), multi)
    with pytest.raises(ValidationError):
        read_entity(ReadEntityInput.model_validate({"id": target, "name": "Widget"}), multi)


def test_get_relations_by_name(multi: MultiGraph) -> None:
    a = ent(multi, "alpha")
    b = ent(multi, "beta")
    rel(multi, a, b)
    by_id = get_relations(GetRelationsInput.model_validate({"entityId": a}), multi)
    by_name = get_relations(GetRelationsInput.model_validate({"name": "alpha"}), multi)
    assert by_name == by_id
    with pytest.raises(ValidationError):
        get_relations(GetRelationsInput.model_validate({}), multi)


def test_get_neighbors_by_name(multi: MultiGraph) -> None:
    a = ent(multi, "alpha")
    b = ent(multi, "beta")
    rel(multi, a, b)
    by_id = get_neighbors(GetNeighborsInput.model_validate({"entityId": a}), multi)
    by_name = get_neighbors(GetNeighborsInput.model_validate({"name": "alpha"}), multi)
    assert by_name == by_id
    assert [n["id"] for n in by_name] == [b]


def test_entity_deep_dive_by_name(multi: MultiGraph) -> None:
    a = ent(multi, "alpha")
    b = ent(multi, "beta")
    rel(multi, a, b)
    result = entity_deep_dive(EntityDeepDiveInput.model_validate({"name": "alpha"}), multi)
    assert result["result"]["entity"]["data"]["id"] == a
    with pytest.raises(ValidationError):
        entity_deep_dive(EntityDeepDiveInput.model_validate({}), multi)


def test_find_shortest_path_by_name(multi: MultiGraph) -> None:
    a = ent(multi, "alpha")
    b = ent(multi, "beta")
    rel(multi, a, b)
    result = find_shortest_path(
        FindShortestPathInput.model_validate({"sourceName": "alpha", "targetName": "beta"}), multi
    )
    assert result["path"] == [a, b]
    mixed = find_shortest_path(
        FindShortestPathInput.model_validate({"source": a, "targetName": "beta"}), multi
    )
    assert mixed["path"] == [a, b]
    with pytest.raises(ValidationError):
        find_shortest_path(FindShortestPathInput.model_validate({"source": a}), multi)


def test_find_shortest_path_name_ambiguity_refuses(multi: MultiGraph) -> None:
    ent(multi, "run_a")
    ent(multi, "run_b")
    target = ent(multi, "beta")
    with pytest.raises(ValidationError) as excinfo:
        find_shortest_path(
            FindShortestPathInput.model_validate({"sourceName": "run", "target": target}), multi
        )
    assert "run_a" in str(excinfo.value)


def test_explain_path_accepts_names(multi: MultiGraph) -> None:
    """Input model shape: names are accepted and ids stay optional."""
    params = ExplainPathInput.model_validate({"sourceName": "alpha", "targetName": "beta"})
    assert params.source_name == "alpha"
    assert params.target_name == "beta"
    assert params.source_id is None
