"""Chunk writes are event-sourced like every other mutation.

Document ingestion used to write chunks straight to FalkorDB with no entry in
the event log, so ingesting, reingesting or deleting a document left no
history to replay. A chunk write now carries the same contract as an entity
write: the Cypher and its event append are one MULTI/EXEC unit, compensated in
whichever direction the failure runs.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from typing import Any

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.documents import chunkstore
from theloom.documents.chunkstore import CHUNK_GRAPH_SUFFIX, ChunkStore
from theloom.documents.ingestion import DocumentIngestion
from theloom.errors import OperationError
from theloom.store.events import EventLog


@pytest.fixture()
def chunks(db: FalkorDB, namespace: str) -> ChunkStore:
    return ChunkStore(db, key_prefix=namespace)


@pytest.fixture()
def log(redis_client: Redis, namespace: str) -> EventLog:
    return EventLog(redis_client, graph_name=CHUNK_GRAPH_SUFFIX, key_prefix=namespace)


def metadata(chunk_id: str, source_id: str = "doc-1", index: int = 0) -> dict[str, Any]:
    return {
        "id": chunk_id,
        "entityId": chunk_id,
        "entryType": "document_chunk",
        "sourceId": source_id,
        "sourceName": "Doc One",
        "chunkIndex": index,
        "contentHash": f"hash-{index}",
        "content": f"chunk {index}",
    }


class Boom(RuntimeError):
    """Injected failure at the event-append step."""


# =============================================================================
# Chunk writes appear in the log
# =============================================================================


def test_upsert_chunk_appends_a_chunk_created_event(chunks: ChunkStore, log: EventLog) -> None:
    chunks.upsert_chunk("chunk-0", metadata("chunk-0"), [1.0, 0.0, 0.0])

    events = log.read_all()
    assert [event.type for event in events] == ["chunk_created"]
    payload = events[0].payload
    assert payload["chunkId"] == "chunk-0"
    assert payload["sourceId"] == "doc-1"
    assert payload["chunkIndex"] == 0
    assert payload["embedded"] is True


def test_upsert_without_a_vector_is_logged_too(chunks: ChunkStore, log: EventLog) -> None:
    chunks.upsert_chunk("chunk-0", metadata("chunk-0"), None)

    events = log.read_all()
    assert [event.type for event in events] == ["chunk_created"]
    assert events[0].payload["embedded"] is False


def test_delete_chunk_appends_a_chunk_deleted_event(chunks: ChunkStore, log: EventLog) -> None:
    chunks.upsert_chunk("chunk-0", metadata("chunk-0"), None)
    chunks.delete_chunk("chunk-0")

    assert [event.type for event in log.read_all()] == ["chunk_created", "chunk_deleted"]
    assert log.read_all()[-1].payload["chunkId"] == "chunk-0"


def test_deleting_a_document_names_every_chunk_it_removed(
    chunks: ChunkStore, log: EventLog
) -> None:
    for index in range(3):
        chunks.upsert_chunk(f"chunk-{index}", metadata(f"chunk-{index}", index=index), None)
    chunks.upsert_chunk("other-0", metadata("other-0", source_id="doc-2"), None)

    assert chunks.delete_where_source("doc-1") == 3

    deleted = [event for event in log.read_all() if event.type == "chunks_deleted"]
    assert len(deleted) == 1
    assert deleted[0].payload["sourceId"] == "doc-1"
    assert deleted[0].payload["chunkIds"] == ["chunk-0", "chunk-1", "chunk-2"]
    assert deleted[0].payload["count"] == 3
    assert [doc["id"] for doc in chunks.query_chunks()] == ["other-0"]


def test_deleting_an_absent_document_appends_nothing(chunks: ChunkStore, log: EventLog) -> None:
    assert chunks.delete_where_source("missing") == 0
    assert log.read_all() == []


def race_during_snapshot(monkeypatch: pytest.MonkeyPatch, interference: Callable[[], None]) -> None:
    """Run ``interference`` in the window between the delete's snapshot read of
    the document's chunk ids and the transaction that removes them."""
    real = chunkstore.fetch_all_rows

    def snapshot_then_interfere(*args: Any, **kwargs: Any) -> Any:
        rows = real(*args, **kwargs)
        interference()
        return rows

    monkeypatch.setattr(chunkstore, "fetch_all_rows", snapshot_then_interfere)


