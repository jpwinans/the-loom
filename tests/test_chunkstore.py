"""ChunkStore read coverage — chunk queries must not be truncated by the
server's RESULTSET_SIZE cap and must honour their own limit parameter.
"""

from __future__ import annotations

import pytest
from falkordb import FalkorDB

from theloom.documents.chunkstore import ChunkStore


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
