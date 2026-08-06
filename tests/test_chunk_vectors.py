"""Chunk vectors are searchable.

``upsert_chunk`` has always written ``_embedding`` on the chunk row, but the
chunk vector index was never created by anything: ``ensure_vector_index`` had
zero callers, so a k-NN over chunks had no index to run against and every
embedding written by ingestion was unsearchable. These tests pin the k-NN path
end to end against the live store.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from falkordb import FalkorDB

from tests.fakes import FakeEmbedder
from theloom import config as config_module
from theloom.documents.chunkstore import ChunkStore
from theloom.documents.ingestion import DocumentIngestion


@pytest.fixture()
def chunks(db: FalkorDB, namespace: str) -> ChunkStore:
    return ChunkStore(db, key_prefix=namespace)


@pytest.fixture()
def embedder() -> Iterator[FakeEmbedder]:
    """Two orthogonal directions, chosen by the text's first word."""
    fake = FakeEmbedder({"alpha": [1.0, 0.0, 0.0], "beta": [0.0, 1.0, 0.0]})
    config_module.set_embedder_override(fake)
    yield fake
    config_module.set_embedder_override(None)


def metadata(chunk_id: str, index: int = 0) -> dict[str, Any]:
    return {
        "id": chunk_id,
        "entityId": chunk_id,
        "entryType": "document_chunk",
        "sourceId": "doc-1",
        "sourceName": "Doc One",
        "chunkIndex": index,
        "contentHash": f"hash-{index}",
        "content": f"chunk {index}",
    }


def test_chunk_vector_search_finds_the_nearest_chunk(chunks: ChunkStore) -> None:
    """Two orthogonal unit vectors: the query equals the first one exactly, so
    cosine similarity is 1.0 against it and 0.0 against the other."""
    chunks.upsert_chunk("chunk-a", metadata("chunk-a", 0), [1.0, 0.0, 0.0])
    chunks.upsert_chunk("chunk-b", metadata("chunk-b", 1), [0.0, 1.0, 0.0])

    hits = chunks.vector_knn([1.0, 0.0, 0.0], 2)

    assert [chunk_id for chunk_id, _ in hits] == ["chunk-a", "chunk-b"]
    assert hits[0][1] == pytest.approx(1.0, abs=1e-6)
    assert hits[1][1] == pytest.approx(0.0, abs=1e-6)


def test_ingested_chunks_are_searchable_by_vector(
    chunks: ChunkStore, embedder: FakeEmbedder
) -> None:
    """The whole point of storing chunk embeddings: ingest two documents, then
    ask for the nearest chunk to one of them. Nothing ever created the chunk
    vector index, so this used to be unanswerable."""
    ingestion = DocumentIngestion(chunks)
    ingestion.ingest_content("src-alpha", "alpha content about alpha things", "txt")
    ingestion.ingest_content("src-beta", "beta content about beta things", "txt")

    alpha_ids = {doc["id"] for doc in chunks.query_chunks(source_id="src-alpha")}
    hits = chunks.vector_knn(embedder.embed_query("alpha query"), 1)

    assert len(hits) == 1
    assert hits[0][0] in alpha_ids
    assert hits[0][1] == pytest.approx(1.0, abs=1e-6)
