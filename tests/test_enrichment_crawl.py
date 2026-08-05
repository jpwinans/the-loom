"""Enrichment-Crawl composite: the crawl must actually run without an LLM.

The command used to return a three-failed-section "no LLM" envelope or, with
an LLM configured, raise a typed OPERATION_ERROR for the unimplemented CISC
N-sample voting — honest, but a registered command that could never produce a
finding. The deterministic half of the contract needs no LLM at all: rank the
under-described frontier, gather each node's context through the existing
read ops, and propose enrichment relations from structural closure (plus
semantic neighbours when entity vectors exist). Only the N-sample *voting*
needs a provider, and its absence is now reported as a boundary instead of
failing the command.
"""

from __future__ import annotations

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.composites.enrichment_crawl import EnrichmentCrawlInput, enrichment_crawl
from theloom.model import EntityCreate, RelationCreate
from theloom.store.multigraph import MultiGraph


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


class _StubEmbedder:
    """embed_query returns a fixed vector regardless of text (no real model)."""

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


def _seed_frontier(multi: MultiGraph) -> dict[str, str]:
    """A well-described hub plus two under-described leaves that share it.

    Beta and Gamma have a common neighbour (Alpha) and no edge between them —
    the open triad structural closure is meant to find.
    """
    store = multi.get_store()

    def entity(name: str, observations: list[str]) -> str:
        created = store.create_entity(
            EntityCreate.model_validate(
                {"name": name, "entityType": "concept", "observations": observations}
            )
        )
        return created.id

    alpha = entity("Alpha", ["well described", "second note", "third note"])
    beta = entity("Beta", ["thin note"])
    gamma = entity("Gamma", [])

    for target in (beta, gamma):
        store.create_relation(
            RelationCreate.model_validate(
                {
                    "from": alpha,
                    "to": target,
                    "relationType": "related_to",
                    "polarity": None,
                    "strength": "moderate",
                    "evidence": "seed",
                }
            )
        )
    return {"alpha": alpha, "beta": beta, "gamma": gamma}


def test_crawl_succeeds_without_an_llm_and_proposes_real_candidates(multi: MultiGraph) -> None:
    ids = _seed_frontier(multi)

    result = enrichment_crawl(EnrichmentCrawlInput(), multi)

    assert result["metadata"]["sectionsFailed"] == 0

    prioritize = result["result"]["prioritize"]["data"]
    assert prioritize["frontierSize"] >= 2
    ranked = prioritize["nodes"]
    # Under-described first: Gamma (0 observations) outranks the Alpha hub.
    assert ranked[0]["id"] == ids["gamma"]
    assert ranked[0]["observationCount"] == 0
    assert ranked[0]["relationCount"] == 1
    assert [n["id"] for n in ranked][-1] == ids["alpha"]
    assert ranked[0]["priorityScore"] > ranked[-1]["priorityScore"]

    crawl = result["result"]["crawl"]["data"]
    assert crawl["nodesCrawled"] >= 2
    candidates = crawl["candidates"]
    assert candidates, "the open Beta-Gamma triad must yield a closure candidate"
    pair = {candidates[0]["from"]["id"], candidates[0]["to"]["id"]}
    assert pair == {ids["beta"], ids["gamma"]}
    assert candidates[0]["confidence"] > 0
    assert candidates[0]["relationType"]
    assert "common-neighbors" in candidates[0]["sources"]
    # One candidate per unordered pair — not the same edge proposed from both ends.
    assert len(candidates) == 1


def test_default_is_a_dry_run_that_writes_nothing(multi: MultiGraph) -> None:
    _seed_frontier(multi)
    before = len(multi.get_store().list_relations())

    result = enrichment_crawl(EnrichmentCrawlInput(), multi)

    assert result["metadata"]["dryRun"] is True
    assert result["metadata"]["enrichedCount"] == 0
    enrich = result["result"]["enrich"]["data"]
    assert enrich["created"] == 0
    assert enrich["wouldCreate"] >= 1
    assert len(multi.get_store().list_relations()) == before


