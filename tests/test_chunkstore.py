"""ChunkStore read coverage — chunk queries must not be truncated by the
server's RESULTSET_SIZE cap and must honour their own limit parameter.
"""

from __future__ import annotations

import pytest
from falkordb import FalkorDB

from theloom.documents.chunkstore import ChunkStore
from theloom.documents.metadata import ChunkMetadata


@pytest.fixture()
def chunks(db: FalkorDB, namespace: str) -> ChunkStore:
    return ChunkStore(db, key_prefix=namespace)


def _seed(store: ChunkStore, count: int) -> None:
    for i in range(count):
        store.upsert_chunk(
            f"chunk-{i:03d}",
            {"sourceId": "doc-1", "category": "test", "text": f"chunk {i}"},
            [1.0, 0.0, 0.0] if i % 2 == 0 else None,
        )


def test_query_chunks_survive_server_resultset_cap(
    chunks: ChunkStore, small_resultset_cap: int
) -> None:
    total = small_resultset_cap + 20
    _seed(chunks, total)
    assert len(chunks.query_chunks(limit=1000)) == total
    assert len(chunks.query_chunks_with_vectors()) == total


def test_query_chunks_honours_explicit_limit(chunks: ChunkStore, small_resultset_cap: int) -> None:
    total = small_resultset_cap + 20
    _seed(chunks, total)
    docs = chunks.query_chunks(limit=10)
    assert len(docs) == 10
    assert [d["text"] for d in docs] == [f"chunk {i}" for i in range(10)]


# =============================================================================
# The stored metadata doc has a declared shape
# =============================================================================


def test_a_chunk_stored_from_the_declared_model_carries_the_chunk_conventions(
    chunks: ChunkStore,
) -> None:
    """``ChunkMetadata`` is the schema of the ``_doc`` every chunk row carries,
    so the conventions ingestion used to repeat by hand — the chunk's own id as
    its entity id, the document's name as the chunk's name, entity/entry type
    ``document_chunk`` — come from the declaration and land on the wire under
    the camelCase names readers already expect."""
    chunks.upsert_chunk(
        "chunk-0",
        ChunkMetadata(
            id="chunk-0",
            source_id="doc-1",
            source_name="Doc One",
            source_format="markdown",
            chunk_index=0,
            total_chunks=1,
            content="hello",
            content_hash="hash-0",
            embedded_at="2026-01-01T00:00:00.000Z",
        ),
        None,
    )

    assert chunks.query_chunks(source_id="doc-1") == [
        {
            "id": "chunk-0",
            "entityId": "chunk-0",
            "entityType": "document_chunk",
            "entryType": "document_chunk",
            "name": "Doc One",
            "contentHash": "hash-0",
            "embeddedAt": "2026-01-01T00:00:00.000Z",
            "sourceId": "doc-1",
            "sourceName": "Doc One",
            "sourceFormat": "markdown",
            "chunkIndex": 0,
            "totalChunks": 1,
            "content": "hello",
        }
    ]


# =============================================================================
# Point lookup by chunk id
# =============================================================================


def test_get_chunk_returns_the_stored_doc(chunks: ChunkStore) -> None:
    """An entity's ``provenance.externalRef`` names the chunk it came from, so
    synthesis needs to resolve one chunk by id — same doc ``query_chunks``
    returns, no scan of the whole document."""
    _seed(chunks, 3)

    assert chunks.get_chunk("chunk-001") == chunks.query_chunks(limit=10)[1]


def test_get_chunk_is_none_for_an_unknown_id(chunks: ChunkStore) -> None:
    """A chunk deleted after extraction degrades to nothing, not an error."""
    _seed(chunks, 1)
    chunks.delete_chunk("chunk-000")

    assert chunks.get_chunk("chunk-000") is None
    assert chunks.get_chunk("never-existed") is None


# =============================================================================
# The id index: get_chunk must not be a label+property scan
# =============================================================================


def test_get_chunk_ensures_an_id_index(chunks: ChunkStore) -> None:
    """``get_chunk`` used to run ``MATCH (c:_Chunk {id: $id})`` with nothing
    indexing ``id`` — a label+property scan on every lookup. The point lookup
    must leave a RANGE index on ``:_Chunk(id)`` behind, the same way a k-NN
    search leaves the chunk vector index behind (see ``vector_knn``)."""
    _seed(chunks, 1)
    assert not chunks.range_index_exists("_Chunk", "id")

    chunks.get_chunk("chunk-000")

    assert chunks.range_index_exists("_Chunk", "id")


def test_ensure_id_index_is_idempotent(chunks: ChunkStore) -> None:
    """Calling twice must not raise — FalkorDB rejects a second CREATE INDEX
    on an already-indexed property, so the second call has to recognize the
    index is already there and treat that as success."""
    chunks.ensure_id_index()
    chunks.ensure_id_index()

    assert chunks.range_index_exists("_Chunk", "id")
