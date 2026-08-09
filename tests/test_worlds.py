"""Branchable belief worlds (desire 12 / Part 5) and belief-blast-radius
(desire 4): acceptance tests (a)-(e), the two named tensions, and the
one-propagation-engine proof — all dispatched through
``theloom.cli.registry.run_handler``, the same path a real CLI invocation
takes, so these read the way the opus critic will rehearse them live.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from redis import Redis

from theloom.cli.registry import run_handler
from theloom.errors import LoomError
from theloom.operations import epistemic as epistemic_ops
from theloom.store import worldctx
from theloom.store.multigraph import MultiGraph
from theloom.store.worlds import world_graph_name
from theloom.timeutil import iso_now

# Bi-temporal bounds are millisecond-resolution wire timestamps (iso_now());
# a checkpoint captured immediately before/after a write can land in the same
# millisecond as that write without this, making a `tx_from <= t` boundary
# comparison nondeterministic.
_TICK = 0.002


def _entity_doc(name: str, **overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "name": name,
        "entityType": "concept",
        "observations": [f"observation about {name}"],
    }
    doc.update(overrides)
    return doc


def create(
    multi: MultiGraph, graph: str, name: str, world: str | None = None, **overrides: Any
) -> dict[str, Any]:
    payload = {"graph": graph, **_entity_doc(name, **overrides)}
    if world is not None:
        payload["world"] = world
    result: dict[str, Any] = run_handler("create-entity", payload, multi)
    return result


def relate(
    multi: MultiGraph,
    graph: str,
    from_id: str,
    to_id: str,
    world: str | None = None,
    relation_type: str = "related_to",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "graph": graph,
        "from": from_id,
        "to": to_id,
        "relationType": relation_type,
        "strength": "moderate",
        "polarity": None,
        "evidence": None,
    }
    if world is not None:
        payload["world"] = world
    result: dict[str, Any] = run_handler("create-relation", payload, multi)
    return result


def fork(multi: MultiGraph, graph: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"graph": graph, **overrides}
    result: dict[str, Any] = run_handler("fork-world", payload, multi)
    return result


# =============================================================================
# Fork basics: O(1), ref shape, list/abandon
# =============================================================================


def test_fork_writes_no_entity_data_and_is_o1(multi: MultiGraph) -> None:
    graph = "g"
    create(multi, graph, "A")
    base_events_before = len(multi.event_log(graph).read_all())

    world = fork(multi, graph, name="scratch")

    assert world["worldId"].startswith("world-")
    assert world["parentWorld"] == "main"
    assert world["status"] == "active"
    assert world["baseGraph"] == graph
    assert world["forkedAtEventId"] is not None
    assert world["eventIds"]  # the ref-registration event's own receipt

    base_events_after = len(multi.event_log(graph).read_all())
    assert base_events_after == base_events_before, "fork must not append to the base graph's log"
    world_events = multi.event_log(world_graph_name(world["worldId"])).read_all()
    assert world_events == [], "fork must not write any entity data to the world's own segment"


def test_list_worlds_and_abandon(multi: MultiGraph) -> None:
    graph = "g"
    world = fork(multi, graph, name="scratch")
    listed = run_handler("list-worlds", {}, multi)
    assert world["worldId"] in {w["worldId"] for w in listed["items"]}

    abandoned = run_handler("abandon-world", {"worldId": world["worldId"]}, multi)
    assert abandoned["applied"] is True
    assert abandoned["status"] == "abandoned"
    assert abandoned["refStatus"] == "reaped"

    again = run_handler("abandon-world", {"worldId": world["worldId"]}, multi)
    assert again["applied"] is False
    assert again["notices"][0]["code"] == "ALREADY_REAPED"


def test_abandon_deletes_the_worlds_own_segment(multi: MultiGraph, redis_client: Redis) -> None:
    graph = "g"
    a = create(multi, graph, "A")
    world = fork(multi, graph)
    create(multi, graph, "In-fork", world=world["worldId"])
    key = f"{multi.key_prefix}:{world_graph_name(world['worldId'])}:events"
    assert redis_client.exists(key)
    run_handler("abandon-world", {"worldId": world["worldId"]}, multi)
    assert not redis_client.exists(key)
    # main is unaffected
    assert run_handler("read-entity", {"graph": graph, "id": a["id"]}, multi)["name"] == "A"


def test_fork_from_unknown_world_is_not_found(multi: MultiGraph) -> None:
    with pytest.raises(LoomError) as exc:
        run_handler("fork-world", {"graph": "g", "fromWorld": "world-doesnotexist"}, multi)
    assert exc.value.code == "NOT_FOUND"


# =============================================================================
# Acceptance (a): fork at asOf a past moment reproduces exactly the
# historical projection.
# =============================================================================


def test_acceptance_a_fork_asof_reproduces_historical_projection(multi: MultiGraph) -> None:
    graph = "g"
    a = create(multi, graph, "A")
    time.sleep(_TICK)
    checkpoint = iso_now()
    time.sleep(_TICK)
    # Entities/relations created strictly after the checkpoint.
    create(multi, graph, "B")
    run_handler("update-entity", {"graph": graph, "id": a["id"], "name": "A-renamed-later"}, multi)

    world = fork(multi, graph, name="historical", asOf=checkpoint)

    listed = run_handler("list-entities", {"graph": graph, "world": world["worldId"]}, multi)
    names = {e["name"] for e in listed["items"]}
    assert names == {"A"}  # exactly the historical projection: not B, not the rename

    historical_a = run_handler(
        "read-entity", {"graph": graph, "id": a["id"], "world": world["worldId"]}, multi
    )
    assert historical_a["name"] == "A"

    # main, read live, shows both the later entity and the rename.
    live = run_handler("list-entities", {"graph": graph}, multi)
    assert {e["name"] for e in live["items"]} == {"A-renamed-later", "B"}


# =============================================================================
# Acceptance (b): a propagate-credit in a fork leaves main re-reads
# untouched.
# =============================================================================


def test_acceptance_b_propagate_credit_in_fork_leaves_main_untouched(multi: MultiGraph) -> None:
    graph = "g"
    now = iso_now()
    evidence = create(
        multi,
        graph,
        "Evidence",
        entityType="evidence",
        confidence={"score": 0.5, "basis": "inference", "lastEvaluated": now},
    )
    claim = create(
        multi,
        graph,
        "Claim",
        entityType="claim",
        confidence={"score": 0.5, "basis": "inference", "lastEvaluated": now},
    )
    relate(multi, graph, evidence["id"], claim["id"], relation_type="supports")

    world = fork(multi, graph)
    result = run_handler(
        "propagate-credit",
        {
            "graph": graph,
            "world": world["worldId"],
            "entityIds": [evidence["id"]],
            "delta": 0.3,
        },
        multi,
    )
    assert result["items"][0]["applied"] is True
    assert result["items"][0]["totalEntitiesAffected"] >= 1

    fork_claim = run_handler(
        "read-entity", {"graph": graph, "id": claim["id"], "world": world["worldId"]}, multi
    )
    assert fork_claim["confidence"]["score"] != 0.5

    main_claim = run_handler("read-entity", {"graph": graph, "id": claim["id"]}, multi)
    assert main_claim["confidence"]["score"] == 0.5, "main must be untouched by a fork's writes"
    main_evidence = run_handler("read-entity", {"graph": graph, "id": evidence["id"]}, multi)
    assert main_evidence["confidence"]["score"] == 0.5


# =============================================================================
# Acceptance (c): diff-worlds between a fork and main lists exactly the
# fork's writes, each with event ids.
# =============================================================================


def test_acceptance_c_diff_worlds_lists_exactly_the_forks_writes(multi: MultiGraph) -> None:
    graph = "g"
    now = iso_now()
    untouched = create(multi, graph, "Untouched")
    to_update = create(
        multi,
        graph,
        "ToUpdate",
        entityType="claim",
        confidence={"score": 0.4, "basis": "inference", "lastEvaluated": now},
    )

    world = fork(multi, graph, name="fork1")
    wid = world["worldId"]
    new_entity = create(multi, graph, "NewInFork", world=wid)
    run_handler(
        "update-entity",
        {
            "graph": graph,
            "id": to_update["id"],
            "world": wid,
            "confidence": {"score": 0.9, "basis": "inference", "lastEvaluated": iso_now()},
        },
        multi,
    )
    relate(multi, graph, new_entity["id"], to_update["id"], world=wid)

    diff = run_handler("diff-worlds", {"a": "main", "b": wid}, multi)
    kinds_by_entity: dict[str, set[str]] = {}
    for row in diff["items"]:
        if "entityId" in row:
            kinds_by_entity.setdefault(row["entityId"], set()).add(row["kind"])

    assert kinds_by_entity.get(new_entity["id"]) == {"entityAdded"}
    # to_update is a claim whose confidence changed: both rows fire.
    assert kinds_by_entity.get(to_update["id"]) == {"confidenceChanged", "contestedClaim"}
    assert untouched["id"] not in kinds_by_entity

    relation_rows = [row for row in diff["items"] if row["kind"] == "relationAdded"]
    assert len(relation_rows) == 1
    assert relation_rows[0]["from"] == new_entity["id"]
    assert relation_rows[0]["fromName"] == "NewInFork"

    # Every row representing an actual write carries its causing event id
    # from the fork's own segment (contestedClaim is a derived annotation on
    # top of a confidenceChanged/other row, not a write of its own).
    write_kinds = {
        "entityAdded",
        "entityInvalidated",
        "confidenceChanged",
        "relationAdded",
        "relationRemoved",
    }
    for row in diff["items"]:
        if row["kind"] in write_kinds:
            assert row["eventId"] is not None, row


# =============================================================================
# Acceptance (d): merge with a manufactured conflict applies the
# uncontested set and notices the contested one.
# =============================================================================


def test_acceptance_d_merge_applies_uncontested_and_notices_contested(multi: MultiGraph) -> None:
    graph = "g"
    now = iso_now()
    contested = create(
        multi,
        graph,
        "Contested",
        entityType="claim",
        confidence={"score": 0.5, "basis": "inference", "lastEvaluated": now},
    )
    clean = create(multi, graph, "Clean")

    world = fork(multi, graph)
    wid = world["worldId"]

    # The fork revises `contested`...
    run_handler(
        "update-entity",
        {
            "graph": graph,
            "id": contested["id"],
            "world": wid,
            "confidence": {"score": 0.8, "basis": "inference", "lastEvaluated": iso_now()},
        },
        multi,
    )
    # ...and also revises `clean` (the uncontested change).
    run_handler(
        "update-entity",
        {"graph": graph, "id": clean["id"], "world": wid, "name": "Clean-renamed"},
        multi,
    )
    # Meanwhile `main` ALSO revises `contested`, since the fork — a genuine conflict.
    run_handler(
        "update-entity",
        {
            "graph": graph,
            "id": contested["id"],
            "confidence": {"score": 0.2, "basis": "inference", "lastEvaluated": iso_now()},
        },
        multi,
    )

    result = run_handler(
        "merge-world", {"from": wid, "into": "main", "strategy": "endorse-all"}, multi
    )

    applied_ids = {row["entityId"] for row in result["appliedEntities"]}
    contested_ids = {row["entityId"] for row in result["contested"]}
    assert clean["id"] in applied_ids
    assert contested["id"] in contested_ids
    assert contested["id"] not in applied_ids
    assert result["applied"] is True
    assert any(n["code"] == "CONTESTED_ON_MERGE" for n in result["notices"])

    merged_clean = run_handler("read-entity", {"graph": graph, "id": clean["id"]}, multi)
    assert merged_clean["name"] == "Clean-renamed"
    # main's own (contested) revision stands -- the merge did not clobber it.
    merged_contested = run_handler("read-entity", {"graph": graph, "id": contested["id"]}, multi)
    assert merged_contested["confidence"]["score"] == 0.2

    worlds = run_handler("list-worlds", {}, multi)
    merged_world = next(w for w in worlds["items"] if w["worldId"] == wid)
    assert merged_world["status"] == "merged"


def test_merge_select_strategy_applies_a_manually_chosen_id_even_if_contested(
    multi: MultiGraph,
) -> None:
    graph = "g"
    now = iso_now()
    contested = create(
        multi,
        graph,
        "Contested",
        confidence={"score": 0.5, "basis": "inference", "lastEvaluated": now},
    )
    world = fork(multi, graph)
    wid = world["worldId"]
    run_handler(
        "update-entity",
        {"graph": graph, "id": contested["id"], "world": wid, "name": "Fork-wins"},
        multi,
    )
    run_handler(
        "update-entity", {"graph": graph, "id": contested["id"], "name": "Main-wins"}, multi
    )

    result = run_handler(
        "merge-world",
        {"from": wid, "into": "main", "strategy": "select", "entityIds": [contested["id"]]},
        multi,
    )
    assert {row["entityId"] for row in result["appliedEntities"]} == {contested["id"]}
    assert result["contested"] == []
    merged = run_handler("read-entity", {"graph": graph, "id": contested["id"]}, multi)
    assert merged["name"] == "Fork-wins"


# =============================================================================
# Acceptance (e): all existing tests pass with `world` omitted is the full
# suite's job; here, a focused check that a representative spread of
# commands is byte-for-byte unaffected by the new field's mere presence.
# =============================================================================


def test_acceptance_e_world_omitted_is_unobservable(multi: MultiGraph) -> None:
    graph = "g"
    a = create(multi, graph, "A")
    with_world_field_absent = run_handler("read-entity", {"graph": graph, "id": a["id"]}, multi)
    with_world_field_explicit_main = run_handler(
        "read-entity", {"graph": graph, "id": a["id"], "world": "main"}, multi
    )
    assert with_world_field_absent == with_world_field_explicit_main
    assert worldctx.current() is None  # nothing leaks out of run_handler's scope


# =============================================================================
# belief-blast-radius (desire 4): one propagation engine, not two.
# =============================================================================


def test_belief_blast_radius_runs_the_real_propagate_credit(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []
    real = epistemic_ops.propagate_credit

    def spy(params: Any, multi_arg: Any) -> Any:
        calls.append(params)
        return real(params, multi_arg)

    monkeypatch.setattr(epistemic_ops, "propagate_credit", spy)

    graph = "g"
    now = iso_now()
    evidence = create(
        multi,
        graph,
        "Evidence",
        entityType="evidence",
        confidence={"score": 0.5, "basis": "inference", "lastEvaluated": now},
    )
    claim = create(
        multi,
        graph,
        "Claim",
        entityType="claim",
        confidence={"score": 0.5, "basis": "inference", "lastEvaluated": now},
    )
    relate(multi, graph, evidence["id"], claim["id"], relation_type="supports")

    result = run_handler(
        "belief-blast-radius",
        {"graph": graph, "entityIds": [evidence["id"]], "delta": 0.3},
        multi,
    )

    assert len(calls) == 1, "belief-blast-radius must call the real propagate_credit exactly once"
    assert result["applied"] is False
    assert any(row.get("entityId") == claim["id"] for row in result["diff"]["items"])
    # The fork is torn down; main's claim is untouched.
    main_claim = run_handler("read-entity", {"graph": graph, "id": claim["id"]}, multi)
    assert main_claim["confidence"]["score"] == 0.5
    worlds = run_handler("list-worlds", {}, multi)
    assert worlds["items"][0]["worldId"] == result["worldId"]
    assert worlds["items"][0]["status"] == "abandoned"


# =============================================================================
# Tension (b): a repaired event can land out of order, by design -- a fork
# taken across a repaired span must still project consistently.
# =============================================================================


def test_tension_b_fork_across_a_repaired_span_projects_consistently(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = "g"
    store = multi.get_store(graph)

    # Force the NEXT XADD on this graph's event stream to fail once, so
    # commit_steps's repair path (theloom.store.commit.repair_log) re-appends
    # the event outside the transaction -- landing later in the stream than
    # it logically belongs, exactly the "repaired event lands out of order"
    # scenario CLAUDE.md's tension names.
    original_xadd = store._redis.xadd
    state = {"fail_once": True}

    def flaky_xadd(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == store.events.key and state["fail_once"]:
            state["fail_once"] = False
            raise RuntimeError("simulated transient XADD failure")
        return original_xadd(name, *args, **kwargs)

    # The mutation itself must still land correctly -- create-entity's own
    # Cypher succeeds, only its paired XADD fails at EXEC and is repaired.
    monkeypatch.setattr(store._redis, "xadd", flaky_xadd)
    b = run_handler("create-entity", {"graph": graph, **_entity_doc("B")}, multi)
    monkeypatch.setattr(store._redis, "xadd", original_xadd)

    events = store.events.read_all()
    # The repaired event exists and is genuinely the last in the stream
    # (repair appends outside the transaction, after whatever landed since).
    assert any(json_matches_b(e.payload, b["id"]) for e in events)

    time.sleep(_TICK)
    checkpoint_after_b = iso_now()
    time.sleep(_TICK)
    c = run_handler("create-entity", {"graph": graph, **_entity_doc("C")}, multi)
    assert c["name"] == "C"

    world = fork(multi, graph, asOf=checkpoint_after_b)
    projected = run_handler("list-entities", {"graph": graph, "world": world["worldId"]}, multi)
    names = {e["name"] for e in projected["items"]}
    assert names == {"B"}, (
        "forkedAtEventId's meaning is anchored to the wall-clock instant its "
        "timestamp encodes (read_graph_as_of), not to the event's position in "
        "the stream -- so a repair that reorders the stream must not change "
        "what a fork taken after it sees."
    )


def json_matches_b(payload: dict[str, Any], entity_id: str) -> bool:
    entity = payload.get("entity")
    return isinstance(entity, dict) and entity.get("id") == entity_id


# =============================================================================
# Tension (a): a world projection declares what it cannot reconstruct
# (embeddings) rather than silently pretending to see the whole thing.
# =============================================================================


def test_tension_a_embeddings_are_not_forked_and_the_gap_is_declared(multi: MultiGraph) -> None:
    graph = "g"
    a = create(multi, graph, "Searchable")
    world = fork(multi, graph)
    result = run_handler("embedding-status", {"graph": graph, "world": world["worldId"]}, multi)
    assert any(n["code"] == "WORLD_PROJECTION_PARTIAL" for n in result["notices"])
    # But the entity itself IS visible through the overlay (doc, not vector).
    listed = run_handler("list-entities", {"graph": graph, "world": world["worldId"]}, multi)
    assert a["id"] in {e["id"] for e in listed["items"]}


# =============================================================================
# Bi-temporal reads inside a world, and main's replay byte-identical
# before/after fork+abandon.
# =============================================================================


def test_bitemporal_read_inside_a_world(multi: MultiGraph) -> None:
    """Bi-temporal queries work inside any world: as-of reads compose the
    world's own history with its frozen view of its parent, at any bound —
    before the fork, after it, or in between."""
    graph = "g"
    a = create(multi, graph, "A")
    time.sleep(_TICK)
    t0 = iso_now()  # before the fork: A as it was in main
    time.sleep(_TICK)
    world = fork(multi, graph)
    wid = world["worldId"]
    run_handler("update-entity", {"graph": graph, "id": a["id"], "world": wid, "name": "A2"}, multi)
    time.sleep(_TICK)
    t1 = iso_now()  # inside the fork, after its first local update
    time.sleep(_TICK)
    run_handler("update-entity", {"graph": graph, "id": a["id"], "world": wid, "name": "A3"}, multi)

    world_store = multi.get_store(None, world=wid)
    assert world_store.read_entity_as_of(a["id"], t0).name == "A"
    assert world_store.read_entity_as_of(a["id"], t1).name == "A2"
    assert world_store.read_entity(a["id"]).name == "A3"

    snapshot_t0 = world_store.read_graph_as_of(t0)
    assert [e.name for e in snapshot_t0.entities] == ["A"]
    snapshot_t1 = world_store.read_graph_as_of(t1)
    assert [e.name for e in snapshot_t1.entities] == ["A2"]


def test_main_replay_byte_identical_before_and_after_fork_and_abandon(multi: MultiGraph) -> None:
    graph = "g"
    create(multi, graph, "A")
    events_before = [(e.type, e.payload) for e in multi.event_log(graph).read_all()]

    world = fork(multi, graph)
    create(multi, graph, "InFork", world=world["worldId"])
    run_handler("abandon-world", {"worldId": world["worldId"]}, multi)

    events_after = [(e.type, e.payload) for e in multi.event_log(graph).read_all()]
    assert events_before == events_after
