"""Document operations: typed-error classification.

ingest/delete/reingest translate `theloom.documents.ingestion.IngestionError`
into the CLI's typed error hierarchy by exception class
(IngestionNotFoundError -> NOT_FOUND, IngestionValidationError ->
VALIDATION_ERROR, plain IngestionError -> OPERATION_ERROR) — never by
pattern-matching the failure's message text.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from falkordb import FalkorDB
from redis import Redis

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
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


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


def test_translate_classifies_by_exception_class_not_message_text() -> None:
    """A generic ingestion failure whose message happens to contain "exist"
    or "not found" must still classify as OPERATION_ERROR unless it was
    raised as the specific typed subclass."""
    misleading = IngestionError("this file does not exist anymore, not found on disk")
    translated = _translate(misleading)
    assert isinstance(translated, OperationError)
    assert not isinstance(translated, NotFoundError)
