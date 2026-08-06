"""Interestingness scoring.

Four pure functions evaluating how interesting a proposed hypothesis is
relative to an existing knowledge graph:

- Subjective Information Density (SI): semantic novelty via k-NN cosine
  distance on embeddings. Neutral 0.5 when embeddings are unavailable.
- Compression Progress (C): whether adding a hypothesis reduces graph
  description length (WL fingerprint entropy), per Schmidhuber.
- Structural Novelty (S): topological impact of the proposed change,
  combining centrality delta, bridge detection and loop creation.
- Aggregate interestingness: a weighted average of SI, S and C.

All formulas, defaults, thresholds and clamps are fixed and explicit.
"""

from __future__ import annotations

import math
from typing import Any

from theloom.semantic.embed import cosine_similarity

# =============================================================================
# Constants
# =============================================================================

DEFAULT_K = 5
NEUTRAL_SCORE = 0.5

CENTRALITY_DELTA_WEIGHT = 0.4
BRIDGE_DETECTED_WEIGHT = 0.4
LOOP_CREATED_WEIGHT = 0.2

DEFAULT_WEIGHTS: dict[str, float] = {
    "siWeight": 1 / 3,
    "structuralWeight": 1 / 3,
    "compressionWeight": 1 / 3,
}


# =============================================================================
# Subjective Information Density
# =============================================================================


def compute_subjective_information_density(
    proposal_emb: list[float] | None,
    existing: list[list[float]],
    k: int = 5,
) -> float:
    """SI(h,G): 1 - mean cosine similarity to the k nearest existing embeddings,
    clamped to [0, 1]. Returns the neutral 0.5 when embeddings are unavailable."""
    if proposal_emb is None or len(existing) == 0:
        return NEUTRAL_SCORE

    effective_k = k if k is not None else DEFAULT_K

    similarities = [cosine_similarity(proposal_emb, other) for other in existing]
    similarities.sort(reverse=True)

    top_k = similarities[: min(effective_k, len(similarities))]
    mean_similarity = sum(top_k) / len(top_k)

    return max(0.0, min(1.0, 1 - mean_similarity))


# =============================================================================
# Compression Progress
# =============================================================================


def compute_compression_progress(wl_delta: float, n: int) -> float:
    """C(h,G): normalized WL-entropy reduction, clamped to [0, 1].

    rawProgress = -wl_delta (positive when entropy decreased);
    normalized by log2(n) and clamped."""
    raw_progress = -wl_delta

    if raw_progress <= 0:
        return 0.0

    max_expected_progress = math.log2(n) if n > 1 else 0.0
    if max_expected_progress <= 0:
        return 0.0

    return min(1.0, raw_progress / max_expected_progress)


# =============================================================================
# Structural Novelty
# =============================================================================


def compute_structural_novelty(sim_data: dict[str, Any]) -> float:
    """S(h,G): 0.4 * normalized centrality delta + 0.4 * bridge + 0.2 * loop.

    Reads the SectionResult ``.data`` fields of a simulate-change result,
    defaulting each to a safe empty/zero value."""
    centrality_entries = (sim_data.get("centralityDelta") or {}).get("data") or []
    component_reduction_data = (sim_data.get("componentCountReduction") or {}).get("data")
    component_reduction = 0 if component_reduction_data is None else component_reduction_data
    new_loops = (sim_data.get("newLoops") or {}).get("data") or []

    normalized_centrality_delta = 0.0
    if len(centrality_entries) > 0:
        sum_abs_delta = 0.0
        max_degree = 0.0
        for entry in centrality_entries:
            sum_abs_delta += abs(float(entry["after"]) - float(entry["before"]))
            max_degree = max(max_degree, float(entry["before"]), float(entry["after"]))
        if max_degree > 0:
            normalized_centrality_delta = min(1.0, sum_abs_delta / max_degree)

    bridge_detected = 1.0 if component_reduction > 0 else 0.0
    loop_created = 1.0 if len(new_loops) > 0 else 0.0

    return max(
        0.0,
        min(
            1.0,
            CENTRALITY_DELTA_WEIGHT * normalized_centrality_delta
            + BRIDGE_DETECTED_WEIGHT * bridge_detected
            + LOOP_CREATED_WEIGHT * loop_created,
        ),
    )


# =============================================================================
# Aggregate Interestingness
# =============================================================================


def compute_interestingness(args: dict[str, Any]) -> float:
    """Weighted average of SI, structural novelty and compression progress.

    args keys: ``si``, ``structuralNovelty``, ``compressionProgress``,
    optional ``weights`` (partial {siWeight, structuralWeight, compressionWeight}),
    and optional ``embeddingsAvailable`` (default True). When
    ``embeddingsAvailable`` is False the SI weight is dropped from the average.
    Raises ValueError if any weight is negative."""
    weights: dict[str, float] = {**DEFAULT_WEIGHTS, **(args.get("weights") or {})}

    if (
        weights["siWeight"] < 0
        or weights["structuralWeight"] < 0
        or weights["compressionWeight"] < 0
    ):
        raise ValueError("Interestingness weights must be non-negative")

    embeddings_available = args.get("embeddingsAvailable", True)
    effective_si_weight = 0.0 if embeddings_available is False else weights["siWeight"]

    total_weight = effective_si_weight + weights["structuralWeight"] + weights["compressionWeight"]

    if total_weight == 0:
        return 0.0

    score = (
        effective_si_weight * float(args["si"])
        + weights["structuralWeight"] * float(args["structuralNovelty"])
        + weights["compressionWeight"] * float(args["compressionProgress"])
    ) / total_weight

    return max(0.0, min(1.0, score))
