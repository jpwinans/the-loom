"""theloom.store.refs.RefRegistry: the generic ref/TTL registry session
workspaces are built on (desire 2), designed for reuse by branchable belief
worlds (desire 12 / Part 5) under a different ``kind``.

Covers register/get/list/touch/expire/reap, TTL bookkeeping (``expired`` is
informational, never auto-enforced), idempotent expire/reap, and the
write-receipts integration (every mutating call appends one event, recorded
under an active ``receipts.collecting()`` scope exactly like a graph
mutation's).
"""

from __future__ import annotations

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.errors import NotFoundError, ValidationError
from theloom.store import receipts
from theloom.store.refs import RefRegistry


@pytest.fixture()
def refs(db: FalkorDB, redis_client: Redis, namespace: str) -> RefRegistry:
    return RefRegistry(redis_client, key_prefix=namespace)


def test_register_defaults_to_active_with_no_ttl(refs: RefRegistry) -> None:
    record = refs.register("session", name="scratch work")
    assert record.kind == "session"
    assert record.name == "scratch work"
    assert record.status == "active"
    assert record.ttl_seconds is None
    assert record.expires_at is None
    assert record.expired is False
    assert record.metadata == {}
    assert refs.get("session", record.id) == record


def test_register_with_ttl_computes_expires_at_and_is_not_yet_expired(refs: RefRegistry) -> None:
    record = refs.register("session", ttl_seconds=3600)
    assert record.ttl_seconds == 3600
    assert record.expires_at is not None
    assert record.expires_at > record.created_at
    assert record.expired is False


def test_register_with_a_past_ttl_reports_expired(refs: RefRegistry) -> None:
    record = refs.register("session", ttl_seconds=-1)
    assert record.expired is True


def test_register_explicit_id_is_reused_by_the_caller(refs: RefRegistry) -> None:
    record = refs.register("session", ref_id="sess-fixed", metadata={"namespace": "sess-fixed-"})
    assert record.id == "sess-fixed"
    assert refs.get("session", "sess-fixed") is not None


def test_register_duplicate_id_under_same_kind_rejected(refs: RefRegistry) -> None:
    refs.register("session", ref_id="dup")
    with pytest.raises(ValidationError):
        refs.register("session", ref_id="dup")


def test_kinds_are_isolated(refs: RefRegistry) -> None:
    """The same id under two different kinds is two different refs — a
    future `kind="world"` consumer cannot collide with session ids."""
    refs.register("session", ref_id="shared-id")
    refs.register("world", ref_id="shared-id")
    assert refs.get("session", "shared-id") is not None
    assert refs.get("world", "shared-id") is not None
    assert [r.id for r in refs.list("session")] == ["shared-id"]
    assert [r.id for r in refs.list("world")] == ["shared-id"]


def test_get_missing_ref_is_none(refs: RefRegistry) -> None:
    assert refs.get("session", "nope") is None


def test_list_is_oldest_first_and_includes_every_status(refs: RefRegistry) -> None:
    first = refs.register("session", name="first")
    second = refs.register("session", name="second")
    refs.reap("session", first.id)
    listed = refs.list("session")
    assert [r.id for r in listed] == [first.id, second.id]
    assert listed[0].status == "reaped"


def test_touch_refreshes_ttl_from_now(refs: RefRegistry) -> None:
    record = refs.register("session", ttl_seconds=10)
    touched = refs.touch("session", record.id)
    assert touched.ttl_seconds == 10
    assert touched.expires_at is not None
    assert touched.expires_at >= record.expires_at


def test_touch_can_override_ttl(refs: RefRegistry) -> None:
    record = refs.register("session", ttl_seconds=10)
    touched = refs.touch("session", record.id, ttl_seconds=99999)
    assert touched.ttl_seconds == 99999
    assert touched.expired is False


def test_touch_missing_ref_raises_not_found(refs: RefRegistry) -> None:
    with pytest.raises(NotFoundError):
        refs.touch("session", "nope")


def test_expire_marks_status_without_touching_metadata(refs: RefRegistry) -> None:
    record = refs.register("session", metadata={"namespace": "sess-x-"})
    expired = refs.expire("session", record.id)
    assert expired.status == "expired"
    assert expired.metadata == {"namespace": "sess-x-"}


def test_expire_is_idempotent(refs: RefRegistry) -> None:
    record = refs.register("session")
    once = refs.expire("session", record.id)
    twice = refs.expire("session", record.id)
    assert once == twice


def test_reap_marks_status_and_stamps_reaped_at(refs: RefRegistry) -> None:
    record = refs.register("session")
    assert record.reaped_at is None
    reaped = refs.reap("session", record.id)
    assert reaped.status == "reaped"
    assert reaped.reaped_at is not None


def test_reap_is_idempotent_and_returns_the_same_record_unchanged(refs: RefRegistry) -> None:
    record = refs.register("session")
    once = refs.reap("session", record.id)
    twice = refs.reap("session", record.id)
    assert once == twice


def test_reap_missing_ref_raises_not_found(refs: RefRegistry) -> None:
    with pytest.raises(NotFoundError):
        refs.reap("session", "nope")


def test_wipe_drops_every_ref_of_a_kind(refs: RefRegistry) -> None:
    refs.register("session", ref_id="a")
    refs.register("session", ref_id="b")
    refs.wipe("session")
    assert refs.list("session") == []


# =============================================================================
# Write-receipts integration
# =============================================================================


def test_register_appends_one_event_recorded_under_an_active_receipt_scope(
    refs: RefRegistry,
) -> None:
    with receipts.collecting("begin-session") as ids:
        refs.register("session")
    assert len(ids) == 1


def test_touch_expire_reap_each_append_one_event(refs: RefRegistry) -> None:
    record = refs.register("session")
    with receipts.collecting("touch-session") as ids:
        refs.touch("session", record.id, ttl_seconds=60)
    assert len(ids) == 1
    with receipts.collecting("expire-session") as ids:
        refs.expire("session", record.id)
    assert len(ids) == 1


def test_idempotent_reap_earns_no_second_event(refs: RefRegistry) -> None:
    record = refs.register("session")
    refs.reap("session", record.id)
    with receipts.collecting("end-session") as ids:
        refs.reap("session", record.id)  # already reaped: no-op, no commit
    assert ids == []


def test_events_carry_causing_command_via_causedby(refs: RefRegistry) -> None:
    with receipts.collecting("begin-session"):
        record = refs.register("session")
    events = refs.events.read_all()
    matching = [e for e in events if e.payload.get("id") == record.id]
    assert matching
    assert matching[0].caused_by == "begin-session"
    assert matching[0].type == "ref_registered"


def test_list_order_is_registration_order_even_when_created_at_ties(
    refs: RefRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CI regression (test_list_is_oldest_first_and_includes_every_status
    flaked on fast runners): two refs registered within the same timestamp
    tick used to tie on ``createdAt`` and fall back to Redis HGETALL's
    arbitrary iteration order. The per-kind registration sequence breaks
    the tie deterministically."""
    monkeypatch.setattr("theloom.store.refs.iso_now", lambda: "2026-08-09T00:00:00.000Z")
    ids = [refs.register("session", name=f"tied-{i}").id for i in range(6)]
    assert [r.id for r in refs.list("session")] == ids
