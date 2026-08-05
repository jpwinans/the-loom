"""Relation operations tests.

Covers polarity auto-inference (CAUSAL_POLARITY_DEFAULTS), the verification
gate running BEFORE the bridge branch (so cross-graph create-relation is
blocked), batch aggregate counts with continueOnError, the explicit-null
polarity filter on list-relations, and bridge inclusion in get-relations /
get-neighbors (with follow_bridges).
"""

from __future__ import annotations

import pytest

from theloom.errors import LoomError, NotFoundError, OperationError
from theloom.model import RelationCreate
from theloom.operations.entity import (
    CreateEntityInput,
    DeleteEntityInput,
    create_entity,
    delete_entity,
)
from theloom.operations.relations import (
    CreateRelationInput,
    CreateRelationsInput,
    DeleteRelationInput,
    GetNeighborsInput,
    GetRelationsInput,
    ListRelationsInput,
    ReadRelationInput,
    ReadRelationsInput,
    UpdateRelationInput,
    create_relation,
    create_relations,
    delete_relation,
    get_neighbors,
    get_relations,
    list_relations,
    read_relation,
    read_relations,
    update_relation,
)
from theloom.operations.verification import (
    GraphOnlyInput,
    ListGuardViolationsInput,
    check_consistency,
    list_guard_violations,
)
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph

MISSING = "00000000-0000-4000-8000-000000000000"


def ent(multi: MultiGraph, name: str, graph: str | None = None) -> str:
    doc: dict[str, object] = {"name": name, "entityType": "concept", "observations": [name]}
    if graph:
        doc["graph"] = graph
    result = create_entity(CreateEntityInput.model_validate(doc), multi)
    return str(result["id"])


def rel_input(from_id: str, to_id: str, relation_type: str = "related_to", **kw: object) -> dict:
    return {
        "from": from_id,
        "to": to_id,
        "relationType": relation_type,
        "polarity": None,
        "strength": "moderate",
        "evidence": None,
        **kw,
    }


# =============================================================================
# create-relation
# =============================================================================


def test_create_infers_causal_polarity(multi: MultiGraph) -> None:
    a, b = ent(multi, "A"), ent(multi, "B")
    result = create_relation(CreateRelationInput.model_validate(rel_input(a, b, "causes")), multi)
    assert result["polarity"] == "+"  # CAUSAL_POLARITY_DEFAULTS.causes
    inhibits = create_relation(
        CreateRelationInput.model_validate(rel_input(b, a, "inhibits")), multi
    )
    assert inhibits["polarity"] == "-"


def test_create_structural_keeps_null_polarity(multi: MultiGraph) -> None:
    a, b = ent(multi, "A"), ent(multi, "B")
    result = create_relation(CreateRelationInput.model_validate(rel_input(a, b, "supports")), multi)
    assert result["polarity"] is None
    assert "_bridge_created" not in result


@pytest.mark.parametrize("relation_type", ["calls", "references"])
def test_create_code_relation_keeps_null_polarity(multi: MultiGraph, relation_type: str) -> None:
    a, b = ent(multi, "A"), ent(multi, "B")
    result = create_relation(
        CreateRelationInput.model_validate(rel_input(a, b, relation_type)), multi
    )
    assert result["relationType"] == relation_type
    assert result["polarity"] is None


@pytest.mark.parametrize("relation_type", ["calls", "references"])
def test_create_code_relation_rejects_polarity(multi: MultiGraph, relation_type: str) -> None:
    a, b = ent(multi, "A"), ent(multi, "B")
    with pytest.raises(OperationError) as excinfo:
        create_relation(
            CreateRelationInput.model_validate(rel_input(a, b, relation_type, polarity="+")),
            multi,
        )
    assert "verification gate" in str(excinfo.value)
    assert "must not have polarity" in str(excinfo.value)


@pytest.mark.parametrize(
    "relation_type",
    [
        "related_to",
        "instance_of",
        "part_of",
        "sources",
        "crystallized_from",
        "supports",
        "contradicts",
        "questions",
        "supersedes",
    ],
)
def test_create_rejects_polarity_on_every_non_causal_type(
    multi: MultiGraph, relation_type: str
) -> None:
    """The partition is model-wide: polarity belongs to causal types only, so
    every structural/epistemic type rejects it, not just calls/references."""
    a, b = ent(multi, "A"), ent(multi, "B")
    with pytest.raises(OperationError) as excinfo:
        create_relation(
            CreateRelationInput.model_validate(rel_input(a, b, relation_type, polarity="+")),
            multi,
        )
    assert "must not have polarity" in str(excinfo.value)


