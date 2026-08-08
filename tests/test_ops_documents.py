"""Document operations: typed-error classification, and the TL-485
`PARAMETER_IGNORED` notice for the `graph` field every command in this module
accepts and ignores (documents are global, never graph-scoped).

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

from tests.fakes import FailingEmbedder, FakeEmbedder
from theloom import config as config_module
from theloom.documents.ingestion import IngestionError
from theloom.errors import NotFoundError, OperationError, ValidationError
from theloom.operations.common import CommandInput
from theloom.operations.documents import (
    AnalyzeCategoryInput,
    DeleteDocumentInput,
    IngestContentInput,
    IngestDirectoryInput,
    IngestDocumentInput,
    IngestUrlInput,
    ListDocumentsInput,
    ReingestDocumentInput,
    _translate,
    analyze_category,
    delete_document,
    ingest_content,
    ingest_directory,
    ingest_document,
    ingest_url,
    list_documents,
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


# =============================================================================
# TL-485: `graph` is accepted-and-ignored on every command in this module (the
# module's own docstring says so) — a caller supplying it could reasonably
# believe the document was scoped to that graph, when scoping actually only
# happens later, at extract-from-documents time. Silent acceptance hid that.
# Every command below is exercised twice: once with `graph` supplied (the
# `PARAMETER_IGNORED` notice appears), once without (no `notices` key at all,
# so a caller who never passes `graph` sees the exact response shape it
# already had — this is additive, not a breaking change to a clean call).
# =============================================================================

_GRAPH_IGNORED_NOTICE = {
    "code": "PARAMETER_IGNORED",
    "message": "documents are global; the graph parameter was not applied",
    "hint": "Graph scoping happens later, at extract-from-documents time.",
}


def test_ingest_document_with_graph_carries_a_parameter_ignored_notice(
    multi: MultiGraph, tmp_path: Path
) -> None:
    doc_file = tmp_path / "notes.md"
    doc_file.write_text("# Title\n\nSome content.")
    result = ingest_document(
        IngestDocumentInput.model_validate(
            {"file_path": str(doc_file), "graph": "tl477-acceptance"}
        ),
        multi,
    )
    assert result["notices"] == [_GRAPH_IGNORED_NOTICE]


def test_ingest_document_without_graph_carries_no_notices(
    multi: MultiGraph, tmp_path: Path
) -> None:
    doc_file = tmp_path / "notes.md"
    doc_file.write_text("# Title\n\nSome content.")
    result = ingest_document(
        IngestDocumentInput.model_validate({"file_path": str(doc_file)}), multi
    )
    assert "notices" not in result


def test_ingest_content_with_graph_carries_a_parameter_ignored_notice(multi: MultiGraph) -> None:
    result = ingest_content(
        IngestContentInput.model_validate(
            {
                "source_id": "tl485-content-source",
                "content": "Some content.",
                "format": "txt",
                "graph": "tl477-acceptance",
            }
        ),
        multi,
    )
    assert result["notices"] == [_GRAPH_IGNORED_NOTICE]


def test_ingest_content_without_graph_carries_no_notices(multi: MultiGraph) -> None:
    result = ingest_content(
        IngestContentInput.model_validate(
            {
                "source_id": "tl485-content-source-clean",
                "content": "Some content.",
                "format": "txt",
            }
        ),
        multi,
    )
    assert "notices" not in result


def test_ingest_url_with_graph_carries_a_parameter_ignored_notice(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "theloom.documents.ingestion.fetch_url",
        lambda url, **kwargs: (url, "<html><body>Hello world.</body></html>"),
    )
    result = ingest_url(
        IngestUrlInput.model_validate(
            {"url": "https://example.com/tl485", "graph": "tl477-acceptance"}
        ),
        multi,
    )
    assert result["notices"] == [_GRAPH_IGNORED_NOTICE]


def test_ingest_url_without_graph_carries_no_notices(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "theloom.documents.ingestion.fetch_url",
        lambda url, **kwargs: (url, "<html><body>Hello world again.</body></html>"),
    )
    result = ingest_url(
        IngestUrlInput.model_validate({"url": "https://example.com/tl485-clean"}), multi
    )
    assert "notices" not in result


def test_reingest_document_with_graph_carries_a_parameter_ignored_notice(
    multi: MultiGraph, tmp_path: Path
) -> None:
    doc_file = tmp_path / "notes.md"
    doc_file.write_text("# Title\n\nOriginal content.")
    first = ingest_document(IngestDocumentInput.model_validate({"file_path": str(doc_file)}), multi)
    doc_file.write_text("# Title\n\nChanged content.")
    result = reingest_document(
        ReingestDocumentInput.model_validate(
            {"source_id": first["sourceId"], "graph": "tl477-acceptance"}
        ),
        multi,
    )
    assert result["notices"] == [_GRAPH_IGNORED_NOTICE]


def test_reingest_document_without_graph_carries_no_notices(
    multi: MultiGraph, tmp_path: Path
) -> None:
    doc_file = tmp_path / "notes.md"
    doc_file.write_text("# Title\n\nOriginal content.")
    first = ingest_document(IngestDocumentInput.model_validate({"file_path": str(doc_file)}), multi)
    doc_file.write_text("# Title\n\nChanged content again.")
    result = reingest_document(
        ReingestDocumentInput.model_validate({"source_id": first["sourceId"]}), multi
    )
    assert "notices" not in result


def test_delete_document_with_graph_carries_a_parameter_ignored_notice(
    multi: MultiGraph, tmp_path: Path
) -> None:
    doc_file = tmp_path / "notes.md"
    doc_file.write_text("# Title\n\nContent to delete.")
    ingested = ingest_document(
        IngestDocumentInput.model_validate({"file_path": str(doc_file)}), multi
    )
    result = delete_document(
        DeleteDocumentInput.model_validate(
            {"source_id": ingested["sourceId"], "graph": "tl477-acceptance"}
        ),
        multi,
    )
    assert result["notices"] == [_GRAPH_IGNORED_NOTICE]


def test_delete_document_without_graph_carries_no_notices(
    multi: MultiGraph, tmp_path: Path
) -> None:
    doc_file = tmp_path / "notes.md"
    doc_file.write_text("# Title\n\nContent to delete.")
    ingested = ingest_document(
        IngestDocumentInput.model_validate({"file_path": str(doc_file)}), multi
    )
    result = delete_document(
        DeleteDocumentInput.model_validate({"source_id": ingested["sourceId"]}), multi
    )
    assert "notices" not in result


def test_ingest_directory_with_graph_attaches_one_top_level_notice(
    multi: MultiGraph, tmp_path: Path
) -> None:
    (tmp_path / "one.md").write_text("# One\n\nFirst file.")
    (tmp_path / "two.md").write_text("# Two\n\nSecond file.")
    result = ingest_directory(
        IngestDirectoryInput.model_validate(
            {"dir_path": str(tmp_path), "graph": "tl477-acceptance"}
        ),
        multi,
    )
    assert result["count"] == 2
    assert len(result["items"]) == 2
    assert result["notices"] == [_GRAPH_IGNORED_NOTICE]
    assert all("notices" not in item for item in result["items"])


def test_ingest_directory_without_graph_carries_no_notices(
    multi: MultiGraph, tmp_path: Path
) -> None:
    (tmp_path / "one.md").write_text("# One\n\nFirst file.")
    result = ingest_directory(
        IngestDirectoryInput.model_validate({"dir_path": str(tmp_path)}), multi
    )
    assert result["count"] == 1
    assert "notices" not in result


def test_list_documents_with_graph_attaches_one_top_level_notice(
    multi: MultiGraph, tmp_path: Path
) -> None:
    doc_file = tmp_path / "notes.md"
    doc_file.write_text("# Title\n\nSome content.")
    ingest_document(IngestDocumentInput.model_validate({"file_path": str(doc_file)}), multi)
    result = list_documents(
        ListDocumentsInput.model_validate({"graph": "tl477-acceptance"}), multi
    )
    assert result["items"]
    assert result["notices"] == [_GRAPH_IGNORED_NOTICE]
    assert all("notices" not in item for item in result["items"])


def test_list_documents_without_graph_carries_no_notices(multi: MultiGraph, tmp_path: Path) -> None:
    doc_file = tmp_path / "notes.md"
    doc_file.write_text("# Title\n\nSome content.")
    ingest_document(IngestDocumentInput.model_validate({"file_path": str(doc_file)}), multi)
    result = list_documents(ListDocumentsInput.model_validate({}), multi)
    assert result["items"]
    assert "notices" not in result


@pytest.fixture()
def fake_embedder() -> Iterator[None]:
    """A fixed-vector embedder for analyze-category: clustering only needs
    *some* vector on every chunk, and a real fastembed call would be slower
    and non-deterministic for no benefit here."""
    config_module.set_embedder_override(FakeEmbedder([1.0, 0.0]))
    yield
    config_module.set_embedder_override(None)


def test_analyze_category_with_graph_carries_a_parameter_ignored_notice(
    multi: MultiGraph, tmp_path: Path, fake_embedder: None
) -> None:
    doc_file = tmp_path / "notes.md"
    doc_file.write_text("# Title\n\nSome content for category clustering.")
    ingest_document(
        IngestDocumentInput.model_validate(
            {"file_path": str(doc_file), "category": "tl485-category"}
        ),
        multi,
    )
    result = analyze_category(
        AnalyzeCategoryInput.model_validate(
            {"category": "tl485-category", "graph": "tl477-acceptance"}
        ),
        multi,
    )
    assert result["notices"] == [_GRAPH_IGNORED_NOTICE]


def test_analyze_category_without_graph_carries_no_notices(
    multi: MultiGraph, tmp_path: Path, fake_embedder: None
) -> None:
    doc_file = tmp_path / "notes.md"
    doc_file.write_text("# Title\n\nSome other content for category clustering.")
    ingest_document(
        IngestDocumentInput.model_validate(
            {"file_path": str(doc_file), "category": "tl485-category-clean"}
        ),
        multi,
    )
    result = analyze_category(
        AnalyzeCategoryInput.model_validate({"category": "tl485-category-clean"}), multi
    )
    assert "notices" not in result


# =============================================================================
# The schema surface must say the same thing an agent would learn from the
# notice at runtime: --schema/COMMANDS.md are the only things a caller reads
# before ever invoking the command, so the field description carries the
# truth ("documents are global") independently of the notice mechanism.
# =============================================================================


@pytest.mark.parametrize(
    "model",
    [
        IngestDocumentInput,
        IngestDirectoryInput,
        IngestUrlInput,
        IngestContentInput,
        ListDocumentsInput,
        DeleteDocumentInput,
        ReingestDocumentInput,
        AnalyzeCategoryInput,
    ],
    ids=lambda m: m.__name__,
)
def test_graph_field_schema_description_states_documents_are_global(
    model: type[CommandInput],
) -> None:
    description = model.model_fields["graph"].description
    assert description is not None
    assert "global" in description.lower()
    assert "ignored" in description.lower()
