"""Cross-graph bridges: event-sourced, bi-temporal store state.

A bridge is a relation whose endpoints live in different graphs, so no single
graph can hold it. It used to live in a raw Redis list — mutated in place, with
no event log and no history, which is exactly what every other record in the
Loom is not. Bridges are now records like any other:

- **Where.** ``:_Bridge`` nodes in a dedicated per-prefix graph
  (``{prefix}:graph:_bridges``), NOT inside any knowledge graph — a bridge
  belongs to no one graph, the same way a document chunk doesn't. Each node
  carries the verbatim wire doc in ``_doc``, the bi-temporal ``tx_from`` /
  ``tx_to`` bounds, and a ``dedupe`` key (``from|to|relationType``). Scan order
  is ``id(b)``, so insertion order — the read API's promise — survives.
- **How.** Every write is one Cypher statement plus its event append committed
  as a single MULTI/EXEC unit through :mod:`theloom.store.commit`, with that
  module's compensation in both directions.
- **History.** Removal *invalidates*: ``delete_bridge`` stamps ``tx_to`` and
  leaves the record, so a removed bridge is still readable
  (``list_bridge_history``) and the (from, to, relationType) triple is free to
  be bridged again. Nothing is overwritten in place.
- **Events.** Their own stream, ``{prefix}:_bridges:events`` (bridges are not
  graph-scoped, so their history replays independently of any graph's log):
  ``bridge_created``, ``bridge_invalidated``, ``bridge_migrated``.

Duplicate rejection is inside the write, not merely in front of it: the CREATE
is guarded by an ``OPTIONAL MATCH`` on the live dedupe key, so a losing racer
creates nothing, and the event queued beside the no-op create is discarded
before the typed error propagates.

**Legacy list migration.** Bridges written by an older version still sit in
``{prefix}:bridges``. The first access to the registry migrates them in place:
the list is claimed with a single ``RENAMENX`` onto ``{prefix}:bridges:migrating``
— so a claim key an earlier run left behind is drained first, never overwritten
— and each doc is written through the new path (verbatim, plus a
``bridge_migrated`` event) *before* being removed from the claim key, by value.
A crash mid-migration therefore leaves its docs on the claim key and the next
access resumes from them. Nothing on the claim key is ever dropped wholesale,
so no bridge is lost to a racer that refilled the key between another
process's write and its removal; two processes racing can at worst write the
same doc twice (the row MERGEs by id, only the event duplicates).
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Mapping
from typing import Any

from falkordb import FalkorDB
from redis import Redis
from redis.exceptions import ResponseError

from theloom.errors import NotFoundError, OperationError
from theloom.store.commit import Step, commit_steps
from theloom.store.events import EventLog
from theloom.store.paging import fetch_all_rows
from theloom.timeutil import iso_now

BridgeDoc = dict[str, Any]
BridgeFilter = Mapping[str, str]
BridgeRecord = dict[str, Any]

BRIDGE_GRAPH_SUFFIX = "_bridges"

# Ceiling on a full bridge scan. Bridges are cross-graph relations, not a bulk
# data set, and the paged read keeps the server's RESULTSET_SIZE cap from
# silently shortening the list.
_MAX_BRIDGES = 1_000_000

# The create is its own precondition: nothing is written when a *live* bridge
# already holds the dedupe key, so two racing creators cannot both land.
_CREATE_IF_ABSENT = (
    "OPTIONAL MATCH (e:_Bridge {dedupe: $key}) WHERE e.tx_to IS NULL "
    "WITH count(e) AS existing WHERE existing = 0 "
    "CREATE (b:_Bridge {id: $id, dedupe: $key, _doc: $doc, tx_from: $tx})"
)

_MERGE_VERBATIM = "MERGE (b:_Bridge {id: $id}) SET b.dedupe = $key, b._doc = $doc, b.tx_from = $tx"

_INVALIDATE = "MATCH (b:_Bridge {id: $id}) WHERE b.tx_to IS NULL SET b.tx_to = $tx"


class BridgeRegistry:
    """Ordered, event-sourced, bi-temporal store of cross-graph bridges."""

    def __init__(self, db: FalkorDB, redis: Redis, key_prefix: str = "loom") -> None:
        self._redis = redis
        self._graph = db.select_graph(f"{key_prefix}:graph:{BRIDGE_GRAPH_SUFFIX}")
        self.events = EventLog(redis, BRIDGE_GRAPH_SUFFIX, key_prefix)
        self.legacy_key = f"{key_prefix}:bridges"
        self._claim_key = f"{key_prefix}:bridges:migrating"
        self._migrated = False

    # -- reads -------------------------------------------------------------------

    def list_bridges(self, filter: BridgeFilter | None = None) -> list[BridgeDoc]:
        """The live bridges, in insertion order."""
        self._ensure_migrated()
        return [doc for doc, _, _ in self._records(live_only=True) if _matches(doc, filter)]

    def list_bridge_history(self, filter: BridgeFilter | None = None) -> list[BridgeRecord]:
        """Every bridge record ever written, invalidated ones included.

        Each entry is ``{"bridge": doc, "txFrom": ..., "txTo": ...}``; ``txTo``
        is None for a bridge that is still live.
        """
        self._ensure_migrated()
        return [
            {"bridge": doc, "txFrom": tx_from, "txTo": tx_to}
            for doc, tx_from, tx_to in self._records(live_only=False)
            if _matches(doc, filter)
        ]

    def read_bridge(
        self, from_id: str, to_id: str, relation_type: str | None = None
    ) -> BridgeDoc | None:
        for bridge in self.list_bridges():
            if bridge["from"] != from_id or bridge["to"] != to_id:
                continue
            if relation_type is not None and bridge["relationType"] != relation_type:
                continue
            return bridge
        return None

    # -- writes ------------------------------------------------------------------

    def create_bridge(self, doc: Mapping[str, Any]) -> BridgeDoc:
        """Create a bridge; rejects a duplicate (from, to, relationType)."""
        self._ensure_migrated()
        now = iso_now()
        full: BridgeDoc = {
            **dict(doc),
            "id": str(uuid.uuid4()),
            "created_at": now,
            "updated_at": now,
        }
        results, event_ids = commit_steps(
            self._redis,
            self._graph,
            self.events,
            [(_CREATE_IF_ABSENT, _write_params(full, now))],
            [("bridge_created", {"bridge": full})],
        )
        if not int(results[0].nodes_created or 0):
            # A live bridge already holds this triple, so the CREATE was a
            # no-op and the event queued beside it was never earned.
            self.events.discard(event_ids)
            raise OperationError(
                f"Bridge already exists from {full['from']} to {full['to']} "
                f"with relationType {full['relationType']}"
            )
        return full

    def import_bridge_doc(self, doc: Mapping[str, Any]) -> None:
        """Write a pre-existing bridge doc verbatim, unlogged (snapshot import).

        Imported docs are historical state, not new mutations — the same
        contract as ``FalkorGraphStore.import_entity_doc`` — so no event is
        appended and the doc's own ``created_at`` is the system-time lower
        bound.
        """
        self._ensure_migrated()
        self._graph.query(_MERGE_VERBATIM, _write_params(dict(doc)))

    def delete_bridge(self, from_id: str, to_id: str, relation_type: str | None = None) -> None:
        """Invalidate a bridge: it leaves the live set but stays in history."""
        self._ensure_migrated()
        target = self.read_bridge(from_id, to_id, relation_type)
        if target is None:
            raise NotFoundError(_missing(from_id, to_id, relation_type))
        now = iso_now()
        results, event_ids = commit_steps(
            self._redis,
            self._graph,
            self.events,
            [(_INVALIDATE, {"id": target["id"], "tx": now})],
            [("bridge_invalidated", {"bridge": target, "tx_to": now})],
        )
        if not int(results[0].properties_set or 0):
            # Invalidated by a concurrent writer between the read and the
            # write: nothing moved here, so the event is not ours to keep.
            self.events.discard(event_ids)
            raise NotFoundError(_missing(from_id, to_id, relation_type))

    def delete_all(self) -> None:
        """Drop every bridge, its history, and the legacy list (reseed path).

        A wipe that failed must not read as a wipe that happened: a reseed on
        top of surviving bridges is silently wrong, so the failure is raised
        rather than swallowed. A store that never held a bridge is not a
        failure — the MATCH simply matches nothing.
        """
        try:
            self._graph.query("MATCH (b:_Bridge) DELETE b")
            self.events.delete()
        except Exception as exc:
            raise OperationError(f"Failed to clear bridges: {exc}") from exc
        self._redis.delete(self.legacy_key, self._claim_key)
        self._migrated = True

    # -- internals ---------------------------------------------------------------

    def _rows(self, cypher: str, params: dict[str, Any] | None = None) -> list[list[Any]]:
        result = self._graph.query(cypher, params or {})
        rows: list[list[Any]] = result.result_set or []
        return rows

    def _records(self, *, live_only: bool) -> list[tuple[BridgeDoc, str, str | None]]:
        where = "WHERE b.tx_to IS NULL " if live_only else ""
        rows = fetch_all_rows(
            self._rows,
            f"MATCH (b:_Bridge) {where}RETURN b._doc, b.tx_from, b.tx_to ORDER BY id(b)",
            limit=_MAX_BRIDGES,
        )
        return [(json.loads(row[0]), row[1], row[2]) for row in rows]

    def _ensure_migrated(self) -> None:
        """Move any legacy-list bridges onto the event-sourced path, once."""
        if self._migrated:
            return
        while True:
            self._claim_legacy_list()
            if not self._drain_claim():
                break
        self._migrated = True

    def _claim_legacy_list(self) -> None:
        """Take ownership of the legacy list, when it is free to take.

        ``RENAMENX`` *is* the claim — one command, so exactly one racing
        process moves the list onto the claim key and a claim key already
        holding an earlier run's undrained docs is never overwritten. That run
        died mid-migration: its docs are drained first and the legacy list
        waits for the next pass.
        """
        with contextlib.suppress(ResponseError):  # no legacy list to claim
            self._redis.renamenx(self.legacy_key, self._claim_key)

    def _drain_claim(self) -> bool:
        """Migrate every doc under the claim key; True if any moved.

        A doc leaves the claim key only after its own write has committed, and
        it leaves *by value* (``LREM``) rather than by dropping the key: a
        racer that migrated the same doc first has already removed it, so this
        ``LREM`` matches nothing instead of discarding whatever the key holds
        by then — which may be a legacy list the racer claimed in the meantime,
        not yet migrated by anyone.
        """
        moved = False
        while True:
            raw = self._redis.lindex(self._claim_key, 0)
            if raw is None:
                return moved
            text = raw if isinstance(raw, str) else raw.decode()
            doc: BridgeDoc = json.loads(text)
            commit_steps(
                self._redis,
                self._graph,
                self.events,
                [self._merge_step(doc)],
                [("bridge_migrated", {"bridge": doc, "source": "legacy_list"})],
            )
            self._redis.lrem(self._claim_key, 1, text)
            moved = True

    def _merge_step(self, doc: BridgeDoc) -> Step:
        return (_MERGE_VERBATIM, _write_params(doc))


def _write_params(doc: BridgeDoc, tx_from: str | None = None) -> dict[str, Any]:
    """Node properties for one bridge doc: identity, dedupe key, wire doc, tx_from.

    A doc that predates the event-sourced registry (or a snapshot import) dates
    from its own ``created_at``, not from the moment it was carried over. An
    id is minted in place for the (malformed) doc that arrives without one, so
    the stored ``_doc`` and the event payload agree on identity.
    """
    doc.setdefault("id", str(uuid.uuid4()))
    return {
        "id": doc["id"],
        "key": _dedupe_key(doc),
        "doc": json.dumps(doc),
        "tx": tx_from or doc.get("created_at") or iso_now(),
    }


def _dedupe_key(doc: BridgeDoc) -> str:
    return f"{doc['from']}|{doc['to']}|{doc['relationType']}"


def _matches(bridge: BridgeDoc, filter: BridgeFilter | None) -> bool:
    if not filter:
        return True
    if "from_graph" in filter and bridge["from_graph"] != filter["from_graph"]:
        return False
    if "to_graph" in filter and bridge["to_graph"] != filter["to_graph"]:
        return False
    return not ("entity_id" in filter and filter["entity_id"] not in (bridge["from"], bridge["to"]))


def _missing(from_id: str, to_id: str, relation_type: str | None) -> str:
    suffix = f" with relationType {relation_type}" if relation_type else ""
    return f"Bridge not found from {from_id} to {to_id}{suffix}"