def test_batch_code_relation_rejects_polarity(multi: MultiGraph) -> None:
    a, b, c = ent(multi, "A"), ent(multi, "B"), ent(multi, "C")
    result = create_relations(
        CreateRelationsInput.model_validate(
            {
                "relations": [
                    rel_input(a, b, "calls", polarity="+"),
                    rel_input(b, c, "references"),
                ]
            }
        ),
        multi,
    )
    assert result["applied"] == 1
    assert result["failed"] == 1
    assert "must not have polarity" in result["errors"][0]["error"]


def test_create_self_loop_blocked_by_gate(multi: MultiGraph) -> None:
    a = ent(multi, "A")
    with pytest.raises(OperationError) as excinfo:
        create_relation(CreateRelationInput.model_validate(rel_input(a, a)), multi)
    assert "verification gate" in str(excinfo.value)
    # A self-loop is a validation failure, not a missing entity — the
    # "verify both entities exist" hint must not be appended. This is decided
    # by reading the store directly (both endpoints exist here), not by
    # pattern-matching the gate's own error prose.
    assert "Use list_entities to verify both entities exist" not in str(excinfo.value)


def test_create_missing_endpoint_blocked_by_gate(multi: MultiGraph) -> None:
    a = ent(multi, "A")
    with pytest.raises(OperationError) as excinfo:
        create_relation(CreateRelationInput.model_validate(rel_input(a, MISSING)), multi)
    assert "does not exist" in str(excinfo.value)
    assert "Use list_entities to verify both entities exist" in str(excinfo.value)


def test_create_to_retracted_endpoint_blocked_by_gate(multi: MultiGraph) -> None:
    """A retracted entity is not a usable endpoint.

    delete-entity retracts (the doc survives, so ``read_entity`` still answers),
    but retraction closes out every attached edge — attaching a new one would
    manufacture the exact state ``check-invariants`` reports as a
    ``retractedIsolated`` violation.
    """
    a = ent(multi, "A")
    b = ent(multi, "B")
    delete_entity(DeleteEntityInput.model_validate({"id": b}), multi)
    with pytest.raises(OperationError) as excinfo:
        create_relation(CreateRelationInput.model_validate(rel_input(a, b)), multi)
    assert "retracted" in str(excinfo.value)
    # ...and the reverse direction is blocked too.
    with pytest.raises(OperationError):
        create_relation(CreateRelationInput.model_validate(rel_input(b, a)), multi)
    assert list_relations(ListRelationsInput.model_validate({}), multi) == []


def test_create_cross_graph_blocked_by_gate_like_reference(multi: MultiGraph) -> None:
    # The gate checks the resolved single store BEFORE the bridge branch,
    # so a cross-graph relation errors.
    multi.create_graph("research")
    a = ent(multi, "A")
    b = ent(multi, "B", graph="research")
    with pytest.raises(OperationError):
        create_relation(CreateRelationInput.model_validate(rel_input(a, b)), multi)
    assert multi.bridges.list_bridges() == []


# =============================================================================
# create-relations (batch)
# =============================================================================


def test_batch_counts_and_errors(multi: MultiGraph) -> None:
    a, b, c = ent(multi, "A"), ent(multi, "B"), ent(multi, "C")
    result = create_relations(
        CreateRelationsInput.model_validate(
            {
                "relations": [
                    rel_input(a, b, "supports"),
                    rel_input(b, MISSING),  # gate error, batch continues
                    rel_input(b, c, "causes"),
                ]
            }
        ),
        multi,
    )
    assert result["applied"] == 2
    assert result["failed"] == 1
    assert result["bridgesCreated"] == 0
    assert len(result["errors"]) == 1
    assert result["errors"][0]["from"] == b
    assert result["errors"][0]["to"] == MISSING


def test_batch_continue_on_error_false_throws(multi: MultiGraph) -> None:
    a, b = ent(multi, "A"), ent(multi, "B")
    with pytest.raises(LoomError):
        create_relations(
            CreateRelationsInput.model_validate(
                {
                    "relations": [rel_input(a, MISSING), rel_input(a, b)],
                    "continueOnError": False,
                }
            ),
            multi,
        )
    # first item failed and aborted the batch — nothing was created
    assert list_relations(ListRelationsInput.model_validate({}), multi) == []