def test_dry_run_false_creates_the_proposed_relations(multi: MultiGraph) -> None:
    ids = _seed_frontier(multi)

    result = enrichment_crawl(EnrichmentCrawlInput.model_validate({"dryRun": False}), multi)

    assert result["metadata"]["enrichedCount"] >= 1
    assert result["result"]["enrich"]["data"]["failures"] == []
    relations = multi.get_store().list_relations()
    assert any({r.from_, r.to} == {ids["beta"], ids["gamma"]} for r in relations), (
        "the accepted candidate must be a real, event-logged relation"
    )


def test_min_confidence_and_max_nodes_bound_the_crawl(multi: MultiGraph) -> None:
    _seed_frontier(multi)

    bounded = enrichment_crawl(EnrichmentCrawlInput.model_validate({"maxNodes": 1}), multi)
    assert bounded["result"]["crawl"]["data"]["nodesCrawled"] == 1
    assert bounded["result"]["prioritize"]["data"]["frontierSize"] == 1

    filtered = enrichment_crawl(EnrichmentCrawlInput.model_validate({"minConfidence": 1}), multi)
    crawl = filtered["result"]["crawl"]["data"]
    assert crawl["candidates"] == [] or all(c["confidence"] >= 1 for c in crawl["candidates"]), (
        "minConfidence must gate the proposals, not be advisory"
    )


def test_semantic_context_is_used_when_entity_vectors_exist(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed_frontier(multi)
    store = multi.get_store()
    store.set_entity_vector(ids["beta"], [1.0, 0.0, 0.0])
    store.set_entity_vector(ids["gamma"], [0.99, 0.14, 0.0])
    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: _StubEmbedder())

    result = enrichment_crawl(EnrichmentCrawlInput(), multi)

    crawl = result["result"]["crawl"]["data"]
    assert crawl["semanticContextAvailable"] is True
    sources = {source for c in crawl["candidates"] for source in c["sources"]}
    assert "semantic-neighbors" in sources


def test_missing_embeddings_are_reported_not_fatal(multi: MultiGraph) -> None:
    _seed_frontier(multi)

    result = enrichment_crawl(EnrichmentCrawlInput(), multi)

    crawl = result["result"]["crawl"]["data"]
    assert crawl["semanticContextAvailable"] is False
    assert "embedding" in crawl["semanticContextReason"].lower()
    assert result["metadata"]["sectionsFailed"] == 0


def test_cisc_voting_boundary_is_reported_in_the_output(multi: MultiGraph) -> None:
    _seed_frontier(multi)

    result = enrichment_crawl(EnrichmentCrawlInput.model_validate({"numSamples": 5}), multi)

    voting = result["result"]["summary"]["data"]["voting"]
    assert voting["requestedSamples"] == 5
    assert voting["samplesUsed"] == 0
    assert voting["applied"] is False
    assert "llm" in voting["reason"].lower()
    assert result["result"]["summary"]["data"]["text"]


def test_empty_graph_reports_an_honest_empty_crawl(multi: MultiGraph) -> None:
    result = enrichment_crawl(EnrichmentCrawlInput(), multi)

    assert result["metadata"]["sectionsFailed"] == 0
    assert result["result"]["prioritize"]["data"]["frontierSize"] == 0
    assert result["result"]["crawl"]["data"]["candidates"] == []
    assert result["metadata"]["enrichedCount"] == 0


def test_registered_summary_no_longer_marks_the_command_unavailable() -> None:
    """The registry summary (and the COMMANDS.md catalog generated from it)
    must stop telling callers the command can never succeed, and must flag
    that it writes when dryRun is false."""
    from theloom.cli.registry import COMMANDS

    descriptor = next(c for c in COMMANDS if c.name == "enrichment-crawl")
    assert "unavailable" not in descriptor.summary.lower()
    assert "writes" in descriptor.summary.lower()
