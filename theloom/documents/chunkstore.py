"""Chunk storage in FalkorDB: chunks live in the transactional store, not a
separate vector store, so there is a single source of truth.

Chunks are ``:_Chunk`` nodes in a dedicated per-prefix graph (``{prefix}:_chunks``),
NOT inside any knowledge graph: documents are global across graphs, so
ingest/list/delete take no graph param. Each node carries the verbatim metadata
doc in ``_doc`` and an optional ``_embedding`` (vecf32) so analyze-category can
cluster. Row order follows ``id(n)`` (insertion order).
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from falkordb import FalkorDB

from theloom.store.paging import fetch_all_rows

Doc = dict[str, Any]
CHUNK_GRAPH_SUFFIX = "_chunks"


class ChunkStore:
    def __init__(self, db: FalkorDB, key_prefix: str = "loom") -> None:
        self._graph = db.select_graph(f"{key_prefix}:graph:{CHUNK_GRAPH_SUFFIX}")

    def _query(self, cypher: str, params: Doc | None = None) -> Any:
        return self._graph.query(cypher, params or {})

    def _rows(self, cypher: str, params: Doc | None = None) -> list[list[Any]]:
        result = self._query(cypher, params)
        return result.result_set or []

    def ensure_vector_index(self, dimension: int = 768) -> None:
        with contextlib.suppress(Exception):
            self._query(
                "CREATE VECTOR INDEX FOR (c:_Chunk) ON (c._embedding) "
                f"OPTIONS {{dimension: {dimension}, similarityFunction: 'cosine'}}"
            )

    def upsert_chunk(self, chunk_id: str, metadata: Doc, vector: list[float] | None) -> None:
        """Insert-or-replace a chunk row keyed by chunk id."""
        if vector is None:
            self._query(
                "MERGE (c:_Chunk {id: $id}) SET c._doc = $doc, c.sourceId = $sid",
                {"id": chunk_id, "doc": json.dumps(metadata), "sid": metadata.get("sourceId", "")},
            )
        else:
            self._query(
                "MERGE (c:_Chunk {id: $id}) "
                "SET c._doc = $doc, c.sourceId = $sid, c._embedding = vecf32($vec)",
                {
                    "id": chunk_id,
                    "doc": json.dumps(metadata),
                    "sid": metadata.get("sourceId", ""),
                    "vec": vector,
                },
            )

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
        rows = self._rows("MATCH (c:_Chunk {sourceId: $sid}) RETURN count(c)", {"sid": source_id})
        count = int(rows[0][0]) if rows else 0
        if count:
            self._query("MATCH (c:_Chunk {sourceId: $sid}) DELETE c", {"sid": source_id})
        return count

    def delete_chunk(self, chunk_id: str) -> None:
        self._query("MATCH (c:_Chunk {id: $id}) DELETE c", {"id": chunk_id})

    def wipe(self) -> None:
        with contextlib.suppress(Exception):
            self._query("MATCH (c:_Chunk) DELETE c")