def test_batch_to_retracted_endpoint_blocked_like_the_single_command(multi: MultiGraph) -> None:
    """Batch and single are the same operation at different arity: the gate's
    verdict on an endpoint cannot depend on which one the caller reached for.
    A retracted endpoint is refused by both, with the same message."""
    a, b = ent(multi, "A"), ent(multi, "B")
    c = ent(multi, "C")
    delete_entity(DeleteEntityInput.model_validate({"id": b}), multi)

    result = create_relations(
        CreateRelationsInput.model_validate(
            {"relations": [rel_input(a, b), rel_input(b, c), rel_input(a, c)]}
        ),
        multi,
    )
    assert result["applied"] == 1
    assert result["failed"] == 2
    assert "is retracted and cannot be a relation endpoint" in result["errors"][0]["error"]
    assert f"Target entity '{b}'" in result["errors"][0]["error"]
    assert f"Source entity '{b}'" in result["errors"][1]["error"]
    # Only the a -> c relation landed.
    surviving = list_relations(ListRelationsInput.model_validate({}), multi)
    assert [(r["from"], r["to"]) for r in surviving] == [(a, c)]


def test_batch_retracted_endpoint_aborts_when_continue_on_error_false(multi: MultiGraph) -> None:
    a, b = ent(multi, "A"), ent(multi, "B")
    delete_entity(DeleteEntityInput.model_validate({"id": b}), multi)
    with pytest.raises(OperationError) as excinfo:
        create_relations(
            CreateRelationsInput.model_validate(
                {"relations": [rel_input(a, b)], "continueOnError": False}
            ),
            multi,
        )
    assert "retracted" in str(excinfo.value)
    assert list_relations(ListRelationsInput.model_validate({}), multi) == []


# =============================================================================
# read / update / delete / list
# =============================================================================


def test_read_relation_not_found_raises(multi: MultiGraph) -> None:
    a, b = ent(multi, "A"), ent(multi, "B")
    with pytest.raises(NotFoundError):
        read_relation(ReadRelationInput.model_validate({"from": a, "to": b}), multi)


def test_update_relation_fields(multi: MultiGraph) -> None:
    a, b = ent(multi, "A"), ent(multi, "B")
    create_relation(CreateRelationInput.model_validate(rel_input(a, b, "supports")), multi)
    updated = update_relation(
        UpdateRelationInput.model_validate(
            {"from": a, "to": b, "strength": "strong", "evidence": "new evidence"}
        ),
        multi,
    )
    assert updated["strength"] == "strong"
    assert updated["evidence"] == "new evidence"


@pytest.mark.parametrize("relation_type", ["calls", "references", "supports"])
def test_update_relation_rejects_polarity_on_non_causal_type(
    multi: MultiGraph, relation_type: str
) -> None:
    """The create-time partition is not bypassable after creation."""
    a, b = ent(multi, "A"), ent(multi, "B")
    create_relation(CreateRelationInput.model_validate(rel_input(a, b, relation_type)), multi)
    with pytest.raises(OperationError) as excinfo:
        update_relation(
            UpdateRelationInput.model_validate({"from": a, "to": b, "polarity": "+"}), multi
        )
    assert "verification gate" in str(excinfo.value)
    assert "must not have polarity" in str(excinfo.value)
    stored = read_relations(ReadRelationsInput.model_validate({"from": a, "to": b}), multi)
    assert stored[0]["polarity"] is None


def test_update_relation_retype_to_non_causal_requires_dropping_polarity(
    multi: MultiGraph,
) -> None:
    a, b = ent(multi, "A"), ent(multi, "B")
    create_relation(CreateRelationInput.model_validate(rel_input(a, b, "causes")), multi)
    with pytest.raises(OperationError) as excinfo:
        update_relation(
            UpdateRelationInput.model_validate({"from": a, "to": b, "relationType": "calls"}), multi
        )
    assert "must not have polarity" in str(excinfo.value)
    cleared = update_relation(
        UpdateRelationInput.model_validate(
            {"from": a, "to": b, "relationType": "calls", "polarity": None}
        ),
        multi,
    )
    assert cleared["relationType"] == "calls"
    assert cleared["polarity"] is None


