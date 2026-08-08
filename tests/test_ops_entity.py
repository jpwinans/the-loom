"""Entity operations tests.

Covers the semantics the ops layer adds on top of the store: verification-gate
warnings appended as observations, revision auto-population (version=1 /
changeType / previousVersionId self-reference on update), confidence/provenance
date auto-population, changeType auto-detection precedence, statusChangedAt,
replacedById → auto supersedes relation, include* status flags, graph="*"
wildcard, list-entities' two output shapes (bare array without `limit`, the
items/truncated envelope with it), and read-entities-by-name partitioning.
"""

from __future__ import annotations

import pytest

from theloom.cli.registry import run_handler
from theloom.errors import LoomError, NotFoundError
from theloom.operations.entity import (
    CreateEntityInput,
    DeleteEntityInput,
    ListEntitiesInput,
    ReadEntitiesByNameInput,
    ReadEntityInput,
    UpdateEntityInput,
    create_entity,
    delete_entity,
    list_entities,
    read_entities_by_name,
    read_entity,
    update_entity,
)
from theloom.store.multigraph import MultiGraph

MISSING = "00000000-0000-4000-8000-000000000000"


def make(multi: MultiGraph, name: str = "Systems Thinking", **overrides: object) -> dict:
    base: dict[str, object] = {
        "name": name,
        "entityType": "concept",
        "observations": [f"observation about {name}"],
    }
    base.update(overrides)
    result = create_entity(CreateEntityInput.model_validate(base), multi)
    assert isinstance(result, dict)
    return result


# =============================================================================
# create-entity
# =============================================================================


def test_create_auto_populates_revision_fields(multi: MultiGraph) -> None:
    entity = make(multi)
    assert entity["version"] == 1
    assert entity["changeType"] == "created"
    assert entity["previousVersionId"] is None
    assert entity["changeReason"] is None


def test_create_empty_observations_appends_guard_warning(multi: MultiGraph) -> None:
    entity = make(multi, observations=[])
    assert entity["observations"] == [
        "[guard:OBSERVATIONS_REQUIRED] Entity must have at least one observation"
    ]


def test_create_duplicate_name_appends_guard_warning_with_partial_match(
    multi: MultiGraph,
) -> None:
    first = make(multi, "Systems Thinking Fundamentals")
    # The duplicate-name guard matches on a *partial*, case-insensitive name —
    # so a substring name also draws the warning.
    second = make(multi, "systems thinking")
    assert second["observations"][-1] == (
        f"[guard:DUPLICATE_NAME] An entity with name 'systems thinking' "
        f"already exists (id: {first['id']})"
    )


def test_create_auto_populates_confidence_and_provenance_dates(multi: MultiGraph) -> None:
    entity = make(
        multi,
        confidence={"score": 0.8, "basis": "multiple_sources"},
        provenance={
            "sourceType": "document",
            "sourceId": None,
            "externalRef": None,
            "extractor": "test",
            "extractionMethod": "manual",
        },
    )
    assert entity["confidence"]["lastEvaluated"].endswith("Z")
    assert entity["provenance"]["extractionDate"].endswith("Z")


def test_create_passes_through_3d_fields(multi: MultiGraph) -> None:
    entity = make(multi, memoryType="knowledge", domain="research", durability="stable")
    assert entity["memoryType"] == "knowledge"
    assert entity["domain"] == "research"
    assert entity["durability"] == "stable"


def test_create_into_named_graph(multi: MultiGraph) -> None:
    multi.create_graph("research")
    entity = make(multi, graph="research")
    assert multi.get_store("research").read_entity(entity["id"]) is not None
    assert multi.get_store("default").read_entity(entity["id"]) is None


# =============================================================================
# read-entity / delete-entity
# =============================================================================


def test_read_entity_not_found_raises(multi: MultiGraph) -> None:
    with pytest.raises(NotFoundError):
        read_entity(ReadEntityInput.model_validate({"id": MISSING}), multi)


