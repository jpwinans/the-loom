"""Reingest diffs a document against what is already stored.

``reingest`` re-reads the source, re-chunks it and compares each chunk against
the stored chunk at the same ``chunkIndex`` by ``contentHash``: same hash means
untouched (not re-embedded, not rewritten), different hash means updated *in
place* under the stored chunk id, an index with no stored counterpart is new,
and a stored index the new content no longer reaches is deleted. Every count in
the result names one of those four cases.

The chunk options pin the chunking so a fixture paragraph is exactly one chunk:
a small target size groups one paragraph per chunk, no overlap keeps an edit
local to its own chunk's hash, and no minimum stops small paragraphs merging.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from falkordb import FalkorDB

from tests.fakes import FakeEmbedder
from theloom import config as config_module
from theloom.documents.chunkstore import ChunkStore
from theloom.documents.ingestion import DocumentIngestion

CHUNK_OPTIONS: dict[str, Any] = {
    "chunkOptions": {"targetSize": 30, "minSize": 1, "overlapSentences": 0}
}

PARAGRAPHS = [
    "Alpha paragraph one.",
    "Bravo paragraph two.",
    "Charlie paragraph three.",
]


@pytest.fixture()
def chunks(db: FalkorDB, namespace: str) -> ChunkStore:
    return ChunkStore(db, key_prefix=namespace)


@pytest.fixture()
def ingestion(chunks: ChunkStore) -> DocumentIngestion:
    return DocumentIngestion(chunks)


@pytest.fixture(autouse=True)
def embedder() -> Iterator[FakeEmbedder]:
    fake = FakeEmbedder([1.0, 0.0, 0.0])
    config_module.set_embedder_override(fake)
    yield fake
    config_module.set_embedder_override(None)


@pytest.fixture()
def document(tmp_path: Path) -> Path:
    path = tmp_path / "notes.md"
    path.write_text("\n\n".join(PARAGRAPHS))
    return path


def test_ingest_makes_one_chunk_per_paragraph(ingestion: DocumentIngestion, document: Path) -> None:
    """The fixture's chunking, pinned: everything below reads counts against it."""
    result = ingestion.ingest_from_file(str(document), CHUNK_OPTIONS)
    assert result["chunksCreated"] == 3
    assert result["totalChunks"] == 3


def test_reingesting_an_untouched_document_changes_nothing(
    ingestion: DocumentIngestion, chunks: ChunkStore, document: Path
) -> None:
    first = ingestion.ingest_from_file(str(document), CHUNK_OPTIONS)
    before = [doc["id"] for doc in chunks.query_chunks(source_id=first["sourceId"])]

    result = ingestion.reingest(first["sourceId"], CHUNK_OPTIONS)

    assert result["chunksUnchanged"] == 3
    assert result["chunksCreated"] == 0
    assert result["chunksUpdated"] == 0
    assert result["chunksDeleted"] == 0
    assert result["totalChunks"] == 3
    assert [doc["id"] for doc in chunks.query_chunks(source_id=first["sourceId"])] == before


def test_an_edited_paragraph_is_updated_in_place(
    ingestion: DocumentIngestion, chunks: ChunkStore, document: Path, embedder: FakeEmbedder
) -> None:
    """The edited chunk keeps its id (so anything referring to it still
    resolves), and only it is re-embedded."""
    first = ingestion.ingest_from_file(str(document), CHUNK_OPTIONS)
    before = [doc["id"] for doc in chunks.query_chunks(source_id=first["sourceId"])]
    embedded_before = embedder.document_calls
    document.write_text(
        "\n\n".join([PARAGRAPHS[0], "Bravo paragraph two, revised.", PARAGRAPHS[2]])
    )

    result = ingestion.reingest(first["sourceId"], CHUNK_OPTIONS)

    assert result["chunksUpdated"] == 1
    assert result["chunksUnchanged"] == 2
    assert result["chunksCreated"] == 0
    assert result["chunksDeleted"] == 0
    assert embedder.document_calls == embedded_before + 1

    stored = chunks.query_chunks(source_id=first["sourceId"])
    assert [doc["id"] for doc in stored] == before
    assert [doc["content"] for doc in stored] == [
        PARAGRAPHS[0],
        "Bravo paragraph two, revised.",
        PARAGRAPHS[2],
    ]


def test_a_new_paragraph_is_created(
    ingestion: DocumentIngestion, chunks: ChunkStore, document: Path
) -> None:
    first = ingestion.ingest_from_file(str(document), CHUNK_OPTIONS)
    document.write_text("\n\n".join([*PARAGRAPHS, "Delta paragraph four."]))

    result = ingestion.reingest(first["sourceId"], CHUNK_OPTIONS)

    assert result["chunksCreated"] == 1
    assert result["chunksUnchanged"] == 3
    assert result["chunksUpdated"] == 0
    assert result["chunksDeleted"] == 0
    assert result["totalChunks"] == 4
    stored = chunks.query_chunks(source_id=first["sourceId"])
    assert [doc["chunkIndex"] for doc in stored] == [0, 1, 2, 3]
    assert stored[3]["content"] == "Delta paragraph four."


def test_a_removed_paragraph_deletes_its_chunk(
    ingestion: DocumentIngestion, chunks: ChunkStore, document: Path
) -> None:
    first = ingestion.ingest_from_file(str(document), CHUNK_OPTIONS)
    document.write_text("\n\n".join(PARAGRAPHS[:2]))

    result = ingestion.reingest(first["sourceId"], CHUNK_OPTIONS)

    assert result["chunksDeleted"] == 1
    assert result["chunksUnchanged"] == 2
    assert result["chunksCreated"] == 0
    assert result["chunksUpdated"] == 0
    assert result["totalChunks"] == 2
    stored = chunks.query_chunks(source_id=first["sourceId"])
    assert [doc["content"] for doc in stored] == PARAGRAPHS[:2]
