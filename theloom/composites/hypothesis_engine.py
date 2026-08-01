"""Hypothesis Engine composite.

Chains semantic gap detection, entity proposal, confidence filtering,
deduplication, and multi-dimensional scoring into a ranked list of hypotheses.

Five sections, each inside :func:`time_section`:

1. ``gaps`` - semantic gap detection (``gapLimit`` gaps).
2. ``proposals`` - entity proposals via ``propose_entities`` (pattern completion;
   no ``llmClient`` is supplied, so LLM reasoning is inert).
3. ``filter`` - drop proposals below ``minConfidence``.
4. ``dedup`` - deduplication gate (or a passthrough when ``dedupEnabled`` is
   false).
5. ``rank`` - score novelty / plausibility / testability, compute aggregate
   interestingness, sort by ``overallScore`` (stable), truncate to ``maxResults``.

Unlike the other composites this returns ``{composite, hypotheses, summary}``
directly — not a bare envelope.

Template mode (this composite takes ``(params, multi)`` with no embedding
pipeline): the deduplication gate falls back to deterministic name matching, and
with ``embeddingsAvailable`` defaulting to false every proposal's aggregate
interestingness is ``0``, so the stable sort preserves dedup order. That makes
the command deterministic.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import Field

from theloom.analysis.interestingness import (
    compute_compression_progress,
    compute_interestingness,
    compute_structural_novelty,
)
from theloom.composites.framework import build_composite_result, time_section
from theloom.operations.common import CommandInput
from theloom.operations.semantic import SemanticGapsInput, semantic_gaps
from theloom.semantic.deduplication_gate import deduplicate_proposals
from theloom.semantic.entity_proposer import propose_entities
from theloom.store.multigraph import MultiGraph

DEFAULT_MAX_RESULTS = 10
MAX_MAX_RESULTS = 100
DEFAULT_MIN_CONFIDENCE = 0.0
DEFAULT_GAP_LIMIT = 20
MAX_GAP_LIMIT = 200
DEFAULT_MIN_SIMILARITY = 0.3

# Neutral fallbacks when a proposal carries no simulation data.
NEUTRAL_SI = 0.5
NEUTRAL_STRUCTURAL_NOVELTY = 0.0
NEUTRAL_COMPRESSION_PROGRESS = 0.0

Strategy = Literal["pattern_completion", "llm_reasoning"]


class HypothesisEngineInput(CommandInput):
    graph: str | None = Field(default=None, max_length=200)
    max_results: int | None = Field(default=None, ge=1, le=100, alias="maxResults")
    min_confidence: float | None = Field(default=None, ge=0, le=1, alias="minConfidence")
    gap_limit: int | None = Field(default=None, ge=1, le=200, alias="gapLimit")
    min_similarity: float | None = Field(default=None, ge=0, le=1, alias="minSimilarity")
    strategies: list[Strategy] | None = Field(default=None, max_length=2)
    dedup_threshold: float | None = Field(default=None, ge=0.5, le=0.99, alias="dedupThreshold")
    dedup_mode: Literal["reject", "flag", "merge"] | None = Field(default=None, alias="dedupMode")
    dedup_enabled: bool | None = Field(default=None, alias="dedupEnabled")
    si_weight: float | None = Field(default=None, ge=0, le=100, alias="siWeight")
    structural_weight: float | None = Field(default=None, ge=0, le=100, alias="structuralWeight")
    compression_weight: float | None = Field(default=None, ge=0, le=100, alias="compressionWeight")
    embeddings_available: bool | None = Field(default=None, alias="embeddingsAvailable")
    simulate: bool | None = None


# =============================================================================
# Interestingness dimension derivation
# =============================================================================


def _derive_interestingness_dimensions(
    proposal: dict[str, Any], graph_entity_count: int
) -> dict[str, float]:
    if not proposal.get("fullSimulationData"):
        return {
            "si": NEUTRAL_SI,
            "structuralNovelty": NEUTRAL_STRUCTURAL_NOVELTY,
            "compressionProgress": NEUTRAL_COMPRESSION_PROGRESS,
        }

    sim_data = proposal["fullSimulationData"]
    si = NEUTRAL_SI
    structural_novelty = compute_structural_novelty(sim_data)
    wl_entropy = (sim_data.get("wlEntropyDelta") or {}).get("data")
    wl_entropy = 0 if wl_entropy is None else wl_entropy
    compression_progress = compute_compression_progress(wl_entropy, graph_entity_count)
    return {
        "si": si,
        "structuralNovelty": structural_novelty,
        "compressionProgress": compression_progress,
    }


# =============================================================================
# Scoring functions (novelty / plausibility / testability)
# =============================================================================


def _compute_novelty(proposal: dict[str, Any]) -> float:
    if proposal["strategy"] == "llm_reasoning":
        novelty = 0.6 + (1 - proposal["confidence"]) * 0.4
    else:
        novelty = 1.0 - proposal["confidence"]
    return float(min(max(novelty, 0.1), 1.0))


def _compute_plausibility(proposal: dict[str, Any]) -> float:
    return float(min(max(proposal["confidence"], 0.1), 1.0))


def _compute_testability(proposal: dict[str, Any]) -> float:
    testability = 0.0
    if len(proposal["relations"]) > 0:
        testability += 0.3
    observation_bonus = min(len(proposal["entity"]["observations"]) * 0.2, 0.4)
    testability += observation_bonus
    if proposal.get("capabilityViolation"):
        testability += 0.3
    return min(max(testability, 0.1), 1.0)


# =============================================================================
# Summary builder
# =============================================================================


def _build_summary(
    gaps: list[dict[str, Any]],
    all_proposals: list[dict[str, Any]],
    filtered_proposals: list[dict[str, Any]],
    dedup_info: dict[str, Any],
    hypotheses: list[dict[str, Any]],
    duration_ms: int,
) -> str:
    lines: list[str] = []

    lines.append(f"Hypothesis Engine Complete ({duration_ms}ms)")
    lines.append("")

    lines.append(f"Semantic gaps detected: {len(gaps)}")

    pattern_count = sum(1 for p in all_proposals if p["strategy"] == "pattern_completion")
    llm_count = sum(1 for p in all_proposals if p["strategy"] == "llm_reasoning")
    lines.append(
        f"Proposals generated: {len(all_proposals)} (pattern: {pattern_count}, llm: {llm_count})"
    )

    removed = len(all_proposals) - len(filtered_proposals)
    lines.append(f"After confidence filter: {len(filtered_proposals)} (removed: {removed})")

    if dedup_info["duplicatesFound"] > 0:
        lines.append(
            f"After deduplication: {dedup_info['afterCount']} "
            f"(duplicates: {dedup_info['duplicatesFound']})"
        )
    else:
        lines.append(f"After deduplication: {dedup_info['afterCount']} (no duplicates found)")
    lines.append("")

    if len(hypotheses) == 0:
        lines.append("No hypotheses generated.")
    else:
        lines.append(f"Top {len(hypotheses)} hypothesis(es):")
        for i in range(min(len(hypotheses), 5)):
            h = hypotheses[i]
            interest_scores = h.get("interestingnessScores")
            interest_str = (
                f"{interest_scores['interestingness']:.2f}"
                if interest_scores is not None
                else "N/A"
            )
            lines.append(
                f"  {i + 1}. {h['entity']['name']} (overall: {h['overallScore']:.3f}, "
                f"I={interest_str}, "
                f"N={h['scores']['novelty']:.2f}, P={h['scores']['plausibility']:.2f}, "
                f"T={h['scores']['testability']:.2f})"
            )
        if len(hypotheses) > 5:
            lines.append(f"  ... and {len(hypotheses) - 5} more")

    return "\n".join(lines)


# =============================================================================
# Composite
# =============================================================================


def hypothesis_engine(params: HypothesisEngineInput, multi: MultiGraph) -> dict[str, Any]:
    start = time.perf_counter()
    graph = params.graph

    max_results = min(
        max(params.max_results if params.max_results is not None else DEFAULT_MAX_RESULTS, 1),
        MAX_MAX_RESULTS,
    )
    min_confidence = min(
        max(
            params.min_confidence if params.min_confidence is not None else DEFAULT_MIN_CONFIDENCE,
            0,
        ),
        1,
    )
    gap_limit = min(
        max(params.gap_limit if params.gap_limit is not None else DEFAULT_GAP_LIMIT, 1),
        MAX_GAP_LIMIT,
    )
    min_similarity = min(
        max(
            params.min_similarity if params.min_similarity is not None else DEFAULT_MIN_SIMILARITY,
            0,
        ),
        1,
    )
    strategies: list[Strategy] = (
        params.strategies if params.strategies is not None else ["pattern_completion"]
    )
    simulate = params.simulate if params.simulate is not None else False
    dedup_enabled = params.dedup_enabled is not False  # default true
    dedup_threshold = params.dedup_threshold
    dedup_mode = params.dedup_mode
    embeddings_available = (
        params.embeddings_available if params.embeddings_available is not None else False
    )

    # Flat interestingness weights -> nested object for compute_interestingness.
    weights: dict[str, float] = {}
    if params.si_weight is not None:
        weights["siWeight"] = params.si_weight
    if params.structural_weight is not None:
        weights["structuralWeight"] = params.structural_weight
    if params.compression_weight is not None:
        weights["compressionWeight"] = params.compression_weight
    interestingness_weights = weights or None

    state: dict[str, Any] = {
        "detectedGaps": [],
        "allProposals": [],
        "filteredProposals": [],
        "dedupedProposals": [],
        "dedupMatches": [],
    }

    # -- Section 1: gaps ----------------------------------------------------
    def _gaps() -> dict[str, Any]:
        gap_results = semantic_gaps(
            SemanticGapsInput.model_validate(
                {"limit": gap_limit, "minSimilarity": min_similarity, "graph": graph}
            ),
            multi,
        )
        state["detectedGaps"] = gap_results
        return {"count": len(gap_results), "gaps": gap_results}

    gaps_section = time_section(_gaps)

    # -- Section 2: proposals ----------------------------------------------
    def _proposals() -> dict[str, Any]:
        store = multi.get_store(graph)
        propose_result = propose_entities(
            store,
            {
                "limit": max_results * 3,  # over-generate so filtering has room
                "simulate": simulate,
                "strategies": strategies,
                "graph": graph,
            },
        )
        state["allProposals"] = propose_result["proposals"]
        return {
            "count": len(propose_result["proposals"]),
            "strategyCounts": propose_result["strategyCounts"],
        }

    proposals_section = time_section(_proposals)

    # -- Section 3: filter --------------------------------------------------
    def _filter() -> dict[str, Any]:
        before_count = len(state["allProposals"])
        state["filteredProposals"] = [
            p for p in state["allProposals"] if p["confidence"] >= min_confidence
        ]
        after_count = len(state["filteredProposals"])
        return {
            "beforeCount": before_count,
            "afterCount": after_count,
            "removedCount": before_count - after_count,
        }

    filter_section = time_section(_filter)

    # -- Section 4: dedup ---------------------------------------------------
    def _dedup() -> dict[str, Any]:
        if not dedup_enabled:
            state["dedupedProposals"] = state["filteredProposals"]
            return {
                "beforeCount": len(state["filteredProposals"]),
                "afterCount": len(state["filteredProposals"]),
                "duplicatesFound": 0,
                "threshold": dedup_threshold if dedup_threshold is not None else 0.85,
                "mode": dedup_mode if dedup_mode is not None else "reject",
                "matches": [],
            }

        store = multi.get_store(graph)
        # No embedding pipeline in this composite -> name-based dedup, which
        # keeps the gate deterministic.
        result = deduplicate_proposals(
            state["filteredProposals"],
            None,
            store,
            {"similarityThreshold": dedup_threshold, "mode": dedup_mode, "graphName": graph},
        )
        state["dedupedProposals"] = result["accepted"]
        state["dedupMatches"] = result["matches"]
        return {
            "beforeCount": result["beforeCount"],
            "afterCount": result["afterCount"],
            "duplicatesFound": len(result["matches"]),
            "threshold": result["threshold"],
            "mode": result["mode"],
            "matches": result["matches"],
        }

    dedup_section = time_section(_dedup)

    # -- Section 5: rank ----------------------------------------------------
    def _rank() -> dict[str, Any]:
        graph_entity_count = len(multi.get_store(graph).list_entities())

        scored: list[dict[str, Any]] = []
        for proposal in state["dedupedProposals"]:
            novelty = _compute_novelty(proposal)
            plausibility = _compute_plausibility(proposal)
            testability = _compute_testability(proposal)

            dimensions = _derive_interestingness_dimensions(proposal, graph_entity_count)
            interestingness = compute_interestingness(
                {
                    "si": dimensions["si"],
                    "structuralNovelty": dimensions["structuralNovelty"],
                    "compressionProgress": dimensions["compressionProgress"],
                    "weights": interestingness_weights,
                    "embeddingsAvailable": embeddings_available,
                }
            )
            overall_score = interestingness

            item: dict[str, Any] = {
                "entity": proposal["entity"],
                "relations": proposal["relations"],
                "rationale": proposal["rationale"],
                "confidence": proposal["confidence"],
                "scores": {
                    "novelty": novelty,
                    "plausibility": plausibility,
                    "testability": testability,
                },
                "interestingnessScores": {
                    "si": dimensions["si"],
                    "structuralNovelty": dimensions["structuralNovelty"],
                    "compressionProgress": dimensions["compressionProgress"],
                    "interestingness": interestingness,
                },
                "overallScore": min(max(overall_score, 0), 1),
                "strategy": proposal["strategy"],
            }
            if proposal.get("isDuplicate") is not None:
                item["isDuplicate"] = proposal["isDuplicate"]
            if proposal.get("duplicateOf"):
                item["duplicateOf"] = proposal["duplicateOf"]
            scored.append(item)

        # Stable descending sort; ties (all-zero in template mode) keep dedup order.
        scored.sort(key=lambda h: -h["overallScore"])
        return {"hypotheses": scored[:max_results]}

    rank_section = time_section(_rank)

    # -- Assemble -----------------------------------------------------------
    total_ms = round((time.perf_counter() - start) * 1000)
    sections = {
        "gaps": gaps_section,
        "proposals": proposals_section,
        "filter": filter_section,
        "dedup": dedup_section,
        "rank": rank_section,
    }
    composite = build_composite_result(sections, total_ms)

    rank_data = rank_section["data"]
    hypotheses: list[dict[str, Any]] = rank_data["hypotheses"] if rank_data is not None else []

    dedup_data = dedup_section["data"]
    dedup_info = {
        "afterCount": (
            dedup_data["afterCount"] if dedup_data is not None else len(state["filteredProposals"])
        ),
        "duplicatesFound": dedup_data["duplicatesFound"] if dedup_data is not None else 0,
    }
    summary = _build_summary(
        state["detectedGaps"],
        state["allProposals"],
        state["filteredProposals"],
        dedup_info,
        hypotheses,
        total_ms,
    )

    return {"composite": composite, "hypotheses": hypotheses, "summary": summary}
