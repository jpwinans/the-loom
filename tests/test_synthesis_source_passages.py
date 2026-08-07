"""Synthesis source passages must be the real document text.

`theloom/synthesis/links.py` used to return `[]` unconditionally ("links live
in process memory"), so from the CLI every `sourcePassages` list was empty:
`traverse-synthesis`'s `evidenceUnits[].sourcePassages`, `synthesize`'s
`evidence_map` "Sources:" line, and the `evidence_map` ordering that is
supposed to float sourced entities first were all inert.

Extraction now records the originating chunk id on each entity's
`provenance.externalRef` (a pointer, like tree-sitter's `file:line`), and the
lookups below resolve it against the chunk store the pipeline already reads.
Entities with no such pointer keep yielding `[]` — honest degradation, no
backfill implied.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from theloom.documents.metadata import ChunkMetadata
from theloom.operations.extraction import ExtractFromDocumentsInput
from theloom.operations.synthesis import SynthesizeInput, TraverseSynthesisInput
from theloom.operations.synthesis import synthesize as synthesize_op
from theloom.operations.synthesis import traverse_synthesis as traverse_synthesis_op
from theloom.store.multigraph import MultiGraph
from theloom.synthesis.links import get_links_for_entity, get_source_passages
from theloom.synthesis.llm import OpenAICompatSynthesisClient

Doc = dict[str, Any]

GRAPH = "default"
PASSAGE = "Provenance chains ground extracted knowledge in the source text."
ENTITY_NAME = "Provenance Chain"


# =============================================================================
# The pure lookups
# =============================================================================


def _entity(external_ref: str | None, source_type: str = "document") -> Doc:
    return {
        "id": "e1",
        "name": ENTITY_NAME,
        "provenance": {
            "sourceType": source_type,
            "sourceId": "src-1",
            "externalRef": external_ref,
            "extractor": "mock-model",
            "extractionMethod": "llm_prompted",
        },
    }


def _lookup(chunks: Doc) -> Any:
    def get_chunk(chunk_id: str) -> Doc | None:
        found = chunks.get(chunk_id)
        return dict(found) if found is not None else None

    return get_chunk


_CHUNKS = {"chunk-0": {"id": "chunk-0", "content": PASSAGE}}


class TestLinkLookups:
    def test_resolves_the_chunk_named_by_provenance(self) -> None:
        assert get_source_passages(_entity("chunk-0"), _lookup(_CHUNKS)) == [PASSAGE]

    def test_links_carry_the_chunk_id_and_the_evidence(self) -> None:
        links = get_links_for_entity(_entity("chunk-0"), _lookup(_CHUNKS))
        assert [link["evidence"] for link in links] == [PASSAGE]
        assert [link["chunkId"] for link in links] == ["chunk-0"]

    def test_entity_without_provenance_yields_nothing(self) -> None:
        assert get_source_passages({"id": "e1", "name": "x"}, _lookup(_CHUNKS)) == []

    def test_null_external_ref_yields_nothing(self) -> None:
        assert get_source_passages(_entity(None), _lookup(_CHUNKS)) == []

    def test_deleted_chunk_degrades_to_nothing(self) -> None:
        assert get_source_passages(_entity("chunk-gone"), _lookup(_CHUNKS)) == []

    def test_empty_chunk_content_is_not_a_passage(self) -> None:
        assert get_source_passages(_entity("blank"), _lookup({"blank": {"content": ""}})) == []

    def test_non_document_provenance_is_not_a_chunk_pointer(self) -> None:
        """tree-sitter writes `file.py:12` into externalRef under sourceType
        'observation' — that is not a chunk id and must never be looked up."""
        calls: list[str] = []

        def spy(chunk_id: str) -> Doc | None:
            calls.append(chunk_id)
            return None

        assert get_source_passages(_entity("theloom/x.py:12", "observation"), spy) == []
        assert calls == []


# =============================================================================
# End to end: ingest a chunk, extract, synthesize
# =============================================================================


_LLM_RESPONSE = json.dumps(
    {
        "entities": [
            {
                "name": ENTITY_NAME,
                "entityType": "concept",
                "observations": ["A concept extracted from a document."],
            }
        ],
        "relations": [],
    }
)


def _mock_client() -> OpenAICompatSynthesisClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": _LLM_RESPONSE}}], "usage": {}}
        )

    return OpenAICompatSynthesisClient(
        base_url="http://mock", model="mock-model", transport=httpx.MockTransport(handler)
    )


@pytest.fixture()
def extracted(multi: MultiGraph, monkeypatch: pytest.MonkeyPatch) -> None:
    """One ingested chunk, extracted into the graph by a mocked LLM."""
    from theloom.extraction import pipeline
    from theloom.operations import synthesis as synthesis_ops

    multi.chunk_store().upsert_chunk(
        "chunk-0",
        ChunkMetadata(
            id="chunk-0",
            source_id="doc-1",
            source_name="Doc One",
            source_format="markdown",
            chunk_index=0,
            total_chunks=1,
            content=PASSAGE,
            category="notes",
        ),
        None,
    )
    monkeypatch.setattr(pipeline, "create_synthesis_client", _mock_client)
    pipeline.run_document_extraction(
        ExtractFromDocumentsInput.model_validate({"category": "notes", "graph": GRAPH}),
        multi,
        dry_run=False,
    )
    # Synthesis itself runs template-only, so the assertions below pin the
    # deterministic path rather than an LLM's prose.
    monkeypatch.setattr(synthesis_ops, "create_synthesis_client", lambda: None)


def test_traverse_synthesis_carries_the_source_passage(multi: MultiGraph, extracted: None) -> None:
    result = traverse_synthesis_op(
        TraverseSynthesisInput.model_validate({"query": ENTITY_NAME, "graph": GRAPH}), multi
    )

    units = [u for u in result["evidenceUnits"] if u["entity"]["name"] == ENTITY_NAME]
    assert len(units) == 1
    assert units[0]["sourcePassages"] == [PASSAGE]


def test_traverse_synthesis_adaptive_carries_the_source_passage(
    multi: MultiGraph, extracted: None
) -> None:
    result = traverse_synthesis_op(
        TraverseSynthesisInput.model_validate(
            {"query": ENTITY_NAME, "graph": GRAPH, "mode": "adaptive"}
        ),
        multi,
    )

    units = [u for u in result["evidenceUnits"] if u["entity"]["name"] == ENTITY_NAME]
    assert len(units) == 1
    assert units[0]["sourcePassages"] == [PASSAGE]


def test_evidence_map_synthesis_prints_the_source_passage(
    multi: MultiGraph, extracted: None
) -> None:
    result = synthesize_op(
        SynthesizeInput.model_validate(
            {"query": ENTITY_NAME, "graph": GRAPH, "format": "evidence_map"}
        ),
        multi,
    )

    assert f"Sources: {PASSAGE}" in result["text"]


def test_entities_without_a_chunk_pointer_stay_empty(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hand-created entity has no provenance pointer — no crash, no passage."""
    from theloom.model import EntityCreate
    from theloom.operations import synthesis as synthesis_ops

    monkeypatch.setattr(synthesis_ops, "create_synthesis_client", lambda: None)
    multi.get_store(GRAPH).create_entity(
        EntityCreate.model_validate(
            {"name": ENTITY_NAME, "entityType": "concept", "observations": ["hand made"]}
        )
    )

    result = traverse_synthesis_op(
        TraverseSynthesisInput.model_validate({"query": ENTITY_NAME, "graph": GRAPH}), multi
    )

    assert [u["sourcePassages"] for u in result["evidenceUnits"]] == [[]]
