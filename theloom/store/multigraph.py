"""Multi-graph management + the cross-graph bridge registry.

Graphs are FalkorDB-native named graphs tracked in a Redis set
(``{prefix}:graphs``) so empty graphs exist before their first write. Bridges
live in a Redis list (``{prefix}:bridges``) preserving insertion order; writes
go through WATCH/MULTI so concurrent CLI invocations cannot lose updates.

Semantics: graph names match ``^[a-zA-Z0-9_-]+$`` and may not start with
``_``; the default graph is undeletable; ``list_graphs`` sorts by name; a
bridge is keyed by (from, to, relationType) and duplicates are rejected; a
relation whose endpoints live in different graphs becomes a bridge.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from typing import Any

from falkordb import FalkorDB
from redis import Redis

from theloom.documents.chunkstore import CHUNK_GRAPH_SUFFIX, ChunkStore
from theloom.errors import NotFoundError, OperationError, ValidationError
from theloom.extraction.runstore import RunStore
from theloom.model import RelationCreate
from theloom.store.events import EventLog
from theloom.store.falkor import FalkorGraphStore
from theloom.timeutil import iso_now

GRAPH_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

BridgeDoc = dict[str, Any]
BridgeFilter = Mapping[str, str]


class BridgeRegistry:
    """Ordered, transactional store of cross-graph bridge relations."""

    def __init__(self, redis: Redis, key_prefix: str = "loom") -> None:
        self._redis = redis
        self.key = f"{key_prefix}:bridges"

    def list_bridges(self, filter: BridgeFilter | None = None) -> list[BridgeDoc]:
        raw: list[Any] = list(self._redis.lrange(self.key, 0, -1))
        bridges: list[BridgeDoc] = [json.loads(item) for item in raw]
        if not filter:
            return bridges
        return [b for b in bridges if self._matches(b, filter)]

    @staticmethod
    def _matches(bridge: BridgeDoc, filter: BridgeFilter) -> bool:
        if "from_graph" in filter and bridge["from_graph"] != filter["from_graph"]:
            return False
        if "to_graph" in filter and bridge["to_graph"] != filter["to_graph"]:
            return False
        return not (
            "entity_id" in filter and filter["entity_id"] not in (bridge["from"], bridge["to"])
        )

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

    def create_bridge(self, doc: Mapping[str, Any]) -> BridgeDoc:
        """Create a bridge; rejects a duplicate (from, to, relationType)."""
        now = iso_now()
        full: BridgeDoc = {
            **dict(doc),
            "id": str(uuid.uuid4()),
            "created_at": now,
            "updated_at": now,
        }
        with self._redis.pipeline(transaction=True) as pipe:
            pipe.watch(self.key)  # type: ignore[no-untyped-call]
            existing = [json.loads(item) for item in pipe.lrange(self.key, 0, -1)]
            for bridge in existing:
                if (
                    bridge["from"] == full["from"]
                    and bridge["to"] == full["to"]
                    and bridge["relationType"] == full["relationType"]
                ):
                    pipe.unwatch()
                    raise OperationError(
                        f"Bridge already exists from {full['from']} to {full['to']} "
                        f"with relationType {full['relationType']}"
                    )
            pipe.multi()
            pipe.rpush(self.key, json.dumps(full))
            pipe.execute()
        return full

    def import_bridge_doc(self, doc: Mapping[str, Any]) -> None:
        """Append a pre-existing bridge doc verbatim (migration path)."""
        self._redis.rpush(self.key, json.dumps(dict(doc)))

    def delete_bridge(self, from_id: str, to_id: str, relation_type: str | None = None) -> None:
        with self._redis.pipeline(transaction=True) as pipe:
            pipe.watch(self.key)  # type: ignore[no-untyped-call]
            raw = pipe.lrange(self.key, 0, -1)
            target: str | None = None
            for item in raw:
                bridge = json.loads(item)
                if bridge["from"] != from_id or bridge["to"] != to_id:
                    continue
                if relation_type is not None and bridge["relationType"] != relation_type:
                    continue
                target = item if isinstance(item, str) else item.decode()
                break
            if target is None:
                pipe.unwatch()
                suffix = f" with relationType {relation_type}" if relation_type else ""
                raise NotFoundError(f"Bridge not found from {from_id} to {to_id}{suffix}")
            pipe.multi()
            pipe.lrem(self.key, 1, target)
            pipe.execute()

    def delete_all(self) -> None:
        self._redis.delete(self.key)


class MultiGraph:
    """The multi-graph facade: named graphs, their stores, and bridges."""

    def __init__(
        self,
        db: FalkorDB,
        redis: Redis,
        default_graph: str = "default",
        key_prefix: str = "loom",
    ) -> None:
        self._db = db
        self._redis = redis
        self._prefix = key_prefix
        self.default_graph = default_graph
        self.bridges = BridgeRegistry(redis, key_prefix)
        self._registry_key = f"{key_prefix}:graphs"
        redis.sadd(self._registry_key, default_graph)

    # -- graphs ------------------------------------------------------------------

    def graph_names(self) -> list[str]:
        members: set[Any] = set(self._redis.smembers(self._registry_key))
        return sorted(m if isinstance(m, str) else m.decode() for m in members)

    def has_graph(self, name: str) -> bool:
        return bool(self._redis.sismember(self._registry_key, name))

    def list_graphs(self) -> list[dict[str, Any]]:
        """GraphInfo objects as the one-shot CLI reports them: nothing is lazily
        loaded in a single-command process, so ``loaded`` is always false and
        stats are omitted."""
        return [{"name": name, "loaded": False} for name in self.graph_names()]

    def create_graph(self, name: str) -> None:
        if not name or name.startswith("_") or not GRAPH_NAME_RE.match(name):
            raise ValidationError(
                f"Invalid graph name '{name}'. Names must be alphanumeric with "
                "optional hyphens/underscores and cannot start with underscore."
            )
        added = self._redis.sadd(self._registry_key, name)
        if not added:
            raise OperationError(f"Graph '{name}' already exists")

    def register_graph(self, name: str) -> None:
        """Register without the exists-check (migration path)."""
        self._redis.sadd(self._registry_key, name)

    def delete_graph(self, name: str) -> None:
        if name == self.default_graph:
            raise OperationError(f"Cannot delete the default graph '{name}'")
        removed = self._redis.srem(self._registry_key, name)
        if not removed:
            raise NotFoundError(f"Graph '{name}' not found")
        self.get_store(name).delete_graph_data()

    def get_store(self, name: str | None = None) -> FalkorGraphStore:
        return FalkorGraphStore(self._db, self._redis, name or self.default_graph, self._prefix)

    def chunk_store(self) -> ChunkStore:
        """The global document-chunk store (not graph-scoped)."""
        return ChunkStore(self._db, self._prefix, self._redis)

    def chunk_event_log(self) -> EventLog:
        """The append-only stream of document-chunk writes (not graph-scoped)."""
        return EventLog(self._redis, CHUNK_GRAPH_SUFFIX, self._prefix)

    def run_store(self) -> RunStore:
        """The extraction-run store (event-log-backed)."""
        return RunStore(self._redis, self._prefix)

    def event_log(self, name: str | None = None) -> EventLog:
        """The append-only event stream for one named graph (viz/history reads)."""
        return EventLog(self._redis, name or self.default_graph, self._prefix)

    def wipe(self) -> None:
        """Remove every graph, bridge, and event stream under this prefix
        (reseeding / migration path)."""
        for name in self.graph_names():
            self.get_store(name).delete_graph_data()
        self.bridges.delete_all()
        self.chunk_store().wipe()
        self.run_store().wipe()
        self._redis.delete(self._registry_key)
        self._redis.sadd(self._registry_key, self.default_graph)

    # -- cross-graph relations ------------------------------------------------------

    def find_entity_graph(self, entity_id: str) -> str | None:
        """The graph containing the entity, or None."""
        for name in self.graph_names():
            if self.get_store(name).read_entity(entity_id) is not None:
                return name
        return None

    def create_relation(self, spec: RelationCreate, graph: str | None = None) -> dict[str, Any]:
        """Create a relation, auto-bridging when the endpoints span graphs."""
        from_graph = self.find_entity_graph(spec.from_)
        to_graph = self.find_entity_graph(spec.to)
        if from_graph and to_graph and from_graph != to_graph:
            existing = self.bridges.read_bridge(spec.from_, spec.to, spec.relation_type.value)
            if existing is not None:
                return {"relation": existing, "bridgeCreated": False}
            doc = spec.model_dump(by_alias=True, exclude_unset=True)
            doc["from_graph"] = from_graph
            doc["to_graph"] = to_graph
            bridge = self.bridges.create_bridge(doc)
            return {"relation": bridge, "bridgeCreated": True}
        relation = self.get_store(graph or from_graph).create_relation(spec)
        return {
            "relation": relation.model_dump(by_alias=True, exclude_unset=True),
            "bridgeCreated": False,
        }