def test_read_side_guards_report_polarized_non_causal_relation(multi: MultiGraph) -> None:
    """A polarized structural edge written below the mutation gate (store-level
    writes, older data) is still reportable by the verification surface."""
    a, b = ent(multi, "A"), ent(multi, "B")
    store = multi.get_store(None)
    store.create_relation(
        RelationCreate.model_validate(
            {"from": a, "to": b, "relationType": "calls", "polarity": "+", "strength": "moderate"}
        )
    )
    violations = list_guard_violations(ListGuardViolationsInput.model_validate({}), multi)
    codes = [v["code"] for group in violations["relationViolations"] for v in group["violations"]]
    assert "NON_CAUSAL_POLARITY" in codes

    consistency = check_consistency(GraphOnlyInput.model_validate({}), multi)
    assert consistency["consistent"] is False
    consistency_codes = [
        v["code"] for group in consistency["relationViolations"] for v in group["violations"]
    ]
    assert "NON_CAUSAL_POLARITY" in consistency_codes


def test_update_relation_retype_to_causal_infers_polarity(multi: MultiGraph) -> None:
    """Retyping to a causal type mirrors create: the default is inferred rather
    than leaving a causal edge with no polarity."""
    a, b = ent(multi, "A"), ent(multi, "B")
    create_relation(CreateRelationInput.model_validate(rel_input(a, b, "calls")), multi)
    updated = update_relation(
        UpdateRelationInput.model_validate({"from": a, "to": b, "relationType": "inhibits"}), multi
    )
    assert updated["relationType"] == "inhibits"
    assert updated["polarity"] == "-"


def test_delete_relation_retracts_by_default_and_not_found(multi: MultiGraph) -> None:
    a, b = ent(multi, "A"), ent(multi, "B")
    create_relation(CreateRelationInput.model_validate(rel_input(a, b)), multi)
    message = delete_relation(DeleteRelationInput.model_validate({"from": a, "to": b}), multi)
    assert message == f"Relation from {a} to {b} retracted successfully."
    with pytest.raises(NotFoundError):
        delete_relation(DeleteRelationInput.model_validate({"from": a, "to": b}), multi)


def test_hard_delete_relation_says_so(multi: MultiGraph) -> None:
    a, b = ent(multi, "A"), ent(multi, "B")
    create_relation(CreateRelationInput.model_validate(rel_input(a, b)), multi)
    message = delete_relation(
        DeleteRelationInput.model_validate({"from": a, "to": b, "hard": True}), multi
    )
    assert message == f"Relation from {a} to {b} deleted successfully."


def test_delete_relation_addresses_a_parallel_edge_by_relation_id(multi: MultiGraph) -> None:
    a, b = ent(multi, "A"), ent(multi, "B")
    first = create_relation(CreateRelationInput.model_validate(rel_input(a, b, "supports")), multi)
    second = create_relation(CreateRelationInput.model_validate(rel_input(a, b, "supports")), multi)
    delete_relation(
        DeleteRelationInput.model_validate({"from": a, "to": b, "relationId": second["id"]}),
        multi,
    )
    remaining = read_relations(ReadRelationsInput.model_validate({"from": a, "to": b}), multi)
    assert [r["id"] for r in remaining] == [first["id"]]


def test_update_relation_addresses_a_parallel_edge_by_relation_id(multi: MultiGraph) -> None:
    a, b = ent(multi, "A"), ent(multi, "B")
    first = create_relation(CreateRelationInput.model_validate(rel_input(a, b, "supports")), multi)
    second = create_relation(CreateRelationInput.model_validate(rel_input(a, b, "supports")), multi)
    updated = update_relation(
        UpdateRelationInput.model_validate(
            {"from": a, "to": b, "relationId": second["id"], "evidence": "the second edge"}
        ),
        multi,
    )
    assert updated["id"] == second["id"]
    remaining = read_relations(ReadRelationsInput.model_validate({"from": a, "to": b}), multi)
    by_id = {r["id"]: r for r in remaining}
    assert by_id[second["id"]]["evidence"] == "the second edge"
    assert by_id[first["id"]]["evidence"] != "the second edge"


def test_list_relations_explicit_null_polarity_filters_to_null(multi: MultiGraph) -> None:
    a, b, c = ent(multi, "A"), ent(multi, "B"), ent(multi, "C")
    create_relation(CreateRelationInput.model_validate(rel_input(a, b, "supports")), multi)
    create_relation(CreateRelationInput.model_validate(rel_input(b, c, "causes")), multi)
    everything = list_relations(ListRelationsInput.model_validate({}), multi)
    assert len(everything) == 2
    # Explicit null polarity filter — matches only null-polarity relations
    # (a "present but null" check), unlike an absent key.
    null_only = list_relations(ListRelationsInput.model_validate({"polarity": None}), multi)
    assert [r["relationType"] for r in null_only] == ["supports"]


