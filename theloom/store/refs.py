"""The ref/TTL registry: named, TTL-bearing refs over the store, generic
across whatever kind of thing needs one.

A *ref* is a small record — kind, id, an optional human name, a status
(``active`` / ``expired`` / ``reaped``), a TTL, and an opaque ``metadata``
payload the caller owns — tracked in one Redis hash per kind
(``{prefix}:_refs:{kind}``) plus one shared, reserved event stream
(``{prefix}:_refs:events``). This module knows nothing about what a ref
*means*: register/list/get/touch/expire/reap are the whole vocabulary, and
``metadata`` is where a consumer keeps whatever makes its kind of ref useful.

Built for session workspaces (desire 2 — ``kind="session"``, with the
session's graph namespace prefix in ``metadata``), and designed so
branchable belief worlds (desire 12 / Part 5, ``kind="world"``) can reuse it
unmodified: a world is "one more ref over the event log," exactly as a
session is one more ref over the graph registry, and both only need a place
to keep an id, a TTL, and a lifecycle status. The kind string is the only
thing that partitions one consumer's ids from another's — pick a kind that
does not collide with an existing one.

Every write (``register``/``touch``/``expire``/``reap``) is one Redis
``HSET`` of the ref's doc plus one event append, sent as a single MULTI/EXEC
— the same "state and log move together" guarantee
``theloom.store.commit.commit_steps`` gives graph mutations, applied to
Redis-native bookkeeping instead of a Cypher step (a ref is not a knowledge-
graph row, so there is no ``GRAPH.QUERY`` to fold it into; this is the
smallest primitive that still keeps the two atomic). The appended event id is
recorded with ``theloom.store.receipts`` exactly like a graph mutation's, so
a command built on this module gets ``eventIds`` on its response for free
from ``theloom.cli.registry.run_handler`` — no extra wiring in the handler.

TTL here is informational, not enforced: ``expires_at`` is computed once at
register/touch time and a ref past it is simply discoverable as due for
``reap`` (``RefRecord.expired``) — there is no background sweep, because a
one-shot CLI process has nowhere to run one. Reaping is always an explicit,
one-call action by a caller (or a future maintenance command), never
automatic eviction.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from redis import Redis

from theloom.errors import NotFoundError, ValidationError
from theloom.store import receipts
from theloom.store.events import EventLog
from theloom.timeutil import iso_now

REF_GRAPH_SUFFIX = "_refs"

STATUS_ACTIVE = "active"
STATUS_EXPIRED = "expired"
STATUS_REAPED = "reaped"


@dataclass(frozen=True)
class RefRecord:
    """One ref: a kind-scoped id, optional name, lifecycle status, TTL
    bounds, and the caller's opaque ``metadata``."""

    kind: str
    id: str
    name: str | None
    status: str
    created_at: str
    expires_at: str | None
    reaped_at: str | None
    ttl_seconds: int | None
    metadata: dict[str, Any]
    seq: int = 0

    @property
    def expired(self) -> bool:
        """True once wall-clock time has passed ``expires_at`` — informational
        only (see the module docstring): nothing reaps a ref just because
        this is true."""
        if self.expires_at is None:
            return False
        return iso_now() > self.expires_at

    def to_doc(self) -> dict[str, Any]:
        """The wire/storage doc — camelCase keys, ready for ``json.dumps``."""
        return {
            "kind": self.kind,
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "createdAt": self.created_at,
            "expiresAt": self.expires_at,
            "reapedAt": self.reaped_at,
            "ttlSeconds": self.ttl_seconds,
            "metadata": self.metadata,
            "seq": self.seq,
        }

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> RefRecord:
        return cls(
            kind=doc["kind"],
            id=doc["id"],
            name=doc.get("name"),
            status=doc["status"],
            created_at=doc["createdAt"],
            expires_at=doc.get("expiresAt"),
            reaped_at=doc.get("reapedAt"),
            ttl_seconds=doc.get("ttlSeconds"),
            metadata=dict(doc.get("metadata") or {}),
            seq=int(doc.get("seq") or 0),
        )


