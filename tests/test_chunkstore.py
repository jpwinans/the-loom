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