def test_read_entity_compact_projects_to_five_fields(multi: MultiGraph) -> None:
    entity = make(multi)
    result = read_entity(
        ReadEntityInput.model_validate({"id": entity["id"], "compact": True}), multi
    )
    assert set(result) == {"id", "name", "entityType", "status", "observations"}
    assert result["id"] == entity["id"]
    assert result["name"] == entity["name"]


def test_read_entity_without_compact_is_unchanged(multi: MultiGraph) -> None:
    entity = make(multi)
    result = read_entity(ReadEntityInput.model_validate({"id": entity["id"]}), multi)
    assert result == entity


def test_delete_retracts_by_default_and_preserves_the_record(multi: MultiGraph) -> None:
    entity = make(multi)
    retracted = delete_entity(DeleteEntityInput.model_validate({"id": entity["id"]}), multi)
    assert retracted["id"] == entity["id"]
    assert retracted["status"] == "retracted"
    # still readable by id — history is preserved, not erased
    assert (
        read_entity(ReadEntityInput.model_validate({"id": entity["id"]}), multi)["id"]
        == (entity["id"])
    )
    with pytest.raises(NotFoundError):
        delete_entity(DeleteEntityInput.model_validate({"id": MISSING}), multi)


def test_hard_delete_removes_the_entity(multi: MultiGraph) -> None:
    entity = make(multi)
    deleted = delete_entity(
        DeleteEntityInput.model_validate({"id": entity["id"], "hard": True}), multi
    )
    assert deleted["id"] == entity["id"]
    with pytest.raises(NotFoundError):
        read_entity(ReadEntityInput.model_validate({"id": entity["id"]}), multi)
    with pytest.raises(NotFoundError):
        delete_entity(DeleteEntityInput.model_validate({"id": entity["id"], "hard": True}), multi)


# =============================================================================
# update-entity
# =============================================================================


def test_update_increments_version_and_self_references(multi: MultiGraph) -> None:
    entity = make(multi)
    result = update_entity(
        UpdateEntityInput.model_validate({"id": entity["id"], "observations": ["new"]}), multi
    )
    updated = result["entity"]
    assert result["supersedesRelation"] is None
    assert updated["version"] == 2
    # previousVersionId is a self-reference on the wire deliberately;
    # real history lives in the event log + version snapshots.
    assert updated["previousVersionId"] == entity["id"]
    assert updated["changeType"] == "content_updated"


@pytest.mark.parametrize(
    ("extra", "expected_change_type"),
    [
        ({"confidence": {"score": 0.5, "basis": "inference"}}, "confidence_updated"),
        ({"status": "investigating"}, "status_changed"),
        ({"name": "Renamed"}, "content_updated"),
        (
            {"confidence": {"score": 0.5, "basis": "inference"}, "status": "investigating"},
            "confidence_updated",  # confidence wins in the precedence
        ),
    ],
)
def test_update_change_type_autodetect(
    multi: MultiGraph, extra: dict, expected_change_type: str
) -> None:
    entity = make(multi)
    result = update_entity(UpdateEntityInput.model_validate({"id": entity["id"], **extra}), multi)
    assert result["entity"]["changeType"] == expected_change_type


def test_update_status_sets_status_changed_at(multi: MultiGraph) -> None:
    entity = make(multi)
    result = update_entity(
        UpdateEntityInput.model_validate({"id": entity["id"], "status": "deprecated"}), multi
    )
    assert result["entity"]["statusChangedAt"].endswith("Z")


def test_update_invalid_transition_is_validation_error(multi: MultiGraph) -> None:
    entity = make(multi)
    update_entity(
        UpdateEntityInput.model_validate({"id": entity["id"], "status": "retracted"}), multi
    )
    with pytest.raises(LoomError) as excinfo:
        update_entity(
            UpdateEntityInput.model_validate({"id": entity["id"], "status": "active"}), multi
        )
    assert excinfo.value.code == "VALIDATION_ERROR"


