"""Chunk storage in FalkorDB: chunks live in the transactional store, not a
separate vector store, so there is a single source of truth.

Chunks are ``:_Chunk`` nodes in a dedicated per-prefix graph (``{prefix}:_chunks``),
NOT inside any knowledge graph: documents are global across graphs, so
ingest/list/delete take no graph param. Each node carries the verbatim metadata
doc in ``_doc`` and an optional ``_embedding`` (vecf32) so analyze-category can
cluster and ``vector_knn`` can search. Row order follows ``id(n)`` (insertion
order).

This is a store over one FalkorDB graph like any other, so it *is* one:
``ChunkStore`` extends :class:`theloom.store.space.GraphSpace` and owns no
graph handle, no Redis connection, no commit primitive and no vector-index
Cypher of its own. Chunk writes therefore get exactly the store's guarantees
rather than a second, subtly different copy of them — the chunk vector index in
particular used to be created by a hand-rolled statement that swallowed every
error and never waited for the index to become operational, and (because
nothing ever called it) was never created at all, leaving every chunk embedding
unsearchable.

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
from typing import TYPE_CHECKING, Any

from theloom.documents.metadata import ChunkMetadata

# The store's paging, called by name rather than through ``_rows_paged``: a
# document delete must snapshot the ids it will remove, and the tests that pin
# what happens when another writer moves in *during* that snapshot substitute
# this function to open the window. Same machinery either way.
from theloom.store.paging import fetch_all_rows
from theloom.store.space import GraphSpace

if TYPE_CHECKING:
    from falkordb import FalkorDB
    from redis import Redis

Doc = dict[str, Any]
CHUNK_GRAPH_SUFFIX = "_chunks"

# Ceiling on the ids gathered for a document-wide delete event. Documents are
# chunked, not unbounded, and the paged read below keeps the server's
# RESULTSET_SIZE cap from silently shortening the list.
_MAX_CHUNKS_PER_DOCUMENT = 100_000


class ChunkStore(GraphSpace):
    """The document-chunk rows, over the shared store machinery."""

    _VECTOR_LABEL = "_Chunk"

    def __init__(self, db: FalkorDB, key_prefix: str = "loom", redis: Redis | None = None) -> None:
        # FalkorDB *is* the Redis server, so the chunk write and its event
        # append are two commands on one connection; default to the client the
        # graph handle already speaks through.
        super().__init__(
            db,
            redis if redis is not None else db.connection,
            CHUNK_GRAPH_SUFFIX,
            key_prefix,
        )

    def upsert_chunk(
        self, chunk_id: str, metadata: ChunkMetadata | Doc, vector: list[float] | None
    ) -> None:
        """Insert-or-replace a chunk row keyed by chunk id, and log it.

        ``metadata`` is a :class:`ChunkMetadata` — or a raw wire doc, which is
        validated into one, so what lands in ``_doc`` always has the declared
        shape whichever way it arrived."""
        chunk = ChunkMetadata.coerce(metadata)
        params: Doc = {
            "id": chunk_id,
            "doc": json.dumps(chunk.to_doc()),
            "sid": chunk.source_id or "",
        }
        if vector is None:
            cypher = "MERGE (c:_Chunk {id: $id}) SET c._doc = $doc, c.sourceId = $sid"
        else:
            cypher = (
                "MERGE (c:_Chunk {id: $id}) "
                "SET c._doc = $doc, c.sourceId = $sid, c._embedding = vecf32($vec)"
            )
            params["vec"] = vector
        event = _chunk_event(chunk_id, chunk, vector)
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
        chunks = (ChunkMetadata.from_json(row[0]) for row in rows)
        return [
            chunk.to_doc() for chunk in chunks if category is None or chunk.category == category
        ]

    def get_chunk(self, chunk_id: str) -> Doc | None:
        """One chunk's metadata doc by id, or ``None`` if it is not stored.

        The point lookup behind a *pointer* to a chunk — an entity's
        ``provenance.externalRef`` names the chunk it was extracted from, and
        synthesis resolves it here to quote the originating passage. A chunk
        deleted after extraction simply reads back as ``None``.
        """
        rows = self._rows("MATCH (c:_Chunk {id: $id}) RETURN c._doc LIMIT 1", {"id": chunk_id})
        if not rows:
            return None
        return ChunkMetadata.from_json(rows[0][0]).to_doc()

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
            chunk = ChunkMetadata.from_json(row[0])
            if category is not None and chunk.category != category:
                continue
            vector = [float(x) for x in row[1]] if row[1] is not None else None
            result.append((chunk.to_doc(), vector))
        return result

    def vector_knn(self, query_vector: list[float], k: int) -> list[tuple[str, float]]:
        """(chunk id, cosine similarity) for the k nearest embedded chunks.

        The chunk vector index is created on first search at the width of the
        vectors already stored, through the store's barrier-guarded path — see
        ``GraphSpace._vector_knn``. Nothing embedded means nothing to search
        and no index to guess at, so the answer is empty.
        """
        return self._vector_knn(query_vector, k)

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
        results, _ = self._commit(
            ("MATCH (c:_Chunk) WHERE c.id IN $ids DELETE c", {"ids": chunk_ids}),
            [
                (
                    "chunks_deleted",
                    {"sourceId": source_id, "chunkIds": chunk_ids, "count": len(chunk_ids)},
                )
            ],
        )
        return int(results[0].nodes_deleted)

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


def _chunk_event(chunk_id: str, chunk: ChunkMetadata, vector: list[float] | None) -> Doc:
    """The ``chunk_created`` payload: who the chunk is and where it came from.

    Deliberately not the chunk text — the log records that the write happened
    and against which document, and the chunk row holds the content.
    """
    payload: Doc = {
        "chunkId": chunk_id,
        "sourceId": chunk.source_id or "",
        "embedded": vector is not None,
    }
    coordinates = {
        "sourceName": chunk.source_name,
        "sourceFormat": chunk.source_format,
        "chunkIndex": chunk.chunk_index,
        "contentHash": chunk.content_hash,
        "category": chunk.category,
    }
    payload.update({name: value for name, value in coordinates.items() if value is not None})
    return payload
