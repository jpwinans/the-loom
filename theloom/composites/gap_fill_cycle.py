"""Gap Fill Cycle composite.

Automated gap-filling workflow:
detect semantic gaps, suggest relations (from a seed or derived from the first
gap), validate each suggestion against type constraints and graph invariants,
score interestingness and semantic consistency, and optionally auto-create the
relations that clear the gate.

Four sections, each inside :func:`time_section`:

1. ``gaps`` - semantic gap detection.
2. ``suggestions`` - relation suggestions for the seed (or the first gap's
   entityA, or ``[]`` / a raised error when gaps produced nothing).
3. ``validation`` - per-suggestion constraint + invariant status,
   interestingness score, semantic consistency, and gated auto-creation.
4. ``verification`` - final graph integrity check.

Template mode (this composite takes ``(params, multi)`` with no embedding
pipeline): semantic consistency degrades to ``{score: 0, status: "skipped"}``
and the interestingness score is always ``0`` (multiplicative ``SI * C * S``
with neutral simulation data forces ``C = S = 0``). That makes the whole command
deterministic. Threshold gating is therefore effectively disabled (semantic
consistency never passes); use ``autoCreate: true`` for ungated creation.

After :func:`build_composite_result`, the aggregate metadata is extended with
``committed`` / ``skipped`` commitment counts.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from pydantic import Field

from theloom.analysis.interestingness import (
    compute_compression_progress,
    compute_structural_novelty,
    compute_subjective_information_density,
)
from theloom.composites.framework import build_composite_result, time_section
from theloom.operations.common import CommandInput
from theloom.operations.relations import CreateRelationInput, create_relation
from theloom.operations.semantic import (
    SemanticGapsInput,
    SuggestRelationsInput,
    semantic_gaps,
    suggest_relations,
)
from theloom.operations.verification import (
    PropagateConstraintsInput,
    VerifyGraphInput,
    propagate_constraints,
    verify_graph,
)
from theloom.store.multigraph import MultiGraph

# Default semantic consistency threshold.
SEMANTIC_CONSISTENCY_THRESHOLD = 0.3


class GapFillCycleInput(CommandInput):
    # seedEntity is a plain string, NOT a uuid — an invalid value simply fails
    # suggest-relations inside its time_section.
    seed_entity: str | None = Field(default=None, alias="seedEntity")
    limit: int | None = Field(default=None, gt=0)
    min_similarity: float | None = Field(default=None, ge=0, le=1, alias="minSimilarity")
    auto_create: bool | None = Field(default=None, alias="autoCreate")
    commit_threshold: float | None = Field(default=None, ge=0, le=1, alias="commitThreshold")
    graph: str | None = None


def _build_neutral_simulation_data() -> dict[str, Any]:
    """Neutral simulate-change data.

    Only the fields read by :func:`compute_structural_novelty` are needed; each
    resolves to an empty/zero value so structural novelty computes to ``0``.
    """
    return {
        "centralityDelta": {"data": [], "durationMs": 0, "error": None},
        "componentCountReduction": {"data": 0, "durationMs": 0, "error": None},
        "newLoops": {"data": [], "durationMs": 0, "error": None},
    }


def _compute_multiplicative_interestingness(
    proposal_embedding: list[float] | None,
    existing_embeddings: list[list[float]],
    wl_entropy_delta: float,
    graph_entity_count: int,
    simulation_data: dict[str, Any],
) -> float:
    """Multiplicative composition ``I(h,G) = SI * C * S``.

    With neutral simulation data and ``wlEntropyDelta = 0``, ``C`` and ``S`` are
    both ``0`` so the score is always ``0`` in template mode.
    """
    si = compute_subjective_information_density(proposal_embedding, existing_embeddings)
    c = compute_compression_progress(wl_entropy_delta, graph_entity_count)
    s = compute_structural_novelty(simulation_data)
    return si * c * s


def gap_fill_cycle(params: GapFillCycleInput, multi: MultiGraph) -> dict[str, Any]:
    start = time.perf_counter()
    graph = params.graph
    auto_create = params.auto_create if params.auto_create is not None else False
    commit_threshold = params.commit_threshold

    counters = {"committed": 0, "skipped": 0}

    # -- Section 1: semantic gaps -------------------------------------------
    def _gaps() -> list[dict[str, Any]]:
        return semantic_gaps(
            SemanticGapsInput.model_validate(
                {"limit": params.limit, "minSimilarity": params.min_similarity, "graph": graph}
            ),
            multi,
        )

    gaps_section = time_section(_gaps)

    # -- Section 2: relation suggestions ------------------------------------
    def _suggest_for(entity_id: str) -> Callable[[], list[dict[str, Any]]]:
        def _run() -> list[dict[str, Any]]:
            return suggest_relations(
                SuggestRelationsInput.model_validate(
                    {
                        "entityId": entity_id,
                        "limit": params.limit,
                        "minSimilarity": params.min_similarity,
                        "graph": graph,
                    }
                ),
                multi,
            )

        return _run

    if params.seed_entity:
        suggestions_section = time_section(_suggest_for(params.seed_entity))
    elif gaps_section["data"]:
        derived_seed = gaps_section["data"][0]["entityA"]["id"]
        suggestions_section = time_section(_suggest_for(derived_seed))
    else:
        gaps_error = gaps_section["error"]
        if gaps_error is not None:

            def _fail_suggestions() -> list[dict[str, Any]]:
                raise RuntimeError(f"Cannot derive suggestions: gaps section failed ({gaps_error})")

            suggestions_section = time_section(_fail_suggestions)
        else:

            def _empty_suggestions() -> list[dict[str, Any]]:
                return []

            suggestions_section = time_section(_empty_suggestions)

    # -- Section 3: validation ----------------------------------------------
    def _try_create(suggestion: dict[str, Any], relation_type: str) -> bool:
        try:
            create_relation(
                CreateRelationInput.model_validate(
                    {
                        "from": suggestion["from"]["id"],
                        "to": suggestion["to"]["id"],
                        "relationType": relation_type,
                        "polarity": None,
                        "strength": "moderate",
                        "evidence": None,
                        "graph": graph,
                    }
                ),
                multi,
            )
            return True
        except Exception:  # noqa: BLE001 — creation failure degrades to skipped.
            return False

    def _validation() -> list[dict[str, Any]]:
        suggestion_list = suggestions_section["data"] or []
        if not suggestion_list:
            return []

        # One graph-level verification supplies the invariant status for all.
        graph_verification = verify_graph(VerifyGraphInput.model_validate({"graph": graph}), multi)
        entity_count = len(multi.get_store(graph).list_entities())

        proposals: list[dict[str, Any]] = []
        for suggestion in suggestion_list:
            relation_type = suggestion.get("suggestedRelationType") or "related_to"
            source_type = suggestion["from"]["entityType"]
            target_type = suggestion["to"]["entityType"]

            try:
                constraint_result = propagate_constraints(
                    PropagateConstraintsInput.model_validate(
                        {
                            "constraints": [
                                {
                                    "sourceType": source_type,
                                    "relationType": relation_type,
                                    "targetType": target_type,
                                }
                            ],
                            "graph": graph,
                        }
                    ),
                    multi,
                )
                constraint_status = "pass" if constraint_result["consistent"] else "fail"
            except Exception:  # noqa: BLE001 — invalid types degrade to a failed constraint.
                constraint_status = "fail"

            invariant_status = "pass" if graph_verification["pass"] else "fail"

            simulation_data = _build_neutral_simulation_data()
            interestingness_score = _compute_multiplicative_interestingness(
                None, [], 0.0, entity_count, simulation_data
            )
            # No embedding pipeline in this composite -> skipped.
            semantic_consistency: dict[str, Any] = {"score": 0, "status": "skipped"}

            created = False
            structural_pass = constraint_status == "pass" and invariant_status == "pass"

            if commit_threshold is not None:
                meets_threshold = interestingness_score >= commit_threshold
                consistency_passes = semantic_consistency["status"] == "pass"
                if structural_pass and meets_threshold and consistency_passes:
                    created = _try_create(suggestion, relation_type)
                    counters["committed" if created else "skipped"] += 1
                else:
                    counters["skipped"] += 1
            elif auto_create and structural_pass:
                created = _try_create(suggestion, relation_type)
                counters["committed" if created else "skipped"] += 1
            else:
                counters["skipped"] += 1

            proposals.append(
                {
                    "suggestion": suggestion,
                    "constraintStatus": constraint_status,
                    "invariantStatus": invariant_status,
                    "interestingnessScore": interestingness_score,
                    "semanticConsistency": semantic_consistency,
                    "created": created,
                }
            )
        return proposals

    validation_section = time_section(_validation)

    # -- Section 4: final verification --------------------------------------
    def _verification() -> dict[str, Any]:
        return verify_graph(VerifyGraphInput.model_validate({"graph": graph}), multi)

    verification_section = time_section(_verification)

    sections = {
        "gaps": gaps_section,
        "suggestions": suggestions_section,
        "validation": validation_section,
        "verification": verification_section,
    }
    total_ms = round((time.perf_counter() - start) * 1000)
    result = build_composite_result(sections, total_ms)
    result["metadata"]["committed"] = counters["committed"]
    result["metadata"]["skipped"] = counters["skipped"]
    return result