def test_update_superseded_with_replaced_by_creates_supersedes_relation(
    multi: MultiGraph,
) -> None:
    old = make(multi, "Old Model")
    new = make(multi, "New Model")
    result = update_entity(
        UpdateEntityInput.model_validate(
            {"id": old["id"], "status": "superseded", "replacedById": new["id"]}
        ),
        multi,
    )
    relation = result["supersedesRelation"]
    assert relation is not None
    assert relation["from"] == new["id"]
    assert relation["to"] == old["id"]
    assert relation["relationType"] == "supersedes"
    assert relation["strength"] == "strong"


def test_update_replaced_by_missing_raises_not_found(multi: MultiGraph) -> None:
    old = make(multi)
    with pytest.raises(NotFoundError):
        update_entity(
            UpdateEntityInput.model_validate(
                {"id": old["id"], "status": "superseded", "replacedById": MISSING}
            ),
            multi,
        )


# =============================================================================
# list-entities / read-entities-by-name
# =============================================================================


def test_list_include_flags_extend_status_filter(multi: MultiGraph) -> None:
    active = make(multi, "Active")
    deprecated = make(multi, "Deprecated One")
    update_entity(
        UpdateEntityInput.model_validate({"id": deprecated["id"], "status": "deprecated"}),
        multi,
    )
    default = list_entities(ListEntitiesInput.model_validate({}), multi)
    assert [e["id"] for e in default["items"]] == [active["id"]]
    with_deprecated = list_entities(
        ListEntitiesInput.model_validate({"includeDeprecated": True}), multi
    )
    assert {e["id"] for e in with_deprecated["items"]} == {active["id"], deprecated["id"]}


def test_list_wildcard_graph_annotates_graph(multi: MultiGraph) -> None:
    multi.create_graph("research")
    make(multi, "In Default")
    make(multi, "In Research", graph="research")
    result = list_entities(ListEntitiesInput.model_validate({"graph": "*"}), multi)
    graphs = {e["name"]: e["graph"] for e in result["items"]}
    assert graphs == {"In Default": "default", "In Research": "research"}


def test_list_wildcard_compact_keeps_the_graph_key(multi: MultiGraph) -> None:
    """Compaction must not strip the wildcard disambiguator — two same-named
    entities in different graphs stay distinguishable."""
    multi.create_graph("research")
    make(multi, "X")
    make(multi, "X", graph="research")
    result = list_entities(ListEntitiesInput.model_validate({"graph": "*", "compact": True}), multi)
    assert isinstance(result, dict)
    items = result["items"]
    assert {e["graph"] for e in items} == {"default", "research"}
    fields = {"id", "name", "entityType", "status", "observations", "graph"}
    assert all(set(e) == fields for e in items)


def test_list_compact_projects_each_entity_to_five_fields(multi: MultiGraph) -> None:
    make(multi, "One")
    make(multi, "Two")
    result = list_entities(ListEntitiesInput.model_validate({"compact": True}), multi)
    assert isinstance(result, dict)
    items = result["items"]
    assert [e["name"] for e in items] == ["One", "Two"]
    assert all(set(e) == {"id", "name", "entityType", "status", "observations"} for e in items)


def test_list_compact_composes_with_limit(multi: MultiGraph) -> None:
    for name in ("One", "Two", "Three"):
        make(multi, name)
    result = list_entities(ListEntitiesInput.model_validate({"compact": True, "limit": 2}), multi)
    assert isinstance(result, dict)
    assert [e["name"] for e in result["items"]] == ["One", "Two"]
    fields = {"id", "name", "entityType", "status", "observations"}
    assert all(set(e) == fields for e in result["items"])
    assert "3" in result["notices"][0]["message"]