def test_a_concurrent_insert_during_a_document_delete_is_not_silently_removed(
    chunks: ChunkStore, log: EventLog, db: FalkorDB, namespace: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The delete removes exactly the chunks its event names — a chunk written
    after the snapshot is neither deleted nor claimed, so replaying the log
    reproduces what the live store did."""
    for index in range(3):
        chunks.upsert_chunk(f"chunk-{index}", metadata(f"chunk-{index}", index=index), None)
    other = ChunkStore(db, key_prefix=namespace)

    race_during_snapshot(
        monkeypatch, lambda: other.upsert_chunk("chunk-9", metadata("chunk-9", index=9), None)
    )
    assert chunks.delete_where_source("doc-1") == 3

    deleted = [event for event in log.read_all() if event.type == "chunks_deleted"]
    assert deleted[0].payload["chunkIds"] == ["chunk-0", "chunk-1", "chunk-2"]
    assert deleted[0].payload["count"] == 3
    # The interloper survives, exactly as the log says.
    assert [doc["id"] for doc in chunks.query_chunks()] == ["chunk-9"]


def test_a_concurrent_delete_during_a_document_delete_is_not_double_counted(
    chunks: ChunkStore, log: EventLog, db: FalkorDB, namespace: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A racing deleter takes a chunk first: the count returned is what this
    call actually removed, not what its snapshot hoped to remove."""
    for index in range(3):
        chunks.upsert_chunk(f"chunk-{index}", metadata(f"chunk-{index}", index=index), None)
    other = ChunkStore(db, key_prefix=namespace)

    race_during_snapshot(monkeypatch, lambda: other.delete_chunk("chunk-2"))
    assert chunks.delete_where_source("doc-1") == 2

    assert chunks.query_chunks() == []


def test_ingestion_appends_one_event_per_chunk(chunks: ChunkStore, log: EventLog) -> None:
    ingestion = DocumentIngestion(chunks)

    result = ingestion.ingest_content("src-1", "# Title\n\nsome body text\n", "markdown")

    events = log.read_all()
    assert result["chunksCreated"] > 0
    assert [event.type for event in events] == ["chunk_created"] * result["chunksCreated"]
    assert {event.payload["sourceId"] for event in events} == {"src-1"}

    ingestion.delete_document("src-1")
    assert [event.type for event in log.read_all()][-1] == "chunks_deleted"


# =============================================================================
# The write and its event are one unit
# =============================================================================


def test_a_chunk_write_with_a_failing_event_append_writes_nothing(
    chunks: ChunkStore, log: EventLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise Boom("event append failed")

    monkeypatch.setattr(EventLog, "queue", explode)

    with pytest.raises(Boom):
        chunks.upsert_chunk("chunk-0", metadata("chunk-0"), None)

    assert chunks.query_chunks() == []
    assert log.read_all() == []


def test_a_failing_chunk_delete_leaves_the_chunks_and_the_log_alone(
    chunks: ChunkStore, log: EventLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks.upsert_chunk("chunk-0", metadata("chunk-0"), None)
    before = [event.type for event in log.read_all()]

    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise Boom("event append failed")

    monkeypatch.setattr(EventLog, "queue", explode)

    with pytest.raises(Boom):
        chunks.delete_where_source("doc-1")

    assert [doc["id"] for doc in chunks.query_chunks()] == ["chunk-0"]
    assert [event.type for event in log.read_all()] == before


def test_a_failing_chunk_cypher_appends_no_event(chunks: ChunkStore, log: EventLog) -> None:
    with pytest.raises(Exception, match="Invalid input"):
        chunks._commit(
            ("MATCH (c:_Chunk) RETRN c", {}),
            [("chunk_created", {"chunkId": "chunk-0"})],
        )
    assert log.read_all() == []


@pytest.fixture()
def poisoned_key(redis_client: Redis, namespace: str) -> str:
    """A key holding a non-stream value — XADD against it fails at EXEC."""
    key = f"{namespace}:not-a-stream"
    redis_client.set(key, "not a stream")
    return key


def break_queued_append(monkeypatch: pytest.MonkeyPatch, poisoned_key: str) -> None:
    """Aim the in-transaction XADD at a non-stream key, leaving the retry good."""
    calls = itertools.count(1)

    def queue(self: EventLog, pipe: Any, event_type: str, payload: dict[str, Any]) -> None:
        next(calls)
        pipe.xadd(poisoned_key, {"type": event_type, "payload": json.dumps(payload)})

    monkeypatch.setattr(EventLog, "queue", queue)


def test_a_failed_chunk_event_append_is_repaired_after_the_write_commits(
    chunks: ChunkStore, log: EventLog, poisoned_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    break_queued_append(monkeypatch, poisoned_key)

    chunks.upsert_chunk("chunk-0", metadata("chunk-0"), None)

    assert [doc["id"] for doc in chunks.query_chunks()] == ["chunk-0"]
    events = log.read_all()
    assert [event.type for event in events] == ["chunk_created"]
    assert events[0].payload["chunkId"] == "chunk-0"


def test_an_unrepairable_chunk_event_append_names_the_gap(
    chunks: ChunkStore, log: EventLog, redis_client: Redis
) -> None:
    redis_client.set(log.key, "not a stream")  # every XADD to the log fails

    with pytest.raises(OperationError) as raised:
        chunks.upsert_chunk("chunk-0", metadata("chunk-0"), None)

    assert "chunk_created" in str(raised.value)
    assert raised.value.code == "OPERATION_ERROR"
    assert raised.value.__cause__ is not None
    # Redis cannot roll the write back, and the error does not pretend it did.
    assert [doc["id"] for doc in chunks.query_chunks()] == ["chunk-0"]
