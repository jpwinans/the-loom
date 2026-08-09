"""Append-only event log — one Redis Stream per graph.

Stream key: ``{prefix}:{graph}:events``. Every store mutation appends an event
(entity_created/updated/status_changed/deleted, relation_created/updated/
deleted) whose payload carries the full document(s) involved, so history is
replayable and "session changelog"-class queries read the log rather than
trusting mutable pointers. Document chunks are global rather than graph-scoped,
so they log to the reserved name ``_chunks`` (chunk_created / chunk_deleted /
chunks_deleted — see ``theloom.documents.chunkstore``).

Atomicity: FalkorDB *is* the Redis server, so the graph mutation and the
stream append are two commands against one connection and go out together in a
single MULTI/EXEC transaction (see ``theloom.store.commit``). ``queue``
buffers an append onto that transaction, and the two commands compensate each
other in whichever direction the failure runs — Redis executes every queued
command regardless of its neighbours, so exactly one half can fail at EXEC:

- the graph half failed: the event is not earned, so ``discard`` deletes it by
  id;
- the stream half failed: the mutation is applied and unrollbackable, so the
  event is *true* and ``repair`` appends it again outside the transaction.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from redis import Redis
from redis.client import Pipeline

from theloom.store import receipts

CAUSED_BY_FIELD = "causedBy"


@dataclass(frozen=True)
class Event:
    """One event from a graph's stream."""

    id: str
    type: str
    payload: dict[str, Any]
    # When the event was appended — recovered from the Redis Stream entry id's
    # leading `<epoch-ms>-<seq>` field, ISO 8601 UTC. Parsed here, on the store
    # side of the seam, so callers (theloom.viz.temporal) never need to know
    # the stream id encodes a timestamp at all.
    timestamp: str
    # The CLI command whose dispatch was active when this event was appended
    # (see theloom.store.receipts), or None for an event appended outside any
    # command dispatch (a direct store call in a test, a migration script) or
    # written before this field existed — always optional, never inferred.
    caused_by: str | None = None


@dataclass(frozen=True)
class RepairResult:
    """The outcome of ``EventLog.repair``: what landed, and what stopped it.

    ``appended`` holds the ids of the leading events that were re-appended, in
    order; ``error`` is the failure that halted the run (``None`` when every
    event landed). The events at and after ``len(appended)`` are untouched.
    """

    appended: list[str]
    error: Exception | None


