"""Chunk storage in FalkorDB: chunks live in the transactional store, not a
separate vector store, so there is a single source of truth.

Chunks are ``:_Chunk`` nodes in a dedicated per-prefix graph (``{prefix}:_chunks``),
NOT inside any knowledge graph: documents are global across graphs, so
ingest/list/delete take no graph param. Each node carries the verbatim metadata
doc in ``_doc`` and an optional ``_embedding`` (vecf32) so analyze-category can
cluster. Row order follows ``id(n)`` (insertion order).

Chunk writes are event-sourced like every other mutation: each one is a single
Cypher statement plus its event append, committed as one MULTI/EXEC unit
through ``theloom.store.commit`` (see that module for the exact guarantee and
the compensation in each direction). The events land in their own stream —
``{prefix}:_chunks:events``, keyed by the same reserved name as the chunk graph
— because chunks are not graph-scoped, so document history replays independently
of any knowledge graph's log. Events: ``chunk_created`` per upserted chunk,
``chunk_deleted`` per removed chunk, ``chunks_deleted`` for a document (naming
the exact chunk ids the delete was pinned to — see ``delete_where_source``).
Payloads carry ids (chunk, source) and the ingestion coordinates, not the chunk
text — the chunk row is the store of record for content.

Reads never touch the log.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from falkordb import FalkorDB
from redis import Redis

from theloom.store.commit import commit_steps
from theloom.store.events import EventLog
from theloom.store.paging import fetch_all_rows

Doc = dict[str, Any]
CHUNK_GRAPH_SUFFIX = "_chunks"

# Ceiling on the ids gathered for a document-wide delete event. Documents are
# chunked, not unbounded, and the paged read below keeps the server's
# RESULTSET_SIZE cap from silently shortening the list.
_MAX_CHUNKS_PER_DOCUMENT = 100_000


class ChunkStore:
    def __init__(self, db: FalkorDB, key_prefix: str = "loom", redis: Redis | None = None) -> None:
        self._graph = db.select_graph(f"{key_prefix}:graph:{CHUNK_GRAPH_SUFFIX}")
        # FalkorDB *is* the Redis server, so the chunk write and its event
        # append are two commands on one connection; default to the client the
        # graph handle already speaks through.
        self._redis: Redis = redis if redis is not None else db.connection
        self.events = EventLog(self._redis, CHUNK_GRAPH_SUFFIX, key_prefix)

    def _query(self, cypher: str, params: Doc | None = None) -> Any:
        return self._graph.query(cypher, params or {})

    def _rows(self, cypher: str, params: Doc | None = None) -> list[list[Any]]:
        result = self._query(cypher, params)
        return result.result_set or []

    def _commit(
        self,
        step: tuple[str, dict[str, Any]],
        events: list[tuple[str, dict[str, Any]]],
    ) -> Any:
        """One Cypher statement plus its events, as one transaction; the
        statement's result (so a caller can report what actually changed)."""
        results, _ = commit_steps(self._redis, self._graph, self.events, [step], events)
        return results[0]

    def ensure_vector_index(self, dimension: int = 768) -> None:
        with contextlib.suppress(Exception):
            self._query(
                "CREATE VECTOR INDEX FOR (c:_Chunk) ON (c._embedding) "
                f"OPTIONS {{dimension: {dimension}, similarityFunction: 'cosine'}}"
            )

    def upsert_chunk(self, chunk_id: str, metadata: Doc, vector: list[float] | None) -> None:
        """Insert-or-replace a chunk row keyed by chunk id, and log it."""
        params: Doc = {
            "id": chunk_id,
            "doc": json.dumps(metadata),
            "sid": metadata.get("sourceId", ""),
        }
        if vector is None:
            cypher = "MERGE (c:_Chunk {id: $id}) SET c._doc = $doc, c.sourceId = $sid"
        else:
            cypher = (
                "MERGE (c:_Chunk {id: $id}) "
                "SET c._doc = $doc, c.sourceId = $sid, c._embedding = vecf32($vec)"
            )
            params["vec"] = vector
        event = _chunk_event(chunk_id, metadata, vector)
        self._commit((cypher, params), [("chunk_created", event)])

    def query_chunks(
        self, *, category: str | None = None, source_id: str | None = None, limit: int = 1000
    ) -> list[Doc]:
        """Chunk metadata docs (entryType document_chunk is implicit — every
        row is a chunk), scan order = id(n) (insertion order)."""
        clauses = []
        params: Doc = {}
        if source_id is not None:
            clauses.append("c.sourceId = $sid")
            params["sid"] = source_id
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        rows = fetch_all_rows(
            self._rows,
            f"MATCH (c:_Chunk) {where}RETURN c._doc ORDER BY id(c)",
            params,
            limit=limit,
        )
        docs = [json.loads(row[0]) for row in rows]
        if category is not None:
            docs = [d for d in docs if d.get("category") == category]
        return docs

    def query_chunks_with_vectors(
        self, *, category: str | None = None, limit: int = 100000
    ) -> list[tuple[Doc, list[float] | None]]:
        rows = fetch_all_rows(
            self._rows,
            "MATCH (c:_Chunk) RETURN c._doc, c._embedding ORDER BY id(c)",
            limit=limit,
        )
        result: list[tuple[Doc, list[float] | None]] = []
        for row in rows:
            meta = json.loads(row[0])
            if category is not None and meta.get("category") != category:
                continue
            vector = [float(x) for x in row[1]] if row[1] is not None else None
            result.append((meta, vector))
        return result

    def delete_where_source(self, source_id: str) -> int:
        """Delete the document's chunks named by one snapshot; how many went.

        The ids are read first so the event can name them, and the delete is
        then *pinned to that snapshot* rather than re-matching on ``sourceId``:
        the statement removes exactly the chunks the event names, so a chunk
        written against the same document between the read and the commit is
        neither deleted nor claimed, and replaying the log reproduces what the
        live store did. (Re-matching would delete such a chunk for real while
        the event stayed silent about it.) The pinned form is idempotent under
        replay, so a racing deleter that takes a chunk first costs nothing —
        the count returned is what this call actually removed, read back from
        the delete itself rather than from the hopeful snapshot.
        """
        rows = fetch_all_rows(
            self._rows,
            "MATCH (c:_Chunk {sourceId: $sid}) RETURN c.id ORDER BY id(c)",
            {"sid": source_id},
            limit=_MAX_CHUNKS_PER_DOCUMENT,
        )
        chunk_ids = [str(row[0]) for row in rows]
        if not chunk_ids:
            return 0
        result = self._commit(
            ("MATCH (c:_Chunk) WHERE c.id IN $ids DELETE c", {"ids": chunk_ids}),
            [
                (
                    "chunks_deleted",
                    {"sourceId": source_id, "chunkIds": chunk_ids, "count": len(chunk_ids)},
                )
            ],
        )
        return int(result.nodes_deleted)

    def delete_chunk(self, chunk_id: str) -> None:
        self._commit(
            ("MATCH (c:_Chunk {id: $id}) DELETE c", {"id": chunk_id}),
            [("chunk_deleted", {"chunkId": chunk_id})],
        )

    def wipe(self) -> None:
        """Drop every chunk and its history (reseeding / migration path)."""
        with contextlib.suppress(Exception):
            self._query("MATCH (c:_Chunk) DELETE c")
        with contextlib.suppress(Exception):
            self.events.delete()


def _chunk_event(chunk_id: str, metadata: Doc, vector: list[float] | None) -> Doc:
    """The ``chunk_created`` payload: who the chunk is and where it came from.

    Deliberately not the chunk text — the log records that the write happened
    and against which document, and the chunk row holds the content.
    """
    payload: Doc = {
        "chunkId": chunk_id,
        "sourceId": metadata.get("sourceId", ""),
        "embedded": vector is not None,
    }
    for field in ("sourceName", "sourceFormat", "chunkIndex", "contentHash", "category"):
        if metadata.get(field) is not None:
            payload[field] = metadata[field]
    return payload
