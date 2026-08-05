"""Bridges as event-sourced, bi-temporal store state.

Cross-graph bridges used to be raw JSON in a Redis list: no event log, no
history, invisible to a replay. They now live in the reserved ``_bridges``
graph with their own stream beside it — create appends ``bridge_created``,
remove *invalidates* (``bridge_invalidated``) instead of erasing, and the
legacy list is migrated in place, once, on first access.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.errors import NotFoundError, OperationError
from theloom.store import bridges as bridges_module
from theloom.store.bridges import BridgeRegistry
from theloom.store.multigraph import MultiGraph


def bridge_doc(
    from_id: str = "e1",
    to_id: str = "e2",
    relation_type: str = "supports",
    from_graph: str = "default",
    to_graph: str = "research",
) -> dict[str, Any]:
    return {
        "from": from_id,
        "to": to_id,
        "relationType": relation_type,
        "polarity": None,
        "strength": "moderate",
        "evidence": None,
        "from_graph": from_graph,
        "to_graph": to_graph,
    }


def legacy_doc(bridge_id: str, from_id: str, to_id: str, **overrides: Any) -> dict[str, Any]:
    """A bridge doc in the shape the old Redis list held."""
    return {
        "id": bridge_id,
        **bridge_doc(from_id, to_id, **overrides),
        "created_at": "2026-07-10T12:00:00.000Z",
        "updated_at": "2026-07-10T12:00:00.000Z",
    }


# =============================================================================
# Round trip: bridges are store state, not process state
# =============================================================================


def test_bridge_round_trips_through_a_fresh_facade(
    multi: MultiGraph, db: FalkorDB, redis_client: Redis, namespace: str
) -> None:
    created = multi.bridges.create_bridge(bridge_doc())
    reopened = MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)
    assert reopened.bridges.list_bridges() == [created]
    assert reopened.bridges.read_bridge("e1", "e2", "supports") == created
    assert reopened.bridges.list_bridges({"from_graph": "default"}) == [created]
    assert reopened.bridges.list_bridges({"entity_id": "e2"}) == [created]
    assert reopened.bridges.list_bridges({"to_graph": "systems"}) == []


def test_insertion_order_is_preserved(multi: MultiGraph) -> None:
    first = multi.bridges.create_bridge(bridge_doc("e1", "e2"))
    second = multi.bridges.create_bridge(bridge_doc("e3", "e4", "related_to"))
    third = multi.bridges.create_bridge(bridge_doc("e5", "e6"))
    assert [b["id"] for b in multi.bridges.list_bridges()] == [
        first["id"],
        second["id"],
        third["id"],
    ]


# =============================================================================
# Event log
# =============================================================================


def test_create_and_remove_are_logged(multi: MultiGraph) -> None:
    created = multi.bridges.create_bridge(bridge_doc())
    multi.bridges.delete_bridge("e1", "e2", "supports")
    events = multi.bridge_event_log().read_all()
    assert [e.type for e in events] == ["bridge_created", "bridge_invalidated"]
    assert events[0].payload["bridge"] == created
    assert events[1].payload["bridge"] == created
    assert events[1].payload["tx_to"]


def test_bridge_events_are_not_in_a_graph_stream(multi: MultiGraph) -> None:
    multi.bridges.create_bridge(bridge_doc())
    assert multi.event_log("default").read_all() == []
    assert len(multi.bridge_event_log().read_all()) == 1


def test_rejected_duplicate_logs_nothing(multi: MultiGraph) -> None:
    multi.bridges.create_bridge(bridge_doc())
    with pytest.raises(OperationError):
        multi.bridges.create_bridge(bridge_doc())
    assert len(multi.bridges.list_bridges()) == 1
    assert [e.type for e in multi.bridge_event_log().read_all()] == ["bridge_created"]


def test_missing_delete_logs_nothing(multi: MultiGraph) -> None:
    with pytest.raises(NotFoundError):
        multi.bridges.delete_bridge("e1", "e2", "supports")
    assert multi.bridge_event_log().read_all() == []


# =============================================================================
# History: removal invalidates, it does not erase
# =============================================================================


def test_removed_bridge_stays_in_history(multi: MultiGraph) -> None:
    created = multi.bridges.create_bridge(bridge_doc())
    multi.bridges.delete_bridge("e1", "e2", "supports")
    assert multi.bridges.list_bridges() == []
    history = multi.bridges.list_bridge_history()
    assert len(history) == 1
    assert history[0]["bridge"] == created
    assert history[0]["txFrom"] == created["created_at"]
    assert history[0]["txTo"] is not None


def test_pair_can_be_bridged_again_after_removal(multi: MultiGraph) -> None:
    first = multi.bridges.create_bridge(bridge_doc())
    multi.bridges.delete_bridge("e1", "e2", "supports")
    second = multi.bridges.create_bridge(bridge_doc())
    assert multi.bridges.list_bridges() == [second]
    history = multi.bridges.list_bridge_history({"entity_id": "e1"})
    assert [record["bridge"]["id"] for record in history] == [first["id"], second["id"]]
    assert [record["txTo"] is None for record in history] == [False, True]


# =============================================================================
# Migration off the legacy Redis list
# =============================================================================


def seed_legacy(redis_client: Redis, namespace: str, docs: list[dict[str, Any]]) -> str:
    key = f"{namespace}:bridges"
    redis_client.rpush(key, *[json.dumps(doc) for doc in docs])
    return key


def test_legacy_list_migrates_on_first_access(
    db: FalkorDB, redis_client: Redis, namespace: str
) -> None:
    docs = [
        legacy_doc("bridge-1", "a", "b"),
        legacy_doc("bridge-2", "b", "c", relation_type="related_to", to_graph="systems"),
    ]
    legacy_key = seed_legacy(redis_client, namespace, docs)

    multi = MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)
    assert multi.bridges.list_bridges() == docs  # verbatim docs, order preserved
    assert redis_client.exists(legacy_key) == 0  # dropped only after the new path has them
    assert [e.type for e in multi.bridge_event_log().read_all()] == [
        "bridge_migrated",
        "bridge_migrated",
    ]

    reopened = MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)
    assert reopened.bridges.list_bridges() == docs  # served from the new home
    assert len(reopened.bridge_event_log().read_all()) == 2  # not re-migrated


def test_migration_keeps_bridges_written_through_the_new_path(
    multi: MultiGraph, db: FalkorDB, redis_client: Redis, namespace: str
) -> None:
    native = multi.bridges.create_bridge(bridge_doc("x", "y"))
    seed_legacy(redis_client, namespace, [legacy_doc("bridge-1", "a", "b")])

    reopened = MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)
    listed = reopened.bridges.list_bridges()
    assert {b["id"] for b in listed} == {native["id"], "bridge-1"}


def test_migrated_bridge_is_a_first_class_record(
    db: FalkorDB, redis_client: Redis, namespace: str
) -> None:
    seed_legacy(redis_client, namespace, [legacy_doc("bridge-1", "a", "b")])
    multi = MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)

    assert multi.bridges.read_bridge("a", "b", "supports") is not None
    with pytest.raises(OperationError):
        multi.bridges.create_bridge(bridge_doc("a", "b"))
    multi.bridges.delete_bridge("a", "b", "supports")
    assert multi.bridges.list_bridges() == []
    assert [e.type for e in multi.bridge_event_log().read_all()] == [
        "bridge_migrated",
        "bridge_invalidated",
    ]
    assert len(multi.bridges.list_bridge_history()) == 1


def test_interrupted_migration_resumes(db: FalkorDB, redis_client: Redis, namespace: str) -> None:
    """A crash between claiming the legacy list and writing it through leaves
    the claim key behind; the next access must finish the job."""
    doc = legacy_doc("bridge-1", "a", "b")
    redis_client.rpush(f"{namespace}:bridges:migrating", json.dumps(doc))

    multi = MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)
    assert multi.bridges.list_bridges() == [doc]
    assert redis_client.exists(f"{namespace}:bridges:migrating") == 0


def test_interrupted_migration_does_not_swallow_a_newer_legacy_write(
    db: FalkorDB, redis_client: Redis, namespace: str
) -> None:
    """A claim key left by a crashed run must not be overwritten by the next
    claim: both the stranded doc and the newly listed one have to survive."""
    stranded = legacy_doc("bridge-1", "a", "b")
    listed = legacy_doc("bridge-2", "c", "d")
    redis_client.rpush(f"{namespace}:bridges:migrating", json.dumps(stranded))
    seed_legacy(redis_client, namespace, [listed])

    multi = MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)
    assert multi.bridges.list_bridges() == [stranded, listed]
    assert redis_client.exists(f"{namespace}:bridges") == 0
    assert redis_client.exists(f"{namespace}:bridges:migrating") == 0


class _ClaimRacer:
    """A Redis view that lets a racer win the claim key just before our rename.

    Stands in for the window between deciding the claim is free and the rename
    landing: another process claims a stranded list in between. Every other
    command passes through untouched.
    """

    def __init__(self, inner: Redis, on_rename: Any) -> None:
        self._inner = inner
        self._on_rename = on_rename
        self._fired = False

    def rename(self, src: str, dst: str) -> Any:
        return self._raced("rename", src, dst)

    def renamenx(self, src: str, dst: str) -> Any:
        return self._raced("renamenx", src, dst)

    def _raced(self, command: str, src: str, dst: str) -> Any:
        if not self._fired:
            self._fired = True
            self._on_rename()
        return getattr(self._inner, command)(src, dst)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def test_claiming_the_legacy_list_cannot_clobber_a_racers_claim(
    db: FalkorDB, redis_client: Redis, namespace: str
) -> None:
    """The claim is one atomic command: a claim key that fills in the window
    between "is it free?" and the rename must not be overwritten."""
    stranded = legacy_doc("bridge-1", "a", "b")
    listed = legacy_doc("bridge-2", "c", "d")
    claim_key = f"{namespace}:bridges:migrating"
    seed_legacy(redis_client, namespace, [listed])

    def racer_wins_the_claim() -> None:
        redis_client.rpush(claim_key, json.dumps(stranded))

    view = _ClaimRacer(redis_client, racer_wins_the_claim)
    registry = BridgeRegistry(db, view, key_prefix=namespace)  # type: ignore[arg-type]

    assert {doc["id"] for doc in registry.list_bridges()} == {"bridge-1", "bridge-2"}
    assert redis_client.exists(claim_key) == 0
    assert redis_client.exists(f"{namespace}:bridges") == 0


def test_draining_the_claim_cannot_drop_a_doc_it_never_migrated(
    db: FalkorDB, redis_client: Redis, namespace: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent registry can empty the claim key and refill it with a
    freshly claimed legacy list mid-drain; this process must remove only the
    doc it just wrote, never whatever the key happens to hold."""
    stranded = legacy_doc("bridge-1", "a", "b")
    later = legacy_doc("bridge-2", "c", "d")
    claim_key = f"{namespace}:bridges:migrating"
    redis_client.rpush(claim_key, json.dumps(stranded))

    real_commit = bridges_module.commit_steps
    interleaved = False

    def commit_then_let_the_racer_run(*args: Any, **kwargs: Any) -> Any:
        nonlocal interleaved
        result = real_commit(*args, **kwargs)
        if not interleaved:
            interleaved = True
            # The racer drained the same stranded doc, dropped the emptied
            # claim, then claimed a legacy list an old-version writer appended.
            redis_client.delete(claim_key)
            seed_legacy(redis_client, namespace, [later])
            redis_client.rename(f"{namespace}:bridges", claim_key)
        return result

    monkeypatch.setattr(bridges_module, "commit_steps", commit_then_let_the_racer_run)
    multi = MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)

    assert {doc["id"] for doc in multi.bridges.list_bridges()} == {"bridge-1", "bridge-2"}
    assert redis_client.exists(claim_key) == 0


