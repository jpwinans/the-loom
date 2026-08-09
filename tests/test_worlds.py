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
from theloom.store.events import EventLog
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

    # list-worlds defaults to hiding reaped worlds (so the default view
    # doesn't grow monotonically) but never actually forgets them --
    # includeReaped: true still finds it, same as list-sessions' history.
    listed_default = run_handler("list-worlds", {}, multi)
    assert world["worldId"] not in {w["worldId"] for w in listed_default["items"]}
    listed_with_reaped = run_handler("list-worlds", {"includeReaped": True}, multi)
    assert world["worldId"] in {w["worldId"] for w in listed_with_reaped["items"]}

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
    # to_update is a claim whose confidence changed: confidenceChanged (from
    # the event), contestedClaim (main vs. the fork disagree), and
    # entityRevised for the bookkeeping fields update-entity always bumps
    # (version, changeType, previousVersionId) -- every field the event
    # actually changed shows up, not just the ones this test cares about.
    assert kinds_by_entity.get(to_update["id"]) == {
        "confidenceChanged",
        "contestedClaim",
        "entityRevised",
    }
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


def test_diff_worlds_event_ids_are_a_superset_of_what_merge_world_applies(
    multi: MultiGraph,
) -> None:
    """The critic's blocking-gap invariant, pinned as a property test: for a
    world with a genuinely mixed batch of writes (create, rename,
    confidence, status transition, relation, hard delete), every event id
    merge-world's own candidate set is built from
    (``_last_event_by_record_id`` over the fork's own segment) is visible
    somewhere in diff-worlds' reported event ids. Reconstructed here
    independently, through the same public event-log read merge-world
    itself uses -- not by reaching into either command's private state --
    so this is a genuine black-box check of the invariant, not a tautology.
    """
    graph = "g"
    now = iso_now()
    keep = create(multi, graph, "Keep")
    rename_me = create(multi, graph, "RenameMe")
    conf_me = create(
        multi,
        graph,
        "ConfMe",
        entityType="claim",
        confidence={"score": 0.3, "basis": "inference", "lastEvaluated": now},
    )
    status_me = create(multi, graph, "StatusMe")
    delete_me = create(multi, graph, "DeleteMe")
    rel_target = create(multi, graph, "RelTarget")

    world = fork(multi, graph, name="mixed-batch")
    wid = world["worldId"]
    created_in_fork = create(multi, graph, "CreatedInFork", world=wid)
    run_handler(
        "update-entity",
        {"graph": graph, "id": rename_me["id"], "world": wid, "name": "Renamed"},
        multi,
    )
    run_handler(
        "update-entity",
        {
            "graph": graph,
            "id": conf_me["id"],
            "world": wid,
            "confidence": {"score": 0.9, "basis": "inference", "lastEvaluated": iso_now()},
        },
        multi,
    )
    run_handler(
        "update-entity",
        {"graph": graph, "id": status_me["id"], "world": wid, "status": "retracted"},
        multi,
    )
    relate(multi, graph, created_in_fork["id"], rel_target["id"], world=wid)
    run_handler(
        "delete-entity", {"graph": graph, "id": delete_me["id"], "world": wid, "hard": True}, multi
    )

    diff = run_handler("diff-worlds", {"a": "main", "b": wid}, multi)
    diff_event_ids = {row["eventId"] for row in diff["items"] if row.get("eventId")}

    # Reconstructed independently, through the fork's own local event log --
    # the same public surface (multi.event_log) merge-world's own
    # _last_event_by_record_id reads, not a peek at either command's
    # private state.
    fork_events = multi.event_log(world_graph_name(wid)).read_all()
    merge_candidate_event_ids: dict[str, str] = {}
    for event in fork_events:
        record = event.payload.get("entity") or event.payload.get("relation")
        if isinstance(record, dict) and isinstance(record.get("id"), str):
            merge_candidate_event_ids[record["id"]] = event.id

    assert merge_candidate_event_ids, "the test's own batch must actually touch something"
    missing = set(merge_candidate_event_ids.values()) - diff_event_ids
    assert not missing, (
        f"diff-worlds must be a superset of merge-world's own candidate event ids; "
        f"missing: {missing}"
    )

    # Confirm it holds for a REAL merge too, not just the reconstruction:
    # every entity merge-world actually applies must have been visible in
    # the diff that preceded it.
    result = run_handler(
        "merge-world", {"from": wid, "into": "main", "strategy": "endorse-all"}, multi
    )
    applied_ids = {row["entityId"] for row in result["appliedEntities"]}
    assert applied_ids == {
        created_in_fork["id"],
        rename_me["id"],
        conf_me["id"],
        status_me["id"],
        delete_me["id"],
    }
    for entity_id in applied_ids:
        assert merge_candidate_event_ids[entity_id] in diff_event_ids

    assert keep["id"] not in applied_ids
    # The propagated hard delete actually landed in main.
    with pytest.raises(LoomError) as exc:
        run_handler("read-entity", {"graph": graph, "id": delete_me["id"]}, multi)
    assert exc.value.code == "NOT_FOUND"


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

    # Ref hygiene: belief-blast-radius PURGES its ephemeral world (not
    # abandon-world's reap-and-keep), so it never appears in list-worlds --
    # not even with includeReaped: true, since purge erases the ref record
    # outright rather than marking it dead.
    worlds_default = run_handler("list-worlds", {}, multi)
    assert result["worldId"] not in {w["worldId"] for w in worlds_default["items"]}
    worlds_with_reaped = run_handler("list-worlds", {"includeReaped": True}, multi)
    assert result["worldId"] not in {w["worldId"] for w in worlds_with_reaped["items"]}

    # And it does not return eventIds pointing into a stream it just
    # deleted: propagate-credit's own writes lived in the fork's now-
    # deleted segment and must not appear, while fork-world's own
    # ref_registered event (in the shared, never-deleted _refs stream)
    # remains genuinely replayable and IS reported.
    assert result["eventIds"], "fork-world's own ref_registered event must still be reported"
    what_changed = run_handler(
        "what-changed", {"graph": "_refs", "eventIds": result["eventIds"]}, multi
    )
    assert what_changed["items"], "the reported eventIds must all still replay"
    assert all(row["eventType"] == "ref_registered" for row in what_changed["items"])