def test_list_relations_wildcard_graph_annotates(multi: MultiGraph) -> None:
    multi.create_graph("research")
    a, b = ent(multi, "A"), ent(multi, "B")
    r1, r2 = ent(multi, "R1", graph="research"), ent(multi, "R2", graph="research")
    create_relation(CreateRelationInput.model_validate(rel_input(a, b)), multi)
    create_relation(CreateRelationInput.model_validate(rel_input(r1, r2, graph="research")), multi)
    result = list_relations(ListRelationsInput.model_validate({"graph": "*"}), multi)
    assert {r["graph"] for r in result} == {"default", "research"}


# =============================================================================
# get-relations / get-neighbors (bridge inclusion)
# =============================================================================


def seed_bridge(multi: MultiGraph, from_id: str, to_id: str) -> dict:
    return multi.bridges.create_bridge(
        {
            "from": from_id,
            "to": to_id,
            "relationType": "supports",
            "polarity": None,
            "strength": "moderate",
            "evidence": None,
            "from_graph": "default",
            "to_graph": "research",
        }
    )


def test_get_relations_includes_bridges(multi: MultiGraph) -> None:
    multi.create_graph("research")
    a, b = ent(multi, "A"), ent(multi, "B")
    remote = ent(multi, "Remote", graph="research")
    create_relation(CreateRelationInput.model_validate(rel_input(a, b)), multi)
    bridge = seed_bridge(multi, a, remote)

    result = get_relations(GetRelationsInput.model_validate({"entityId": a}), multi)
    assert len(result) == 2
    bridge_row = next(r for r in result if r.get("from_graph"))
    assert bridge_row["id"] == bridge["id"]
    assert bridge_row["to_graph"] == "research"

    outgoing_only = get_relations(
        GetRelationsInput.model_validate({"entityId": remote, "direction": "outgoing"}), multi
    )
    assert outgoing_only == []  # bridge points TO remote; direction excludes it


def test_get_neighbors_cross_graph_stub_and_follow(multi: MultiGraph) -> None:
    multi.create_graph("research")
    a, b = ent(multi, "A"), ent(multi, "B")
    remote = ent(multi, "Remote", graph="research")
    create_relation(CreateRelationInput.model_validate(rel_input(a, b)), multi)
    seed_bridge(multi, a, remote)

    stubs = get_neighbors(GetNeighborsInput.model_validate({"entityId": a}), multi)
    assert {n.get("name", n.get("id")) for n in stubs} == {"B", remote}
    stub = next(n for n in stubs if n.get("stub"))
    assert stub == {
        "id": remote,
        "graph": "research",
        "stub": True,
        "relationType": "supports",
        "direction": "out",
    }

    followed = get_neighbors(
        GetNeighborsInput.model_validate({"entityId": a, "follow_bridges": True}), multi
    )
    full = next(n for n in followed if n.get("graph") == "research")
    assert full["name"] == "Remote"
    assert full.get("stub") is None


# =============================================================================
# get-neighbors relationType/direction + compact
# =============================================================================


def test_get_neighbors_carries_relation_type_and_direction(multi: MultiGraph) -> None:
    a, b, c = ent(multi, "A"), ent(multi, "B"), ent(multi, "C")
    create_relation(CreateRelationInput.model_validate(rel_input(a, b, "supports")), multi)
    create_relation(CreateRelationInput.model_validate(rel_input(c, a, "causes")), multi)

    result = get_neighbors(GetNeighborsInput.model_validate({"entityId": a}), multi)
    by_name = {r["name"]: r for r in result}
    assert by_name["B"]["relationType"] == "supports"
    assert by_name["B"]["direction"] == "out"
    assert by_name["C"]["relationType"] == "causes"
    assert by_name["C"]["direction"] == "in"