class EventLog:
    """The append-only event stream for one named graph."""

    def __init__(self, redis: Redis, graph_name: str, key_prefix: str = "loom") -> None:
        self._redis = redis
        self.key = f"{key_prefix}:{graph_name}:events"

    def _fields(self, event_type: str, payload: dict[str, Any]) -> dict[str, str]:
        """The XADD field map for one event, stamped with the causing
        command's name when a ``receipts.collecting()`` scope is active
        (omitted, not sent as a literal null, when none is)."""
        fields = {"type": event_type, "payload": json.dumps(payload)}
        caused_by = receipts.current_command()
        if caused_by is not None:
            fields[CAUSED_BY_FIELD] = caused_by
        return fields

    def append(self, event_type: str, payload: dict[str, Any]) -> str:
        """Append one event; returns the stream entry id."""
        # redis-py's stub wants a union-keyed dict even though a plain
        # str-keyed one is exactly what every XADD caller in this codebase
        # (including the pre-existing inline-literal calls this replaces) has
        # always passed — a stub mismatch, not a real type hazard.
        entry_id = self._redis.xadd(self.key, self._fields(event_type, payload))  # type: ignore[arg-type]
        return _decode_id(entry_id)

    def queue(self, pipe: Pipeline, event_type: str, payload: dict[str, Any]) -> None:
        """Buffer an append onto an open transaction instead of sending it now.

        The caller's ``EXEC`` decides: the append lands with the mutation it
        belongs to, or — if anything raises while the transaction is still
        being built — neither ever reaches the server.
        """
        pipe.xadd(self.key, self._fields(event_type, payload))  # type: ignore[arg-type]

    def discard(self, entry_ids: Sequence[str]) -> None:
        """Remove already-appended entries (compensation for a failed mutation).

        Redis has no rollback: every command queued in a transaction runs even
        when an earlier one errors, so an event appended alongside a mutation
        that the server rejected has to be deleted after the fact. Also
        scrubs the ids from any active write-receipts collector
        (``theloom.store.receipts``): an id discarded here was never earned,
        so a response must never claim it.
        """
        if entry_ids:
            self._redis.xdel(self.key, *entry_ids)
            receipts.forget(entry_ids)

    def repair(self, events: Sequence[tuple[str, dict[str, Any]]]) -> RepairResult:
        """Re-append events whose queued XADD errored at ``EXEC``.

        The mirror image of ``discard``. A runtime rejection of the append (the
        stream key holding a non-stream value, a server-side refusal of the
        write) does not touch the graph mutation queued beside it, and Redis
        has no rollback to undo that mutation with — so the projection has
        moved and the event describing it is simply true. Appending it again,
        outside the transaction, is the only compensation the semantics allow.

        The retry is a plain ``XADD``, so a cause that outlives the transaction
        (a permanently mistyped key) raises again. A failure is *returned*
        rather than raised, and the run stops at it: the ids already appended
        stay visible to the caller (it owes them the same compensation as any
        other event it wrote), and the events behind the failure are left alone
        so nothing overtakes the one that could not land. The caller turns the
        remainder into a typed error naming exactly that gap rather than
        leaving the log silently short — or overstating it.
        """
        appended: list[str] = []
        for event_type, payload in events:
            try:
                appended.append(self.append(event_type, payload))
            except Exception as exc:  # noqa: BLE001 — reported, not swallowed
                return RepairResult(appended=appended, error=exc)
        return RepairResult(appended=appended, error=None)

    def append_many(self, events: list[tuple[str, dict[str, Any]]]) -> list[str]:
        """Append a batch of events in one pipelined round trip (batch
        mutations); returns their stream entry ids, in the same order."""
        if not events:
            return []
        with self._redis.pipeline(transaction=False) as pipe:
            for event_type, payload in events:
                pipe.xadd(self.key, self._fields(event_type, payload))  # type: ignore[arg-type]
            replies = pipe.execute()
        return [_decode_id(reply) for reply in replies]

    def read_all(self) -> list[Event]:
        """All events in append order."""
        entries = self._redis.xrange(self.key) or []
        return _build_events(entries)

    def read_range(
        self, start: str | None = None, end: str | None = None, count: int | None = None
    ) -> list[Event]:
        """Events whose stream id falls in ``[start, end]`` (both inclusive),
        append order. ``start``/``end`` default to the stream's own bounds
        (Redis ``-``/``+``); ``count`` caps how many entries are read, same as
        ``XRANGE ... COUNT``."""
        entries = self._redis.xrange(self.key, min=start or "-", max=end or "+", count=count) or []
        return _build_events(entries)

    def read_ids(self, entry_ids: Sequence[str]) -> list[Event]:
        """The events named by ``entry_ids``, in the ids' own order (not
        stream order — the caller may be replaying a specific receipt).

        An id absent from the stream (trimmed, or simply wrong) is silently
        skipped rather than raised on, the same tolerance ``read_all`` has for
        a compacted stream: a span is whatever of it still exists.
        """
        events_by_id: dict[str, Event] = {}
        for entry_id in entry_ids:
            entries = self._redis.xrange(self.key, min=entry_id, max=entry_id) or []
            for event in _build_events(entries):
                events_by_id[event.id] = event
        return [events_by_id[entry_id] for entry_id in entry_ids if entry_id in events_by_id]

    def delete(self) -> None:
        """Drop the stream (graph deletion / reseeding)."""
        self._redis.delete(self.key)

    def last_id(self) -> str | None:
        """The most recent entry id in the stream, or ``None`` if empty —
        a graph's "tip" (branchable belief worlds, desire 12: the id a fork
        with no ``asOf`` captures as ``forkedAtEventId``)."""
        entries = self._redis.xrevrange(self.key, count=1)
        return _decode_id(entries[0][0]) if entries else None

    def last_id_before(self, timestamp: str) -> str | None:
        """The most recent entry id at or before ``timestamp`` (the wire ISO
        format — see ``theloom.timeutil.iso_now``), or ``None`` if the stream
        has no entry that old. The bi-temporal-fork half of ``last_id``: a
        ``fork-world`` with ``asOf`` captures this instead of the live tip."""
        entries = self._redis.xrevrange(self.key, max=str(_iso_to_epoch_ms(timestamp)), count=1)
        return _decode_id(entries[0][0]) if entries else None

    def entry_id_timestamp(self, entry_id: str) -> str:
        """``entry_id``'s embedded epoch-ms, rendered in the wire ISO format
        (``theloom.timeutil.iso_now``'s exact shape) rather than
        ``Event.timestamp``'s plain ``.isoformat()`` — so a caller comparing
        it against ``tx_from``/``created_at``/other wire timestamps (a
        world's ``forkedAt``, derived from ``forkedAtEventId``) can do so by
        plain string comparison like every other timestamp in the store."""
        return _entry_id_to_wire_iso(entry_id)


def _build_events(entries: Sequence[tuple[Any, Any]]) -> list[Event]:
    events: list[Event] = []
    for entry_id, fields in entries:
        if entry_id is None or fields is None:
            continue
        entry_id = _decode_id(entry_id)
        events.append(
            Event(
                id=entry_id,
                type=_field(fields, "type"),
                payload=json.loads(_field(fields, "payload")),
                timestamp=_entry_id_to_iso(entry_id),
                caused_by=_optional_field(fields, CAUSED_BY_FIELD),
            )
        )
    return events


def _decode_id(value: Any) -> str:
    return value if isinstance(value, str) else value.decode()


def _entry_id_to_iso(entry_id: str) -> str:
    """A Redis Stream entry id is `<epoch-ms>-<seq>`; recover the epoch-ms
    field and render it as ISO 8601 UTC."""
    milliseconds = int(entry_id.split("-", maxsplit=1)[0])
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC).isoformat()


def _entry_id_to_wire_iso(entry_id: str) -> str:
    """Like ``_entry_id_to_iso``, but in the wire format (``iso_now()``'s
    exact ``YYYY-MM-DDTHH:MM:SS.mmmZ`` shape) instead of ``datetime``'s
    default ``.isoformat()`` — see ``EventLog.entry_id_timestamp``."""
    milliseconds = int(entry_id.split("-", maxsplit=1)[0])
    dt = datetime.fromtimestamp(milliseconds / 1000, tz=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _iso_to_epoch_ms(timestamp: str) -> int:
    """Inverse of ``_entry_id_to_wire_iso``: the wire ISO format back to
    epoch milliseconds, for bounding an ``XREVRANGE`` by wall-clock time."""
    dt = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _field(fields: dict[Any, Any], name: str) -> str:
    value = fields.get(name, fields.get(name.encode()))
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode()
    raise KeyError(name)


def _optional_field(fields: dict[Any, Any], name: str) -> str | None:
    value = fields.get(name, fields.get(name.encode()))
    if value is None:
        return None
    return value if isinstance(value, str) else value.decode()
