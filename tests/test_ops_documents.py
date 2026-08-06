"""Document operations: typed-error classification.

ingest/delete/reingest translate `theloom.documents.ingestion.IngestionError`
into the CLI's typed error hierarchy by exception class
(IngestionNotFoundError -> NOT_FOUND, IngestionValidationError ->
VALIDATION_ERROR, plain IngestionError -> OPERATION_ERROR) — never by
pattern-matching the failure's message text.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.fakes import FailingEmbedder
from theloom import config as config_module
from theloom.documents.ingestion import IngestionError
from theloom.errors import NotFoundError, OperationError, ValidationError
from theloom.operations.documents import (
    DeleteDocumentInput,
    IngestDocumentInput,
    ReingestDocumentInput,
    _translate,
    delete_document,
    ingest_document,
    reingest_document,
)
from theloom.store.multigraph import MultiGraph


@pytest.fixture()
def failing_embedder() -> Iterator[None]:
    config_module.set_embedder_override(FailingEmbedder())
    yield
    config_module.set_embedder_override(None)


def test_ingest_missing_file_is_not_found(multi: MultiGraph, tmp_path: Path) -> None:
    missing = str(tmp_path / "does-not-exist.md")
    with pytest.raises(NotFoundError) as excinfo:
        ingest_document(IngestDocumentInput.model_validate({"file_path": missing}), multi)
    assert "File not found" in str(excinfo.value)
    assert excinfo.value.code == "NOT_FOUND"


def test_ingest_unsupported_extension_is_a_validation_error(
    multi: MultiGraph, tmp_path: Path
) -> None:
    bad_file = tmp_path / "notes.exe"
    bad_file.write_text("hello")
    with pytest.raises(ValidationError) as excinfo:
        ingest_document(IngestDocumentInput.model_validate({"file_path": str(bad_file)}), multi)
    assert excinfo.value.code == "VALIDATION_ERROR"


def test_delete_missing_document_is_not_found(multi: MultiGraph) -> None:
    with pytest.raises(NotFoundError) as excinfo:
        delete_document(DeleteDocumentInput.model_validate({"source_id": "no-such-source"}), multi)
    assert "No document found" in str(excinfo.value)
    assert excinfo.value.code == "NOT_FOUND"


def test_reingest_missing_document_is_not_found(multi: MultiGraph) -> None:
    with pytest.raises(NotFoundError) as excinfo:
        reingest_document(
            ReingestDocumentInput.model_validate({"source_id": "no-such-source"}), multi
        )
    assert excinfo.value.code == "NOT_FOUND"


def test_ingest_success_still_returns_a_document_summary(multi: MultiGraph, tmp_path: Path) -> None:
    doc_file = tmp_path / "notes.md"
    doc_file.write_text("# Title\n\nSome content.")
    result = ingest_document(
        IngestDocumentInput.model_validate({"file_path": str(doc_file)}), multi
    )
    assert result["chunksCreated"] >= 1


def test_ingest_records_the_embedding_failure_reason_on_the_chunk(
    multi: MultiGraph, tmp_path: Path, failing_embedder: None
) -> None:
    """A failing embedder used to be swallowed silently: chunks landed with
    vector=None and no trace of why. The reason is now recorded on the chunk
    metadata as a fact, not lost."""
    doc_file = tmp_path / "notes.md"
    doc_file.write_text("# Title\n\nSome content.")

    result = ingest_document(
        IngestDocumentInput.model_validate({"file_path": str(doc_file)}), multi
    )
    assert result["chunksCreated"] >= 1

    chunks = multi.chunk_store().query_chunks(source_id=result["sourceId"])
    assert len(chunks) == result["chunksCreated"]
    for chunk in chunks:
        assert chunk["embeddingError"] == "embedding backend unavailable"


def test_reingest_records_the_embedding_failure_reason_on_changed_chunks(
    multi: MultiGraph, tmp_path: Path, failing_embedder: None
) -> None:
    doc_file = tmp_path / "notes.md"
    doc_file.write_text("# Title\n\nOriginal content.")
    first = ingest_document(IngestDocumentInput.model_validate({"file_path": str(doc_file)}), multi)

    doc_file.write_text("# Title\n\nContent that changed since the first ingest.")
    result = reingest_document(
        ReingestDocumentInput.model_validate({"source_id": first["sourceId"]}), multi
    )
    assert result["chunksUpdated"] + result["chunksCreated"] >= 1

    chunks = multi.chunk_store().query_chunks(source_id=first["sourceId"])
    assert any(c.get("embeddingError") == "embedding backend unavailable" for c in chunks)


def test_translate_classifies_by_exception_class_not_message_text() -> None:
    """A generic ingestion failure whose message happens to contain "exist"
    or "not found" must still classify as OPERATION_ERROR unless it was
    raised as the specific typed subclass."""
    misleading = IngestionError("this file does not exist anymore, not found on disk")
    translated = _translate(misleading)
    assert isinstance(translated, OperationError)
    assert not isinstance(translated, NotFoundError)