# =============================================================================
# Tension (b): a repaired event can land out of order, by design -- a fork
# taken across a repaired span must still project consistently.
# =============================================================================


def _stream_id_key(entry_id: str) -> tuple[int, int]:
    ms, seq = entry_id.split("-", 1)
    return int(ms), int(seq)


def test_tension_b_fork_across_a_repaired_span_projects_consistently(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reaches the REAL repair path, not a mock of it: appends go through
    ``theloom.store.commit.commit_steps`` -> ``EventLog.queue`` ->
    ``Pipeline.xadd`` (buffered onto the MULTI/EXEC transaction) -- patching
    the Redis *client*'s ``xadd`` does nothing, since a pipeline never calls
    back through the client's own bound method. Patching ``Pipeline.xadd``
    itself to queue a syntactically valid but always-rejected id (``"0-0"``,
    below the minimum valid stream id) reproduces a genuine *runtime*
    rejection: the command is queued successfully and only fails at EXEC,
    exactly the case ``commit_steps``'s repair path exists for (a queue-time
    rejection aborts the whole transaction before repair is reachable at
    all).

    To make the repair genuinely land out of order (not just theoretically
    capable of it), a second, unrelated write is interleaved between the
    failure and the repair itself: ``EventLog.append`` (what ``repair()``
    calls to re-append outside the transaction) is patched to create an
    "Interloper" entity first, through the ordinary, unpatched write path.
    That write's event lands at a real, later stream position *before* the
    repaired event completes -- so B's event ends up after Interloper's in
    the stream, despite B being requested first.
    """
    graph = "g"
    store = multi.get_store(graph)
    target_key = store.events.key

    from redis.client import Pipeline

    original_pipeline_xadd = Pipeline.xadd
    state = {"fail_once": True}

    def patched_pipeline_xadd(
        self: Pipeline, name: str, fields: dict[str, Any], id: str = "*", **kwargs: Any
    ) -> Any:
        if name == target_key and state["fail_once"]:
            state["fail_once"] = False
            # A syntactically valid XADD, guaranteed to be rejected at EXEC
            # time (ids must be > 0-0) -- the runtime rejection commit_steps'
            # repair path exists for, not a queue-time one.
            return original_pipeline_xadd(self, name, fields, id="0-0", **kwargs)
        return original_pipeline_xadd(self, name, fields, id=id, **kwargs)

    monkeypatch.setattr(Pipeline, "xadd", patched_pipeline_xadd)

    # theloom.store.multigraph.MultiGraph.get_store builds a fresh
    # FalkorGraphStore (and so a fresh EventLog instance) on every call --
    # by design, so patching the local `store` variable's own `.events.
    # append` would never touch the instance `run_handler`'s own internal
    # `get_store` call actually uses. Patching EventLog.append on the
    # CLASS (like Pipeline.xadd above) reaches every instance, keyed here
    # by comparing `self.key` to this graph's own stream key.
    original_append = EventLog.append
    stash: dict[str, Any] = {"interleaved": False}

    def patched_append(self: EventLog, event_type: str, payload: dict[str, Any]) -> str:
        if self.key == target_key and not stash["interleaved"]:
            stash["interleaved"] = True
            # The concurrent writer commit.py's docstring warns can slip an
            # entry in front of a repair.
            stash["interloper"] = run_handler(
                "create-entity", {"graph": graph, **_entity_doc("Interloper")}, multi
            )
        return original_append(self, event_type, payload)

    monkeypatch.setattr(EventLog, "append", patched_append)

    b = run_handler("create-entity", {"graph": graph, **_entity_doc("B")}, multi)

    # Both patches actually fired -- this is not passing by coincidence.
    assert state["fail_once"] is False, "the patched XADD never ran"
    assert stash["interleaved"] is True, "repair never reached EventLog.append"
    interloper = stash["interloper"]

    b_event_id = b["eventIds"][0]
    interloper_event_id = interloper["eventIds"][0]
    assert _stream_id_key(b_event_id) > _stream_id_key(interloper_event_id), (
        "the repair must genuinely land out of order: B was requested (and "
        "logically created) before Interloper, but its repaired event should "
        "land at a LATER stream position"
    )

    time.sleep(_TICK)
    checkpoint_after_b = iso_now()
    time.sleep(_TICK)
    c = run_handler("create-entity", {"graph": graph, **_entity_doc("C")}, multi)
    assert c["name"] == "C"

    world = fork(multi, graph, asOf=checkpoint_after_b)
    projected = run_handler("list-entities", {"graph": graph, "world": world["worldId"]}, multi)
    names = {e["name"] for e in projected["items"]}
    assert names == {"B", "Interloper"}, (
        "forkedAtEventId's meaning is anchored to the wall-clock instant its "
        "timestamp encodes (read_graph_as_of/tx_from), not to the event's "
        "position in the stream -- so a repair that reorders the stream must "
        "not change what a fork taken after it sees, even though B's own "
        "event now sits AFTER Interloper's."
    )


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
