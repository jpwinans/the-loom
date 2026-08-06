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

from tests.fakes import FakeEmbedder
from theloom.composites.enrichment_crawl import EnrichmentCrawlInput, enrichment_crawl
from theloom.model import EntityCreate, RelationCreate
from theloom.store.multigraph import MultiGraph


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
    monkeypatch.setattr(
        "theloom.operations.semantic.get_embedder", lambda: FakeEmbedder([1.0, 0.0, 0.0])
    )

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


def _seed_causal_graph(multi: MultiGraph) -> dict[str, str]:
    """A causal-modelling graph: every concept→concept edge is ``causes``."""
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
                    "relationType": "causes",
                    "polarity": "+",
                    "strength": "moderate",
                    "evidence": "seed",
                }
            )
        )
    return {"alpha": alpha, "beta": beta, "gamma": gamma}


def test_symmetric_evidence_never_infers_a_causal_relation(multi: MultiGraph) -> None:
    """Closure/semantic evidence is symmetric and directionless, so it cannot
    justify a causal claim — which would also invent a polarity."""
    ids = _seed_causal_graph(multi)

    result = enrichment_crawl(EnrichmentCrawlInput.model_validate({"dryRun": False}), multi)

    candidates = result["result"]["crawl"]["data"]["candidates"]
    assert candidates
    assert all(c["relationType"] == "related_to" for c in candidates), (
        "the graph's causal habits must not be copied onto directionless evidence"
    )
    pair = {ids["beta"], ids["gamma"]}
    written = [r for r in multi.get_store().list_relations() if {r.from_, r.to} == pair]
    assert written
    assert all(r.relation_type == "related_to" and r.polarity is None for r in written)


def test_same_type_pairs_stay_symmetric_but_cross_type_habits_are_kept(multi: MultiGraph) -> None:
    """Direction is arbitrary for a same-type pair, so only the symmetric
    fallback is allowed there; an ordered cross-type precedent survives."""
    from theloom.composites.enrichment_crawl import _infer_relation_type

    frequencies = {
        "concept→concept": {"part_of": 9},
        "concept→system": {"part_of": 4, "causes": 9},
        "system→concept": {"related_to": 2},
    }
    assert _infer_relation_type(frequencies, "concept", "concept") == "related_to"
    assert _infer_relation_type(frequencies, "concept", "system") == "part_of"
    assert _infer_relation_type(frequencies, "system", "concept") == "related_to"
    assert _infer_relation_type(frequencies, "concept", "unknown") == "related_to"


def test_semantic_failure_degrades_instead_of_losing_the_structural_half(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed_frontier(multi)
    store = multi.get_store()
    store.set_entity_vector(ids["beta"], [1.0, 0.0, 0.0])
    store.set_entity_vector(ids["gamma"], [0.99, 0.14, 0.0])

    def _boom(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        raise RuntimeError("embedding model unavailable")

    monkeypatch.setattr("theloom.composites.enrichment_crawl.semantic_neighbors", _boom)

    result = enrichment_crawl(EnrichmentCrawlInput(), multi)

    assert result["result"]["crawl"]["error"] is None
    crawl = result["result"]["crawl"]["data"]
    assert crawl["semanticContextAvailable"] is False
    assert "embedding model unavailable" in crawl["semanticContextReason"]
    assert crawl["candidates"], "structural closure must survive a semantic failure"
    assert all(c["sources"] == ["common-neighbors"] for c in crawl["candidates"])


def test_upstream_failure_does_not_fabricate_clean_downstream_zeros(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_frontier(multi)

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("hydrate exploded")

    monkeypatch.setattr("theloom.composites.enrichment_crawl.hydrate_graph", _boom)

    result = enrichment_crawl(EnrichmentCrawlInput.model_validate({"dryRun": False}), multi)

    sections = result["result"]
    assert sections["prioritize"]["error"] is not None
    for name in ("crawl", "enrich", "summary"):
        assert sections[name]["data"] is None, f"{name} must not report fabricated zeros"
        assert sections[name]["error"] is not None
    assert result["metadata"]["sectionsFailed"] == 4
    assert result["metadata"]["enrichedCount"] is None


def test_candidate_budget_is_not_burned_by_already_merged_pairs(multi: MultiGraph) -> None:
    store = multi.get_store()

    def entity(name: str) -> str:
        created = store.create_entity(
            EntityCreate.model_validate({"name": name, "entityType": "concept", "observations": []})
        )
        return created.id

    alpha = entity("Alpha")
    leaves = {name: entity(name) for name in ("Beta", "Delta", "Gamma")}
    for leaf in leaves.values():
        store.create_relation(
            RelationCreate.model_validate(
                {
                    "from": alpha,
                    "to": leaf,
                    "relationType": "related_to",
                    "polarity": None,
                    "strength": "moderate",
                    "evidence": "seed",
                }
            )
        )

    result = enrichment_crawl(EnrichmentCrawlInput.model_validate({"maxCandidates": 1}), multi)

    crawl = result["result"]["crawl"]["data"]
    pairs = {frozenset({c["from"]["id"], c["to"]["id"]}) for c in crawl["candidates"]}
    expected = {
        frozenset({leaves[a], leaves[b]})
        for a, b in (("Beta", "Delta"), ("Beta", "Gamma"), ("Delta", "Gamma"))
    }
    assert pairs == expected, "a merged pair must not consume the other endpoint's budget"
    by_name = {row["name"]: row for row in crawl["context"]}
    assert [by_name[name]["candidatesProposed"] for name in ("Beta", "Delta", "Gamma")] == [1, 1, 1]
    assert by_name["Alpha"]["candidatesProposed"] == 0, (
        "candidatesProposed must count real proposals, not skipped duplicates"
    )


def test_total_duration_covers_work_done_in_the_sections(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``metadata.totalDurationMs`` is the whole crawl's wall clock, not the
    runner's own bookkeeping — a caller budgets crawl cost from it."""
    import time

    from theloom.composites import enrichment_crawl as module

    _seed_frontier(multi)
    real_get_relations = module.get_relations

    def slow_get_relations(*args: object, **kwargs: object) -> object:
        time.sleep(0.05)
        return real_get_relations(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(module, "get_relations", slow_get_relations)

    result = enrichment_crawl(EnrichmentCrawlInput(), multi)

    # One 50ms sleep per crawled frontier node, so the crawl itself cannot
    # have taken less than 50ms.
    assert result["metadata"]["totalDurationMs"] >= 50
    assert result["metadata"]["totalDurationMs"] >= max(
        section["durationMs"] for section in result["result"].values()
    )