def test_list_without_limit_carries_no_truncation_notice(multi: MultiGraph) -> None:
    make(multi, "One")
    make(multi, "Two")
    result = list_entities(ListEntitiesInput.model_validate({}), multi)
    assert isinstance(result, dict)
    assert result["count"] == 2
    assert [e["name"] for e in result["items"]] == ["One", "Two"]
    assert "notices" not in result


def test_list_with_limit_returns_the_uniform_envelope(multi: MultiGraph) -> None:
    for name in ("One", "Two", "Three"):
        make(multi, name)
    result = list_entities(ListEntitiesInput.model_validate({"limit": 2}), multi)
    assert isinstance(result, dict)
    assert set(result) == {"items", "count", "notices"}
    assert result["count"] == 2
    assert [e["name"] for e in result["items"]] == ["One", "Two"]
    assert result["notices"] == [
        {
            "code": "TRUNCATED",
            "message": "Showing 2 of 3 matching entities.",
            "hint": "raise limit or narrow with entityType/query",
        }
    ]


def test_list_with_a_generous_limit_reports_no_truncation(multi: MultiGraph) -> None:
    make(multi, "Only")
    result = list_entities(ListEntitiesInput.model_validate({"limit": 50}), multi)
    assert isinstance(result, dict)
    assert result["count"] == 1
    assert "notices" not in result


def test_list_limit_composes_with_the_other_filters(multi: MultiGraph) -> None:
    make(multi, "Concept A")
    make(multi, "Concept B")
    make(multi, "A Pattern", entityType="pattern")
    result = list_entities(
        ListEntitiesInput.model_validate({"entityType": "concept", "limit": 1}), multi
    )
    assert isinstance(result, dict)
    assert [e["name"] for e in result["items"]] == ["Concept A"]
    assert "2" in result["notices"][0]["message"]


def test_list_limit_totals_across_the_wildcard_graph(multi: MultiGraph) -> None:
    multi.create_graph("research")
    make(multi, "In Default")
    make(multi, "In Research", graph="research")
    result = list_entities(ListEntitiesInput.model_validate({"graph": "*", "limit": 1}), multi)
    assert isinstance(result, dict)
    assert [e["name"] for e in result["items"]] == ["In Default"]
    assert result["notices"] == [
        {
            "code": "TRUNCATED",
            "message": "Showing 1 of 2 matching entities.",
            "hint": "raise limit or narrow with entityType/query",
        }
    ]


def test_list_limit_rejects_zero(multi: MultiGraph) -> None:
    with pytest.raises(ValueError):
        ListEntitiesInput.model_validate({"limit": 0})


def test_list_entities_shapes_through_the_registry(multi: MultiGraph) -> None:
    make(multi, "One")
    make(multi, "Two")
    bare = run_handler("list-entities", {}, multi)
    assert isinstance(bare, dict)
    assert [e["name"] for e in bare["items"]] == ["One", "Two"]
    assert "notices" not in bare
    capped = run_handler("list-entities", {"limit": 1}, multi)
    assert isinstance(capped, dict)
    assert [e["name"] for e in capped["items"]] == ["One"]
    assert capped["notices"] == [
        {
            "code": "TRUNCATED",
            "message": "Showing 1 of 2 matching entities.",
            "hint": "raise limit or narrow with entityType/query",
        }
    ]


def test_read_entities_by_name_partitions(multi: MultiGraph) -> None:
    entity = make(multi, "Known Entity")
    result = read_entities_by_name(
        ReadEntitiesByNameInput.model_validate({"names": ["Known Entity", "Ghost"]}), multi
    )
    assert result == {"resolved": {"Known Entity": entity["id"]}, "unresolved": ["Ghost"]}


def test_read_entities_by_name_empty_is_trivial(multi: MultiGraph) -> None:
    result = read_entities_by_name(ReadEntitiesByNameInput.model_validate({"names": []}), multi)
    assert result == {"resolved": {}, "unresolved": []}