def test_get_neighbors_compact_projects_entity_fields(multi: MultiGraph) -> None:
    a, b = ent(multi, "A"), ent(multi, "B")
    create_relation(CreateRelationInput.model_validate(rel_input(a, b, "supports")), multi)

    full = get_neighbors(GetNeighborsInput.model_validate({"entityId": a}), multi)
    assert "created_at" in full[0]

    compact = get_neighbors(
        GetNeighborsInput.model_validate({"entityId": a, "compact": True}), multi
    )
    assert len(compact) == 1
    entry = compact[0]
    assert set(entry) == {
        "id",
        "name",
        "entityType",
        "status",
        "observations",
        "relationType",
        "direction",
    }
    assert entry["name"] == "B"
    assert entry["relationType"] == "supports"
    assert entry["direction"] == "out"


def test_get_neighbors_hydrates_the_neighbourhood_in_one_batched_read(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hydration is a set fetch, not a per-neighbour loop.

    A single-id read costs a label scan, so hydrating a hub entity one
    ``read_entity`` at a time is a round trip per neighbour — the N+1 the
    store's batched ``read_entity_docs`` exists to avoid. Same rows, same
    order, one query.
    """
    a = ent(multi, "A")
    neighbours = [ent(multi, name) for name in ("B", "C", "D", "E")]
    for neighbour in neighbours:
        create_relation(CreateRelationInput.model_validate(rel_input(a, neighbour)), multi)

    expected = get_neighbors(GetNeighborsInput.model_validate({"entityId": a}), multi)

    single_reads = 0
    original = FalkorGraphStore.read_entity

    def counting_read_entity(self: FalkorGraphStore, entity_id: str) -> object:
        nonlocal single_reads
        single_reads += 1
        return original(self, entity_id)

    monkeypatch.setattr(FalkorGraphStore, "read_entity", counting_read_entity)
    result = get_neighbors(GetNeighborsInput.model_validate({"entityId": a}), multi)

    assert result == expected
    assert [row["name"] for row in result] == ["B", "C", "D", "E"]
    assert single_reads == 0


def test_get_neighbors_follows_bridges_with_one_read_per_remote_graph(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cross-graph branch batches too — one read per remote graph, not one
    per bridge."""
    multi.create_graph("research")
    a = ent(multi, "A")
    remotes = [ent(multi, name, graph="research") for name in ("R1", "R2", "R3")]
    for remote in remotes:
        seed_bridge(multi, a, remote)

    expected = get_neighbors(
        GetNeighborsInput.model_validate({"entityId": a, "follow_bridges": True}), multi
    )

    single_reads = 0
    original = FalkorGraphStore.read_entity

    def counting_read_entity(self: FalkorGraphStore, entity_id: str) -> object:
        nonlocal single_reads
        single_reads += 1
        return original(self, entity_id)

    monkeypatch.setattr(FalkorGraphStore, "read_entity", counting_read_entity)
    result = get_neighbors(
        GetNeighborsInput.model_validate({"entityId": a, "follow_bridges": True}), multi
    )

    assert result == expected
    assert [row["name"] for row in result] == ["R1", "R2", "R3"]
    assert single_reads == 0


# =============================================================================
# get-relations compact bridge rows
# =============================================================================


def test_get_relations_compact_projects_followed_bridge_entities(multi: MultiGraph) -> None:
    multi.create_graph("research")
    a = ent(multi, "A")
    remote = ent(multi, "Remote", graph="research")
    seed_bridge(multi, a, remote)

    result = get_relations(
        GetRelationsInput.model_validate({"entityId": a, "follow_bridges": True, "compact": True}),
        multi,
    )
    bridge_row = next(r for r in result if r.get("to_graph"))
    assert set(bridge_row["to_entity"]) == {"id", "name", "entityType", "status", "observations"}
    assert bridge_row["to_entity"]["name"] == "Remote"


def test_get_relations_default_keeps_full_followed_bridge_entities(multi: MultiGraph) -> None:
    """Without `compact`, followed bridge entities stay full envelopes."""
    multi.create_graph("research")
    a = ent(multi, "A")
    remote = ent(multi, "Remote", graph="research")
    seed_bridge(multi, a, remote)

    result = get_relations(
        GetRelationsInput.model_validate({"entityId": a, "follow_bridges": True}), multi
    )
    bridge_row = next(r for r in result if r.get("to_graph"))
    expected = (
        multi.get_store("research")
        .read_entity(remote)
        .model_dump(by_alias=True, exclude_unset=True)  # type: ignore[union-attr]
    )
    assert bridge_row["to_entity"] == expected
    assert bridge_row["from_entity"]["id"] == a
    assert "created_at" in bridge_row["from_entity"]
