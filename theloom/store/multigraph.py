"""Multi-graph management + the cross-graph bridge registry + session
workspaces.

Graphs are FalkorDB-native named graphs tracked in a Redis set
(``{prefix}:graphs``) so empty graphs exist before their first write, and so
every graph a write ever lands on — explicit ``create_graph`` or an implicit
bare ``graph`` param — is a member: ``GraphSpace`` (``theloom/store/space.py``)
SADDs a graph into this same set, inside the same MULTI/EXEC as the mutation,
on every commit (see ``theloom.store.commit.commit_steps``'s ``register``
parameter). Bridges are store records like any other — ``:_Bridge`` nodes in
the reserved ``_bridges`` graph, event-logged and bi-temporal, migrated off
the old Redis list on first access (see :mod:`theloom.store.bridges`).
Sessions are refs — see :mod:`theloom.store.refs` for the generic mechanism
and the "-- session workspaces --" section below for what a session ref means
here specifically.

Semantics: graph names match ``^[a-zA-Z0-9_-]+$`` and may not start with
``_``; the default graph is undeletable; ``list_graphs`` sorts by name; a
bridge is keyed by (from, to, relationType) and duplicates are rejected; a
relation whose endpoints live in different graphs becomes a bridge; a session
is a namespace prefix plus a TTL, and ``end_session`` deletes every graph
currently registered under that namespace in one call.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from falkordb import FalkorDB
from redis import Redis

from theloom.documents.chunkstore import CHUNK_GRAPH_SUFFIX, ChunkStore
from theloom.errors import NotFoundError, OperationError, ValidationError
from theloom.extraction.runstore import RunStore
from theloom.model import RelationCreate
from theloom.store import worldctx
from theloom.store import worlds as worlds_module
from theloom.store.bridges import (
    BRIDGE_GRAPH_SUFFIX,
    BridgeDoc,
    BridgeFilter,
    BridgeRegistry,
)
from theloom.store.events import EventLog
from theloom.store.falkor import FalkorGraphStore
from theloom.store.refs import RefRecord, RefRegistry

GRAPH_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

__all__ = ["GRAPH_NAME_RE", "BridgeDoc", "BridgeFilter", "BridgeRegistry", "MultiGraph"]


def _session_doc(record: RefRecord, all_graph_names: list[str]) -> dict[str, Any]:
    """A session ref rendered for the CLI: its namespace's *current* member
    graphs, computed by prefix-scanning the live graph registry rather than
    tracked incrementally — so it can never drift from what ``list-graphs``
    itself would show, whether a member graph arrived via ``create-graph`` or
    an ad-hoc ``graph`` param on any other mutating command."""
    namespace = str(record.metadata.get("namespace", ""))
    graphs = [name for name in all_graph_names if namespace and name.startswith(namespace)]
    return {
        "sessionId": record.id,
        "name": record.name,
        "namespace": namespace,
        "status": record.status,
        "createdAt": record.created_at,
        "expiresAt": record.expires_at,
        "reapedAt": record.reaped_at,
        "ttlSeconds": record.ttl_seconds,
        "expired": record.expired,
        "graphs": graphs,
        "graphCount": len(graphs),
    }


class MultiGraph:
    """The multi-graph facade: named graphs, their stores, bridges, and
    session workspaces."""

    #: The ``theloom.store.refs.RefRegistry`` kind session workspaces use —
    #: exported so a future ``kind="world"`` consumer (desire 12 / Part 5)
    #: has a concrete sibling to pick a non-colliding kind next to.
    SESSION_KIND = "session"
    #: Branchable belief worlds (desire 12 / Part 5) — the sibling kind the
    #: comment above anticipated, reusing ``RefRegistry`` unmodified.
    WORLD_KIND = worlds_module.WORLD_KIND

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
        self.bridges = BridgeRegistry(db, redis, key_prefix)
        self.refs = RefRegistry(redis, key_prefix)
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

    def delete_graph(self, name: str) -> list[str]:
        """Delete a registered graph and its data; returns the ids of every
        world ref purged along with it (see ``_purge_world_refs_for_graph``).

        Two properties, both about not half-applying: validation happens
        entirely before any mutation, and the mutations themselves (data,
        then registry) are ordered so a failure never leaves a
        deregistered-but-undeleted orphan behind.

        Refuses outright whenever ANY non-``main`` world is ambient
        (``theloom.store.worldctx``), regardless of whether it names ``name``
        coherently. An earlier version of this method ran a coherence check
        alone (``get_store(name)``, discarding the result) and let a
        *coherent* world reference through to delete the base graph anyway —
        reasoning that worlds have no registry entry of their own, so there
        is no meaningful "delete this graph inside a world" to redirect to.
        That was itself the bug: it deleted ``main``'s own data — the
        world's own parent — while a live fork's ``baseGraph`` still pointed
        at it, from inside what should have been a read/write scope confined
        to the fork's own segment. ``main`` is never mutable from inside a
        fork (the same invariant every ``WorldGraphStore`` write override
        upholds by copying-on-write into the fork's segment instead of
        touching the parent) — deleting graph data is no exception, coherent
        target or not. A caller that genuinely means to delete the base
        graph does so with no world active (``abandon-world``/``merge-world``
        the fork first, or simply omit ``world``).
        """
        if name == self.default_graph:
            raise OperationError(f"Cannot delete the default graph '{name}'")
        if not self.has_graph(name):
            raise NotFoundError(f"Graph '{name}' not found")
        effective_world = worldctx.current()
        if effective_world not in (None, worldctx.MAIN):
            raise ValidationError(
                f"delete-graph cannot run inside world '{effective_world}' — main is never "
                "mutable from inside a fork. Abandon or merge the world first, or omit "
                "`world` to delete the base graph directly."
            )
        self.plain_store(name).delete_graph_data()
        self._redis.srem(self._registry_key, name)
        return self._purge_world_refs_for_graph(name)

    def _purge_world_refs_for_graph(self, graph_name: str) -> list[str]:
        """Purge every world ref whose ``baseGraph`` is ``graph_name`` —
        called from ``delete_graph``, the one funnel every graph-deletion
        path already goes through (``end_session``'s per-member reap, the
        standalone ``delete-graph`` command, ``theloom.operations.
        verification``'s temp-graph cleanup), so a world ref can never
        outlive the graph it was forked from regardless of which caller
        deleted it.

        A world ref surviving its base graph's deletion is permanently
        dangling — its ``worldId``/``diffWorldsHandle`` can never be used
        again, the base graph ``theloom.store.worlds.resolve_layers`` needs
        to answer any read is gone — so this purges the ref outright
        (``purge_world``, not ``abandon_world``) rather than merely marking
        it reaped: ``list_worlds(include_reaped=True)`` must not keep
        showing something nothing can act on. Every world whose
        ``metadata["baseGraph"]`` matches is caught regardless of fork
        depth — ``theloom.store.worlds.fork_world`` always propagates the
        ROOT base graph onto a fork-of-a-fork's own ref, never just the
        immediate parent's — so no ancestor-chain walk is needed here.

        Already-``reaped`` refs (abandoned or merged) are purged too:
        ``reaped`` only ever meant "no segment left to replay," not "no
        longer dangling" — a merged dream's history is meaningless to query
        once the graph it was about no longer exists. This does not
        conflict with ``since-last-session``/``consolidate``'s own reliance
        on ``list_worlds(include_reaped=True)`` for merged/abandoned dream
        history (``ALL_DREAMS_REVIEWED``, ``find_reports``'s credit-pass
        diff boundary): both filter by ``baseGraph`` themselves and are only
        ever asked about a graph that still exists, so they never observe
        a ref this method has pruned — the graph they'd be asking about is,
        by construction, exactly the one just deleted.

        No event is appended for the purge, deliberately mirroring how
        deleting the graph's own data is itself not evented
        (``FalkorGraphStore.delete_graph_data`` appends nothing): the ref
        going away is not a new fact being recorded, it is the ref
        catching up to a fact (the graph's absence) that already happened.
        ``end_session``'s own ``eventIds`` therefore stay exactly what they
        were before this method existed — the session ref's own
        ``ref_reaped`` event, nothing invented and nothing lost. The purge
        is DISCLOSED, though, not silent: the purged world ids are returned
        so ``delete_graph``'s callers (``end_session``'s ``reapedWorlds``,
        the delete-graph command's success message) can tell the caller
        which of their worlds just ceased to exist along with the graph.

        A full ``refs.list(WORLD_KIND)`` scan per deleted graph, so
        ``end_session`` is O(member graphs x world refs) — fine at today's
        scale; revisit with a per-graph index if session workspaces ever
        hold many member graphs against many live worlds.
        """
        purged: list[str] = []
        for record in self.refs.list(self.WORLD_KIND):
            if record.metadata.get("baseGraph") == graph_name:
                self.purge_world(record.id)
                purged.append(record.id)
        return purged

    # -- store construction: the one resolution path for both `graph` and
    # `world` (desire 12 / Part 5) ------------------------------------------

    @property
    def db(self) -> FalkorDB:
        return self._db

    @property
    def redis(self) -> Redis:
        return self._redis

    @property
    def key_prefix(self) -> str:
        return self._prefix

    def plain_store(self, name: str | None = None) -> FalkorGraphStore:
        """A plain, non-world-aware store for ``name`` (default graph if
        omitted) — ignores whatever world is ambient in
        ``theloom.store.worldctx``. The building block ``get_store`` composes
        world-awareness on top of, and what ``theloom.store.worlds.
        resolve_layers`` uses to build each ancestor layer of a fork chain
        regardless of which world the *current* command happens to be
        running in."""
        return FalkorGraphStore(self._db, self._redis, name or self.default_graph, self._prefix)

    def get_store(self, name: str | None = None, world: str | None = None) -> FalkorGraphStore:
        """The one place every command gets a store instance — and so the
        one resolution path both ``graph`` and ``world`` thread through.

        ``world`` explicit here overrides whatever is ambient (used by
        internal callers that need a *specific* world regardless of command
        context); omitted, it falls back to ``theloom.store.worldctx``'s
        contextvar, which ``theloom.cli.registry.run_handler`` opens from
        the command's own validated ``world`` field. Either way, a non-
        ``main`` world returns a ``WorldGraphStore`` — same public surface as
        a plain ``FalkorGraphStore`` (it IS one), so no call site of the
        ~140 that already call this method needs to change to become world-
        aware. ``main`` (the default, and every pre-existing call site)
        returns exactly what this method always returned.
        """
        effective_world = world if world is not None else worldctx.current()
        if effective_world is None or effective_world == worldctx.MAIN:
            return self.plain_store(name)
        record = self.refs.get(worlds_module.WORLD_KIND, effective_world)
        if record is None:
            raise NotFoundError(
                f"World '{effective_world}' not found. Use list-worlds to see active worlds."
            )
        base_graph = str(record.metadata["baseGraph"])
        if name is not None and name != base_graph:
            raise ValidationError(
                f"graph '{name}' does not match world '{effective_world}''s own base graph "
                f"'{base_graph}' — omit graph when addressing a world, or pass the matching one."
            )
        return worlds_module.get_world_store(self, effective_world)

    def chunk_store(self) -> ChunkStore:
        """The global document-chunk store (not graph-scoped)."""
        return ChunkStore(self._db, self._prefix, self._redis)

    def chunk_event_log(self) -> EventLog:
        """The append-only stream of document-chunk writes (not graph-scoped)."""
        return EventLog(self._redis, CHUNK_GRAPH_SUFFIX, self._prefix)

    def bridge_event_log(self) -> EventLog:
        """The append-only stream of cross-graph bridge writes (not graph-scoped)."""
        return EventLog(self._redis, BRIDGE_GRAPH_SUFFIX, self._prefix)

    def run_store(self) -> RunStore:
        """The extraction-run store (event-log-backed)."""
        return RunStore(self._redis, self._prefix)

    def event_log(self, name: str | None = None) -> EventLog:
        """The append-only event stream for one named graph (viz/history reads)."""
        return EventLog(self._redis, name or self.default_graph, self._prefix)

    def wipe(self) -> None:
        """Remove every graph, bridge, session/world ref, and event stream
        under this prefix (reseeding / migration path)."""
        for name in self.graph_names():
            self.plain_store(name).delete_graph_data()
        for record in self.refs.list(self.WORLD_KIND):
            self.plain_store(worlds_module.world_graph_name(record.id)).delete_graph_data()
        self.bridges.delete_all()
        self.chunk_store().wipe()
        self.run_store().wipe()
        self.refs.wipe(self.SESSION_KIND)
        self.refs.wipe(self.WORLD_KIND)
        self.refs.events.delete()
        self._redis.delete(self._registry_key)
        self._redis.sadd(self._registry_key, self.default_graph)

    # -- session workspaces (desire 2) --------------------------------------------
    #
    # A session is a `theloom.store.refs.RefRegistry` ref of kind
    # `SESSION_KIND`: a unique namespace prefix plus a TTL, nothing else. No
    # graph creation path changes for a session to "contain" a graph — a
    # caller creates graphs the ordinary way (create-graph, or a bare `graph`
    # param on any mutating command) and simply names them under the
    # namespace `begin_session` hands back. Two properties compose to make
    # that a complete workspace boundary rather than a naming convention an
    # orchestrator has to police:
    #
    #   1. Every graph a write ever lands on is registered at creation (the
    #      `GraphSpace`/`commit_steps` fix above), so it is guaranteed to
    #      show up in `graph_names()` — nothing can be "inside" a session
    #      invisibly.
    #   2. A session's namespace embeds a random id, so a prefix scan over
    #      `graph_names()` at reap time can never pick up a graph the caller
    #      did not create inside this session.
    #
    # `end_session` therefore needs no separate membership bookkeeping: it
    # reaps exactly what a prefix scan finds, every time, which also means it
    # is self-correcting — a graph created after some `list_sessions()` call
    # is still reaped correctly.

    def begin_session(self, name: str | None, ttl_seconds: int | None) -> dict[str, Any]:
        """Start a session: mint a unique namespace, register it as a ref,
        and return it rendered for the CLI (`_session_doc`)."""
        session_id = f"sess-{uuid.uuid4().hex[:12]}"
        namespace = f"{session_id}-"
        record = self.refs.register(
            self.SESSION_KIND,
            ref_id=session_id,
            name=name,
            ttl_seconds=ttl_seconds,
            metadata={"namespace": namespace},
        )
        return _session_doc(record, [])

    def end_session(self, session_id: str) -> dict[str, Any]:
        """Reap a session: delete every graph currently registered under its
        namespace, then mark the ref reaped. Idempotent — reaping an
        already-reaped session deletes nothing (there is nothing left to
        delete) and the returned doc carries `"alreadyReaped": True` so the
        operations layer can render that truthfully rather than claiming a
        write that did not happen."""
        record = self.refs.get(self.SESSION_KIND, session_id)
        if record is None:
            raise NotFoundError(
                f"Session '{session_id}' not found. Use list_sessions to see active sessions."
            )
        if record.status == "reaped":
            doc = _session_doc(record, [])
            doc["reapedGraphs"] = []
            doc["reapedWorlds"] = []
            doc["alreadyReaped"] = True
            return doc
        namespace = str(record.metadata.get("namespace", ""))
        members = [name for name in self.graph_names() if namespace and name.startswith(namespace)]
        reaped_worlds: list[str] = []
        for graph_name in members:
            reaped_worlds.extend(self.delete_graph(graph_name))
        reaped = self.refs.reap(self.SESSION_KIND, session_id)
        doc = _session_doc(reaped, [])
        doc["reapedGraphs"] = members
        doc["reapedWorlds"] = reaped_worlds
        doc["alreadyReaped"] = False
        return doc

    def list_sessions(self) -> list[dict[str, Any]]:
        """Every session ref, oldest first, each with its current member
        graphs (one `graph_names()` call shared across all of them)."""
        names = self.graph_names()
        return [_session_doc(record, names) for record in self.refs.list(self.SESSION_KIND)]

    # -- branchable belief worlds (desire 12 / Part 5) ----------------------------
    #
    # A world is a `RefRegistry` ref of kind `WORLD_KIND`, exactly as a
    # session is one of kind `SESSION_KIND` — see `theloom.store.worlds` for
    # the fork/overlay/copy-on-write mechanism these thinly wrap.

    def fork_world(
        self,
        *,
        name: str | None,
        graph: str | None,
        from_world: str | None,
        as_of: str | None,
        ttl_seconds: int | None,
    ) -> dict[str, Any]:
        record = worlds_module.fork_world(
            self,
            name=name,
            graph=graph,
            from_world=from_world,
            as_of=as_of,
            ttl_seconds=ttl_seconds,
        )
        return worlds_module.world_doc(record)

    def list_worlds(self, *, include_reaped: bool = True) -> list[dict[str, Any]]:
        return worlds_module.list_worlds(self, include_reaped=include_reaped)

    def abandon_world(self, world_id: str) -> dict[str, Any]:
        return worlds_module.abandon_world(self, world_id)

    def purge_world(self, world_id: str) -> None:
        worlds_module.purge_world(self, world_id)

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
