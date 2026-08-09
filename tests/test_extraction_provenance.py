"""LLM document extraction must ground entities with a `provenance` block, not
just a `sources` edge to the per-run source entity.

Before this test: entities created by run_document_extraction carried no
`provenance`, so every one of them surfaced in the `unprovenanced` epistemic
query, defeating its purpose.
"""

from __future__ import annotations

import json

import httpx
import pytest

from theloom.documents.metadata import ChunkMetadata
from theloom.model import SourceType
from theloom.operations.epistemic import TypedEpistemicInput, unprovenanced
from theloom.operations.extraction import ExtractFromDocumentsInput
from theloom.store.multigraph import MultiGraph
from theloom.synthesis.llm import OpenAICompatSynthesisClient

GRAPH = "default"

_LLM_RESPONSE = json.dumps(
    {
        "entities": [
            {
                "name": "Provenance Chain",
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
            200,
            json={"choices": [{"message": {"content": _LLM_RESPONSE}}], "usage": {}},
        )

    return OpenAICompatSynthesisClient(
        base_url="http://mock", model="mock-model", transport=httpx.MockTransport(handler)
    )


@pytest.fixture()
def seeded_chunk(multi: MultiGraph) -> None:
    multi.chunk_store().upsert_chunk(
        "chunk-0",
        ChunkMetadata(
            id="chunk-0",
            source_id="doc-1",
            source_name="Doc One",
            source_format="markdown",
            chunk_index=0,
            total_chunks=1,
            content="Provenance chains ground extracted knowledge.",
            category="notes",
        ),
        None,
    )


def test_llm_extracted_entities_carry_provenance(
    multi: MultiGraph, seeded_chunk: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from theloom.extraction import pipeline

    monkeypatch.setattr(pipeline, "create_synthesis_client", _mock_client)

    result = pipeline.run_document_extraction(
        ExtractFromDocumentsInput.model_validate({"category": "notes", "graph": GRAPH}),
        multi,
        dry_run=False,
    )
    assert result["totalEntitiesCreated"] == 1

    store = multi.get_store(GRAPH)
    entities = store.list_entities()
    extracted = [e for e in entities if e.name == "Provenance Chain"]
    assert len(extracted) == 1
    entity = extracted[0]

    assert entity.provenance is not None
    prov = entity.provenance
    assert prov.source_type == SourceType.DOCUMENT
    assert prov.extraction_method is not None
    assert prov.extraction_method.value == "llm_prompted"
    assert prov.extractor == "mock-model"
    assert prov.extraction_date is not None

    # The source entity id the pipeline created for this run.
    source_entities = [e for e in entities if e.entity_type.value == "source"]
    assert len(source_entities) == 1
    assert prov.source_id == source_entities[0].id


def test_llm_extracted_entities_do_not_surface_as_unprovenanced(
    multi: MultiGraph, seeded_chunk: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from theloom.extraction import pipeline

    monkeypatch.setattr(pipeline, "create_synthesis_client", _mock_client)

    pipeline.run_document_extraction(
        ExtractFromDocumentsInput.model_validate({"category": "notes", "graph": GRAPH}),
        multi,
        dry_run=False,
    )

    results = unprovenanced(TypedEpistemicInput.model_validate({"graph": GRAPH}), multi)
    names = {r["name"] for r in results["items"]}
    assert "Provenance Chain" not in names


def test_total_links_created_counts_entities_with_chunk_provenance(
    multi: MultiGraph, seeded_chunk: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`totalLinksCreated` must report the number of entities whose
    `provenance.externalRef` was actually set to a chunk id — the real
    entity->chunk pointer created by this run — not a hardcoded 0.
    """
    from theloom.extraction import pipeline

    monkeypatch.setattr(pipeline, "create_synthesis_client", _mock_client)

    result = pipeline.run_document_extraction(
        ExtractFromDocumentsInput.model_validate({"category": "notes", "graph": GRAPH}),
        multi,
        dry_run=False,
    )

    assert result["totalEntitiesCreated"] == 1
    assert result["totalLinksCreated"] == 1
