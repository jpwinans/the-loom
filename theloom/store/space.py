"""One FalkorDB graph plus its event stream: the machinery every store shares.

A Loom store — the knowledge graph, the document-chunk store — is a named
FalkorDB graph, the Redis connection that graph speaks through, and one
append-only event stream. Everything that follows from that trio is the same
whatever the rows mean: how a mutation is committed (one Cypher statement plus
its event append, as one MULTI/EXEC unit — see :mod:`theloom.store.commit`),
how a full-scan read survives the server's RESULTSET_SIZE cap (see
:mod:`theloom.store.paging`), and how a vector index is created and waited on.

This class owns that machinery once. Subclasses add the rows they know about
and inherit the rest — in particular the vector index, whose subtleties (never
guessing a width from the query vector; blocking until the index reports
OPERATIONAL) were previously written correctly in one store and dangerously in
the other. A subclass that stores vectors names its node label in
``_VECTOR_LABEL``; the property is ``_embedding`` everywhere.

The helpers are underscore-named because they are the *inside* of a store, not
its interface: a subclass calls them, callers of the subclass do not.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from falkordb import FalkorDB
from redis import Redis

from theloom.errors import OperationError
from theloom.store.commit import commit_steps
from theloom.store.events import EventLog
from theloom.store.paging import fetch_all_rows

VECTOR_PROPERTY = "_embedding"


class GraphSpace:
    """One named FalkorDB graph, its connection, and its event log."""

    #: Node label whose ``_embedding`` this store's vector index covers.
    _VECTOR_LABEL = ""

    def __init__(
        self, db: FalkorDB, redis: Redis, graph_name: str, key_prefix: str = "loom"
    ) -> None:
        self._graph = db.select_graph(f"{key_prefix}:graph:{graph_name}")
        self._redis = redis
        self._events = EventLog(redis, graph_name, key_prefix)
        # Every write through `_commit`/`_commit_steps` also SADDs this
        # graph's name into the multigraph registry set (`{prefix}:graphs`),
        # in the same MULTI/EXEC as the mutation (see `commit_steps`'s
        # `register` parameter) — the fix for the registry gap where a graph
        # created implicitly via a bare `graph` param on any mutating command
        # was invisible to `list-graphs`/`delete-graph` and needed redis-cli
        # to clean up. Reserved graphs (leading underscore — `_chunks`,
        # `_bridges`, `_refs`) are excluded: they are not user-addressable
        # graphs and `MultiGraph.create_graph` already refuses that name
        # shape for explicit creation, so auto-registration must honour the
        # same rule rather than leaking internal graphs into `list-graphs`.
        self._register: tuple[str, str] | None = (
            None if graph_name.startswith("_") else (f"{key_prefix}:graphs", graph_name)
        )

    @property
    def events(self) -> EventLog:
        """The append-only stream this store's mutations are logged to."""
        return self._events

    # -- the mutation primitive ---------------------------------------------------

    def _commit(
        self,
        step: tuple[str, dict[str, Any]],
        events: Sequence[tuple[str, dict[str, Any]]],
    ) -> tuple[list[Any], list[str]]:
        """Run ONE Cypher statement and append its events as one unit.

        The signature is the guarantee: a mutation is a single ``GRAPH.QUERY``,
        because that is the only unit FalkorDB rolls back. Anything that needs
        several effects (snapshot + swap + close out attached edges) expresses
        them as clauses of one statement, not as several statements — Redis
        MULTI is *not* a rollback boundary, so a second statement failing would
        leave the first one applied. See ``_commit_steps`` for the one place
        that genuinely needs several statements, and what it owes in return.

        The transaction mechanism and its exact failure semantics — including
        which half is compensated in which direction — live in
        ``theloom.store.commit``.

        Returns ``(query results, appended event ids)``; the ids let a caller
        that only learns the mutation was wrong *after* ``EXEC`` (see
        ``create_relations``) discard the events it no longer earns.
        """
        return self._commit_steps([step], events)

    def _commit_steps(
        self,
        steps: Sequence[tuple[str, dict[str, Any]]],
        events: Sequence[tuple[str, dict[str, Any]]],
    ) -> tuple[list[Any], list[str]]:
        """``_commit`` for the batch case: several Cypher statements in one
        MULTI/EXEC.

        Weaker than ``_commit`` by exactly one thing, and the caller owes the
        difference: MULTI is not a rollback boundary, so if statement *k*
        fails, statements before it have already applied. Only
        ``create_relations`` uses this (edge types cannot be parametrized, so a
        mixed-type batch is one statement per type), and it pays the debt by
        checking every endpoint before committing and by deleting the edges it
        did create if the reply still disagrees.
        """
        results, event_ids = commit_steps(
            self._redis, self._graph, self._events, steps, events, register=self._register
        )
        return list(results), event_ids

    # -- query helpers ----------------------------------------------------------

    def _query(self, cypher: str, params: dict[str, Any] | None = None) -> Any:
        return self._graph.query(cypher, params or {})

    def _rows(self, cypher: str, params: dict[str, Any] | None = None) -> list[list[Any]]:
        result = self._query(cypher, params)
        rows: list[list[Any]] = result.result_set or []
        return rows

    def _rows_paged(
        self, cypher: str, params: dict[str, Any] | None = None, limit: int | None = None
    ) -> list[list[Any]]:
        """All rows of an ORDER BY-carrying query, immune to RESULTSET_SIZE.
        ``limit`` caps the window server-side (the paging loop stops there)."""
        return fetch_all_rows(self._rows, cypher, params, limit)

    # -- the vector index -------------------------------------------------------

    def vector_index_dimension(self) -> int | None:
        """The width of this store's vector index, or ``None`` if the graph has
        no such index yet. The index is write-once — FalkorDB rejects a second
        CREATE on the same property — so this is also the authority on whether
        a vector already stored can ever be searched."""
        rows = self._rows(
            "CALL db.indexes() YIELD label, types, options RETURN label, types, options"
        )
        for label, types, options in rows:
            if label != self._VECTOR_LABEL:
                continue
            if "VECTOR" not in (dict(types or {}).get(VECTOR_PROPERTY) or []):
                continue
            dimension = dict(dict(options or {}).get(VECTOR_PROPERTY) or {}).get("dimension")
            return int(dimension) if dimension is not None else None
        return None

    def ensure_vector_index(self, dimension: int = 768) -> None:
        """Create the vector index at ``dimension`` if the graph has none.

        Idempotent, but not blind: an existing index keeps whatever width it was
        created with (a re-CREATE is an error, not a reshape), and any other
        failure is surfaced rather than swallowed. Swallowing it is what let a
        wrong-width index sit there silently indexing nothing.
        """
        if self.vector_index_dimension() is not None:
            return
        try:
            self._query(
                f"CREATE VECTOR INDEX FOR (n:{self._VECTOR_LABEL}) ON (n.{VECTOR_PROPERTY}) "
                f"OPTIONS {{dimension: {dimension}, similarityFunction: 'cosine'}}"
            )
        except Exception:
            # Lost a race with a concurrent create: the index exists, which is
            # all this method promised. Anything else is a real failure.
            if self.vector_index_dimension() is None:
                raise
        self._wait_vector_index_operational()

    def _wait_vector_index_operational(self, timeout: float = 30.0) -> None:
        """Block until this store's vector index reports OPERATIONAL.

        CREATE VECTOR INDEX returns while FalkorDB populates the index in the
        background, and queryNodes against an index still under construction
        can be rejected outright — measured on the linux/amd64 build: k=1 fails
        with "Invalid arguments for procedure 'db.idx.vector.queryNodes'" until
        construction finishes (k>=2 happens to work, and Apple-silicon builds
        construct too fast to observe it). A create followed by a query is only
        correct with this barrier in between.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rows = self._rows(
                "CALL db.indexes() YIELD label, types, status RETURN label, types, status"
            )
            for label, types, status in rows:
                if label != self._VECTOR_LABEL:
                    continue
                if "VECTOR" not in (dict(types or {}).get(VECTOR_PROPERTY) or []):
                    continue
                if status == "OPERATIONAL":
                    return
            time.sleep(0.05)
        raise OperationError(
            f"{self._VECTOR_LABEL} vector index did not become operational within {timeout}s"
        )

    # -- exact-match / range indexes ---------------------------------------------

    def range_index_exists(self, label: str, property: str) -> bool:
        """Whether a RANGE (exact-match) index already covers ``label.property``."""
        rows = self._rows("CALL db.indexes() YIELD label, types RETURN label, types")
        for row_label, types in rows:
            if row_label != label:
                continue
            if "RANGE" in (dict(types or {}).get(property) or []):
                return True
        return False

    def ensure_range_index(self, label: str, property: str) -> None:
        """Create an exact-match/range index on ``label.property`` if none exists.

        Idempotent the same way ``ensure_vector_index`` is: FalkorDB rejects a
        second ``CREATE INDEX`` on an already-indexed property, so this checks
        first and, if it still loses a race to a concurrent create, treats the
        index now existing as success rather than propagating the error — any
        other failure surfaces. Unlike the vector index, a RANGE index is
        queryable the instant ``CREATE`` returns, so there is no OPERATIONAL
        barrier to wait behind here.
        """
        if self.range_index_exists(label, property):
            return
        try:
            self._query(f"CREATE INDEX FOR (n:{label}) ON (n.{property})")
        except Exception:
            if not self.range_index_exists(label, property):
                raise

    def _stored_vector_dimension(self) -> int | None:
        """The width of the vectors actually stored, or ``None`` if none are."""
        rows = self._rows(
            f"MATCH (n:{self._VECTOR_LABEL}) WHERE n.{VECTOR_PROPERTY} IS NOT NULL "
            f"RETURN n.{VECTOR_PROPERTY} LIMIT 1"
        )
        return len(rows[0][0]) if rows else None

    def _vector_knn(self, query_vector: list[float], k: int) -> list[tuple[str, float]]:
        """(node id, cosine similarity) for the k nearest embedded nodes.
        FalkorDB returns cosine *distance*; similarity = 1 - distance.

        If the graph has no vector index yet, one is created at the width of
        the vectors already *stored* — never at the query vector's width, which
        would let a single oddly-shaped query permanently fix the schema and
        leave every real embedding unindexed. With nothing embedded there is
        nothing to search and no index to guess at, so the answer is empty.
        """
        if self.vector_index_dimension() is None:
            stored = self._stored_vector_dimension()
            if stored is None:
                return []
            self.ensure_vector_index(dimension=stored)
        query = (
            f"CALL db.idx.vector.queryNodes('{self._VECTOR_LABEL}', '{VECTOR_PROPERTY}', "
            "$k, vecf32($q)) YIELD node, score RETURN node.id, score"
        )
        try:
            rows = self._rows(query, {"k": k, "q": query_vector})
        except Exception as exc:
            # An index created by another process can still be under
            # construction here, and queryNodes against it is rejected with
            # exactly this message (ensure_vector_index barriers only our own
            # creates). Wait for construction once, retry once; anything else,
            # or a second failure, is a real error and surfaces.
            if "db.idx.vector.queryNodes" not in str(exc):
                raise
            self._wait_vector_index_operational()
            rows = self._rows(query, {"k": k, "q": query_vector})
        return [(row[0], 1.0 - float(row[1])) for row in rows]