class RefRegistry:
    """Generic named-ref registry with TTL and lifecycle.

    API surface: ``register``, ``get``, ``list``, ``touch``, ``expire``,
    ``reap`` — see each method's docstring. A caller that needs to associate
    child resources with a ref (session workspaces track graphs by namespace
    prefix; a future ``kind`` might track something else entirely) does that
    itself, in ``metadata`` or by its own convention — this module stores and
    times out refs, nothing more, so it never needs to change shape to serve
    a new kind of ref.
    """

    def __init__(self, redis: Redis, key_prefix: str = "loom") -> None:
        self._redis = redis
        self._prefix = key_prefix
        self._events = EventLog(redis, REF_GRAPH_SUFFIX, key_prefix)

    def _key(self, kind: str) -> str:
        return f"{self._prefix}:_refs:{kind}"

    def _commit(self, kind: str, ref_id: str, doc: dict[str, Any], event_type: str) -> RefRecord:
        """HSET the ref's doc and append its lifecycle event — carrying the
        doc verbatim, the same "payload is the full document" convention
        every other event in the store follows — as one MULTI/EXEC (see the
        module docstring for why this, and not
        ``theloom.store.commit.commit_steps``, is the primitive here)."""
        with self._redis.pipeline(transaction=True) as pipe:
            pipe.hset(self._key(kind), ref_id, json.dumps(doc))
            self._events.queue(pipe, event_type, doc)
            responses = pipe.execute()
        event_id = responses[-1]
        event_id = event_id if isinstance(event_id, str) else event_id.decode()
        receipts.record([event_id])
        return RefRecord.from_doc(doc)

    def register(
        self,
        kind: str,
        *,
        ref_id: str | None = None,
        name: str | None = None,
        ttl_seconds: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RefRecord:
        """Create a new ``active`` ref and return it.

        ``ref_id`` defaults to a fresh uuid4 hex; a caller that needs the id
        before the ref exists (session workspaces mint the id first, then use
        it to build the graph namespace they pass back in ``metadata``) may
        supply one explicitly. Registering an id that already exists under
        the same kind is a ``VALIDATION_ERROR`` — ids are not reused.
        """
        ref_id = ref_id or uuid.uuid4().hex
        if self.get(kind, ref_id) is not None:
            raise ValidationError(f"Ref '{ref_id}' already registered under kind '{kind}'")
        now = iso_now()
        # Monotonic per-kind registration sequence: ``createdAt`` alone cannot
        # order two refs registered within the same timestamp tick (observed
        # live in CI, where a Redis HGETALL's arbitrary iteration order
        # decided the tie), so ``list`` breaks ties on this counter instead.
        seq = int(self._redis.incr(f"{self._key(kind)}:seq"))
        doc = {
            "kind": kind,
            "id": ref_id,
            "name": name,
            "status": STATUS_ACTIVE,
            "createdAt": now,
            "expiresAt": _add_seconds(now, ttl_seconds) if ttl_seconds is not None else None,
            "reapedAt": None,
            "ttlSeconds": ttl_seconds,
            "metadata": metadata or {},
            "seq": seq,
        }
        return self._commit(kind, ref_id, doc, "ref_registered")

    def get(self, kind: str, ref_id: str) -> RefRecord | None:
        """The ref, or ``None`` if no such id is registered under ``kind``."""
        raw = self._redis.hget(self._key(kind), ref_id)
        if raw is None:
            return None
        return RefRecord.from_doc(json.loads(raw))

    def list(self, kind: str) -> list[RefRecord]:
        """Every ref of ``kind``, oldest first — reaped and expired included
        (a reaped ref is still a fact about what used to exist; ``list``
        never filters it out, a caller does that by ``status`` if it wants
        to)."""
        raw = self._redis.hgetall(self._key(kind))
        records = [RefRecord.from_doc(json.loads(value)) for value in raw.values()]
        return sorted(records, key=lambda record: (record.created_at, record.seq))

    def touch(self, kind: str, ref_id: str, ttl_seconds: int | None = None) -> RefRecord:
        """Refresh a ref's TTL from now. ``ttl_seconds`` overrides the ref's
        original TTL if given; omitted, the original TTL is reapplied from
        now (a ref registered with no TTL stays TTL-less)."""
        record = self._require(kind, ref_id)
        effective_ttl = ttl_seconds if ttl_seconds is not None else record.ttl_seconds
        now = iso_now()
        expires_at = _add_seconds(now, effective_ttl) if effective_ttl is not None else None
        updated = replace(record, expires_at=expires_at, ttl_seconds=effective_ttl)
        return self._commit(kind, ref_id, updated.to_doc(), "ref_touched")

    def expire(self, kind: str, ref_id: str) -> RefRecord:
        """Mark a ref ``expired`` without reaping anything. Idempotent: a ref
        already ``expired`` or ``reaped`` is returned unchanged."""
        record = self._require(kind, ref_id)
        if record.status != STATUS_ACTIVE:
            return record
        updated = replace(record, status=STATUS_EXPIRED)
        return self._commit(kind, ref_id, updated.to_doc(), "ref_expired")

    def reap(self, kind: str, ref_id: str) -> RefRecord:
        """Mark a ref ``reaped``. Idempotent: reaping an already-reaped ref
        is a no-op that returns it unchanged, so a caller that deletes the
        ref's children before calling this can tell (by comparing
        ``record.status`` beforehand) whether this call actually did
        anything — the ``applied`` truthfulness the CLI contract needs.
        Reaping never deletes the ref's own record: it stays listable as
        history."""
        record = self._require(kind, ref_id)
        if record.status == STATUS_REAPED:
            return record
        updated = replace(record, status=STATUS_REAPED, reaped_at=iso_now())
        return self._commit(kind, ref_id, updated.to_doc(), "ref_reaped")

    def update_metadata(self, kind: str, ref_id: str, patch: dict[str, Any]) -> RefRecord:
        """Merge ``patch`` into a ref's ``metadata`` and commit, same as
        every other write here (one event, kind-agnostic). Callers own their
        own metadata shape; this never touches the ref's own lifecycle
        fields (``status``/TTL) — only its opaque payload. Branchable belief
        worlds (kind ``"world"``) use this for the domain-status axis
        (``active``/``merged``/``abandoned``) a world tracks alongside, and
        independently of, this registry's own ``active``/``expired``/
        ``reaped`` ref lifecycle.
        """
        record = self._require(kind, ref_id)
        updated = replace(record, metadata={**record.metadata, **patch})
        return self._commit(kind, ref_id, updated.to_doc(), "ref_metadata_updated")

    def purge(self, kind: str, ref_id: str) -> None:
        """Permanently erase a ref's record — unlike ``reap`` (which marks
        it dead but keeps it listable as history forever), this removes it
        outright. For a ref whose *entire* lifecycle a caller owns
        end-to-end within one call (belief-blast-radius's throwaway fork:
        it forks, uses, and discards a world in one composite, and no
        other caller ever has a reason to look it up afterward) reap-and-
        keep would grow the registry forever with entries nobody will ever
        read again. No event: a purged ref, from every other caller's
        perspective, never existed — there is nothing to replay. Silently
        a no-op if the ref is already gone (idempotent, matching ``reap``'s
        tolerance of being called twice)."""
        self._redis.hdel(self._key(kind), ref_id)

    def _require(self, kind: str, ref_id: str) -> RefRecord:
        record = self.get(kind, ref_id)
        if record is None:
            raise NotFoundError(f"Ref '{ref_id}' not found under kind '{kind}'")
        return record

    def wipe(self, kind: str) -> None:
        """Drop every ref of ``kind`` (reseeding / migration path — mirrors
        ``MultiGraph.wipe``'s per-store wipes; does not touch the shared
        event stream, which ``EventLog.delete()`` on ``events()`` handles)."""
        self._redis.delete(self._key(kind))

    @property
    def events(self) -> EventLog:
        """The shared, reserved ``_refs`` event stream every kind logs to."""
        return self._events


def _add_seconds(iso_timestamp: str, seconds: int) -> str:
    """``iso_timestamp`` (the ``iso_now()`` wire format) plus ``seconds``, in
    the same format — kept in lockstep with ``theloom.timeutil.iso_now`` so
    ``expires_at`` stays lexicographically comparable like every other
    timestamp in the store."""
    base = datetime.strptime(iso_timestamp, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    later = base + timedelta(seconds=seconds)
    return later.strftime("%Y-%m-%dT%H:%M:%S.") + f"{later.microsecond // 1000:03d}Z"
