"""Append-only event log — one Redis Stream per graph.

Stream key: ``{prefix}:{graph}:events``. Every store mutation appends an event
(entity_created/updated/status_changed/deleted, relation_created/updated/
deleted) whose payload carries the full document(s) involved, so history is
replayable and "session changelog"-class queries read the log rather than
trusting mutable pointers.

Atomicity: FalkorDB *is* the Redis server, so the graph mutation and the
stream append are two commands against one connection and go out together in a
single MULTI/EXEC transaction (see ``FalkorGraphStore._commit``). ``queue``
buffers an append onto that transaction; ``discard`` is its compensation, used
when the graph half of the transaction reports a server-side error (Redis
executes every queued command regardless, so the event has to be rolled back
out by id).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from redis import Redis
from redis.client import Pipeline


@dataclass(frozen=True)
class Event:
    """One event from a graph's stream."""

    id: str
    type: str
    payload: dict[str, Any]


class EventLog:
    """The append-only event stream for one named graph."""

    def __init__(self, redis: Redis, graph_name: str, key_prefix: str = "loom") -> None:
        self._redis = redis
        self.key = f"{key_prefix}:{graph_name}:events"

    def append(self, event_type: str, payload: dict[str, Any]) -> str:
        """Append one event; returns the stream entry id."""
        entry_id = self._redis.xadd(self.key, {"type": event_type, "payload": json.dumps(payload)})
        return entry_id if isinstance(entry_id, str) else entry_id.decode()

    def queue(self, pipe: Pipeline, event_type: str, payload: dict[str, Any]) -> None:
        """Buffer an append onto an open transaction instead of sending it now.

        The caller's ``EXEC`` decides: the append lands with the mutation it
        belongs to, or — if anything raises while the transaction is still
        being built — neither ever reaches the server.
        """
        pipe.xadd(self.key, {"type": event_type, "payload": json.dumps(payload)})

    def discard(self, entry_ids: Sequence[str]) -> None:
        """Remove already-appended entries (compensation for a failed mutation).

        Redis has no rollback: every command queued in a transaction runs even
        when an earlier one errors, so an event appended alongside a mutation
        that the server rejected has to be deleted after the fact.
        """
        if entry_ids:
            self._redis.xdel(self.key, *entry_ids)

    def append_many(self, events: list[tuple[str, dict[str, Any]]]) -> None:
        """Append a batch of events in one pipelined round trip (batch mutations)."""
        if not events:
            return
        with self._redis.pipeline(transaction=False) as pipe:
            for event_type, payload in events:
                pipe.xadd(self.key, {"type": event_type, "payload": json.dumps(payload)})
            pipe.execute()

    def read_all(self) -> list[Event]:
        """All events in append order."""
        entries = self._redis.xrange(self.key) or []
        events: list[Event] = []
        for entry_id, fields in entries:
            if entry_id is None or fields is None:
                continue
            events.append(
                Event(
                    id=entry_id if isinstance(entry_id, str) else entry_id.decode(),
                    type=_field(fields, "type"),
                    payload=json.loads(_field(fields, "payload")),
                )
            )
        return events

    def delete(self) -> None:
        """Drop the stream (graph deletion / reseeding)."""
        self._redis.delete(self.key)


def _field(fields: dict[Any, Any], name: str) -> str:
    value = fields.get(name, fields.get(name.encode()))
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode()
    raise KeyError(name)