# =============================================================================
# Wiping
# =============================================================================


class _BrokenGraph:
    def query(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("falkordb unavailable")


class _BrokenDB:
    def select_graph(self, name: str) -> _BrokenGraph:
        return _BrokenGraph()


def test_failed_bridge_wipe_raises_instead_of_reporting_success(
    redis_client: Redis, namespace: str
) -> None:
    """A wipe that did not happen must not read as a wipe that did — a reseed
    on top of surviving bridges is silently wrong."""
    registry = BridgeRegistry(_BrokenDB(), redis_client, key_prefix=namespace)  # type: ignore[arg-type]
    with pytest.raises(OperationError):
        registry.delete_all()


def test_wiping_a_store_that_holds_no_bridges_is_quiet(
    db: FalkorDB, redis_client: Redis, namespace: str
) -> None:
    BridgeRegistry(db, redis_client, key_prefix=f"{namespace}:virgin").delete_all()


def test_wipe_clears_bridges_history_and_the_legacy_list(
    multi: MultiGraph, redis_client: Redis, namespace: str
) -> None:
    multi.bridges.create_bridge(bridge_doc())
    seed_legacy(redis_client, namespace, [legacy_doc("bridge-1", "a", "b")])
    multi.wipe()
    assert multi.bridges.list_bridges() == []
    assert multi.bridges.list_bridge_history() == []
    assert multi.bridge_event_log().read_all() == []
    assert redis_client.exists(f"{namespace}:bridges") == 0
