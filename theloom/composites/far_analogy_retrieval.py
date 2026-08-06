"""Far-Analogy Retrieval composite.

Chains the full far-analogy pipeline into one CLI call:

1. **Fingerprint** — component signatures via WL hashing (plus semantic
   signatures when entity vectors exist).
2. **Match** — far-analogy candidates (structurally similar, semantically
   different components).
3. **Slip** — concept slippage on candidate source entities (creative
   substitutions).
4. **Transfer** — CWSG analogy transfer -> novel entity proposals.
5. **Score** — rank proposals by interestingness (blended with absence surprise).

Each section runs inside :func:`time_section`; downstream sections that lack a
prerequisite degrade to :func:`failed_section`. Every analysis primitive is
imported from the already-built ``theloom.analysis`` layer — nothing is rebuilt.

Determinism notes:
- The CLI never supplies ``explorationState`` (the ``explorationBoosted`` /
  ``bridgingBoost`` inputs are inert), so the BridgingPotential-directed boost
  and the trigger-candidate fast-path are dead from the CLI. ``explorationBoosted``
  is always ``False`` and ``bridgingBoostApplied`` is always ``0``.
- With **no entity vectors** the semantic path is skipped and Match falls back to
  structural Jaccard far-analogy candidates — fully deterministic.
- With entity vectors present, semantic signatures are built from the stored
  entity vectors and Match ranks pairs by sliced-Wasserstein distance
  (``find_semantic_far_analogy_candidates``) — rank-only (the ranking is stable,
  absolute scores are not).
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from pydantic import Field

from theloom.analysis.adaptability import assess_transfer_adaptability
from theloom.analysis.component_signatures import (
    compare_component_signatures,
    compute_all_component_signatures,
    find_far_analogy_candidates,
)
from theloom.analysis.crossdomain import map_cross_domain_concepts
from theloom.analysis.cwsg import cwsg_transfer
from theloom.analysis.interestingness import compute_interestingness
from theloom.analysis.sliced_wasserstein import find_semantic_far_analogy_candidates
from theloom.analysis.slippage import find_concept_slippages
from theloom.composites.framework import (
    SectionResult,
    failed_section,
    run_composite,
    time_section,
)
from theloom.graph.analytics import connected_components
from theloom.graph.hydrate import LoomGraph, hydrate_graph
from theloom.model import ALL_ENTITY_STATUSES, EntityFilter
from theloom.operations.common import CommandInput
from theloom.store.multigraph import MultiGraph

DEFAULT_MAX_CANDIDATES = 5
DEFAULT_MIN_STRUCTURAL_SIMILARITY = 0.3
DEFAULT_SLIPPAGE_TEMPERATURE = 0.5
DEFAULT_MAX_PROPOSALS = 10
HASH_DIGEST_LENGTH = 16


class FarAnalogyRetrievalInput(CommandInput):
    """Input schema for ``far-analogy-retrieval`` (cliAllowEmpty=True).

    The handler applies defaults for absent optionals. ``exploration_boosted``
    and ``bridging_boost`` are declared for schema completeness but inert from
    the CLI (no ``explorationState``).
    """

    graph: str | None = Field(default=None, max_length=200)
    max_candidates: int | None = Field(default=None, ge=1, le=50, alias="maxCandidates")
    min_structural_similarity: float | None = Field(
        default=None, ge=0, le=1, alias="minStructuralSimilarity"
    )
    slippage_temperature: float | None = Field(
        default=None, ge=0, le=1, alias="slippageTemperature"
    )
    max_proposals: int | None = Field(default=None, ge=1, le=100, alias="maxProposals")
    use_semantic_fingerprint: bool | None = Field(default=None, alias="useSemanticFingerprint")
    purpose: str | None = Field(default=None, max_length=10000)
    exploration_boosted: bool | None = Field(default=None, alias="explorationBoosted")
    bridging_boost: float | None = Field(default=None, ge=0, le=1, alias="bridgingBoost")


def _component_id(sorted_ids: list[str]) -> str:
    """Deterministic componentId: sha256 of the comma-joined sorted entity IDs,
    truncated to 16 hex chars (matches ``compute_component_signature``)."""
    return hashlib.sha256(",".join(sorted_ids).encode("utf-8")).hexdigest()[:HASH_DIGEST_LENGTH]


def _semantic_component_signatures(
    graph: LoomGraph, vectors: dict[str, list[float]]
) -> list[dict[str, Any]]:
    """Semantic signatures from stored entity vectors, one matrix row per entity
    that has a vector. The shape is what ``find_semantic_far_analogy_candidates``
    can consume. A simpler stand-in for a full multi-scale semantic
    fingerprint."""
    signatures: list[dict[str, Any]] = []
    for component_ids in connected_components(graph):
        matrix: list[list[float]] = []
        entity_ids: list[str] = []
        for node_id in component_ids:
            vector = vectors.get(node_id)
            if vector is not None:
                matrix.append(vector)
                entity_ids.append(node_id)
        signatures.append(
            {
                "componentId": _component_id(sorted(component_ids)),
                "entityCount": len(component_ids),
                "signatureMatrix": matrix,
                "entityIds": entity_ids,
            }
        )
    signatures.sort(key=lambda s: (-s["entityCount"], s["componentId"]))
    return signatures


def far_analogy_retrieval(params: FarAnalogyRetrievalInput, multi: MultiGraph) -> dict[str, Any]:
    """Run the far-analogy retrieval pipeline; return the composite envelope plus
    the ranked proposals, a human summary, and exploration feedback."""
    start = time.perf_counter()

    max_candidates = (
        params.max_candidates if params.max_candidates is not None else DEFAULT_MAX_CANDIDATES
    )
    min_structural = (
        params.min_structural_similarity
        if params.min_structural_similarity is not None
        else DEFAULT_MIN_STRUCTURAL_SIMILARITY
    )
    slippage_temperature = (
        params.slippage_temperature
        if params.slippage_temperature is not None
        else DEFAULT_SLIPPAGE_TEMPERATURE
    )
    max_proposals = (
        params.max_proposals if params.max_proposals is not None else DEFAULT_MAX_PROPOSALS
    )
    use_semantic_fp = (
        params.use_semantic_fingerprint if params.use_semantic_fingerprint is not None else True
    )

    shared: dict[str, Any] = {
        "signatures_result": None,
        "semantic_signatures": None,
        "all_entities": [],
        "all_relations": [],
        "component_entity_map": {},  # componentId -> entity names
        "component_entity_id_map": {},  # componentId -> entity ids
        "candidates": [],
        "collected_proposals": [],
        "candidate_proposal_counts": {},  # candidate index -> proposal count
        "absence_surprise_results": [],
        "absence_surprise_duration_ms": 0.0,
    }

    # -- Section 1: Fingerprint -------------------------------------------------
    def _fingerprint() -> dict[str, Any]:
        store = multi.get_store(params.graph)
        all_statuses = EntityFilter.model_validate({"statusFilter": list(ALL_ENTITY_STATUSES)})
        shared["all_entities"] = [
            e.model_dump(by_alias=True, exclude_unset=True)
            for e in store.list_entities(all_statuses)
        ]
        shared["all_relations"] = [
            r.model_dump(by_alias=True, exclude_unset=True) for r in store.list_relations()
        ]

        graph = hydrate_graph(shared["all_entities"], shared["all_relations"])
        shared["signatures_result"] = compute_all_component_signatures(graph)

        if graph.order > 0:
            for component_ids in connected_components(graph):
                component_id = _component_id(sorted(component_ids))
                shared["component_entity_map"][component_id] = [
                    graph.node_docs[node_id]["name"] for node_id in component_ids
                ]
                shared["component_entity_id_map"][component_id] = list(component_ids)

        # Semantic signatures only when requested AND entity vectors exist.
        if use_semantic_fp:
            vectors = store.get_entity_vectors()
            if vectors:
                shared["semantic_signatures"] = _semantic_component_signatures(graph, vectors)

        sig = shared["signatures_result"]
        return {
            "componentCount": sig["componentCount"],
            "signatureCount": len(sig["signatures"]),
            "globalHashDimensionality": len(sig["globalHashOrder"]),
        }

    fingerprint = time_section(_fingerprint)

    # -- Section 2: Match -------------------------------------------------------
    sig_result = shared["signatures_result"]
    if sig_result is None or len(sig_result["signatures"]) < 2:
        match = failed_section("Fewer than 2 component signatures available for comparison")
    else:

        def _match() -> dict[str, Any]:
            sigs = shared["signatures_result"]
            semantic = shared["semantic_signatures"]
            if semantic is not None and len(semantic) >= 2:
                # Semantic path: rank by Wasserstein distance, then map
                # each semantic candidate back to its structural signatures so the
                # downstream slip/transfer sections keep working on componentIds.
                semantic_candidates = find_semantic_far_analogy_candidates(
                    semantic, {"topN": max_candidates}
                )
                struct_sig_map = {s["componentId"]: s for s in sigs["signatures"]}
                for sc in semantic_candidates:
                    source_sig = struct_sig_map.get(sc["sourceComponent"]["componentId"])
                    target_sig = struct_sig_map.get(sc["targetComponent"]["componentId"])
                    if source_sig is not None and target_sig is not None:
                        shared["candidates"].append(
                            {
                                "sourceComponent": source_sig,
                                "targetComponent": target_sig,
                                "structuralSimilarity": compare_component_signatures(
                                    source_sig, target_sig
                                ),
                                "semanticDissimilarity": sc["semanticDistance"],
                                "farAnalogyScore": sc["farAnalogyScore"],
                            }
                        )
            else:
                # Structural fallback: Jaccard over component name tokens.
                shared["candidates"] = find_far_analogy_candidates(
                    sigs["signatures"],
                    {
                        "topN": max_candidates,
                        "minStructuralSimilarity": min_structural,
                        "componentEntities": shared["component_entity_map"],
                    },
                )
            return {
                "candidateCount": len(shared["candidates"]),
                "candidates": [
                    {
                        "sourceComponentId": c["sourceComponent"]["componentId"],
                        "targetComponentId": c["targetComponent"]["componentId"],
                        "farAnalogyScore": c["farAnalogyScore"],
                    }
                    for c in shared["candidates"]
                ],
            }

        match = time_section(_match)

    # -- Section 3: Slip --------------------------------------------------------
    if not shared["candidates"]:
        slip = failed_section("No far-analogy candidates to run slippage on")
    else:

        def _slip() -> dict[str, Any]:
            pairs_attempted = 0
            pairs_with_slippages = 0
            total_slippage_candidates = 0
            for candidate in shared["candidates"]:
                source_entity_ids = shared["component_entity_id_map"].get(
                    candidate["sourceComponent"]["componentId"], []
                )
                pairs_attempted += 1
                pair_has_slippage = False
                for entity_id in source_entity_ids:
                    try:
                        slippage_result = find_concept_slippages(
                            shared["all_entities"],
                            shared["all_relations"],
                            entity_id,
                            {"temperature": slippage_temperature},
                        )
                    except Exception:
                        continue
                    if slippage_result["candidates"]:
                        pair_has_slippage = True
                        total_slippage_candidates += len(slippage_result["candidates"])
                if pair_has_slippage:
                    pairs_with_slippages += 1
            return {
                "pairsAttempted": pairs_attempted,
                "pairsWithSlippages": pairs_with_slippages,
                "totalSlippageCandidates": total_slippage_candidates,
            }

        slip = time_section(_slip)

    # -- Section 4: Transfer ----------------------------------------------------
    if not shared["candidates"]:
        transfer = failed_section("No far-analogy candidates for transfer")
    else:

        def _transfer() -> dict[str, Any]:
            pairs_attempted = 0
            proposals_generated = 0
            for i, candidate in enumerate(shared["candidates"]):
                source_entity_ids = shared["component_entity_id_map"].get(
                    candidate["sourceComponent"]["componentId"], []
                )
                target_entity_ids = shared["component_entity_id_map"].get(
                    candidate["targetComponent"]["componentId"], []
                )
                if not source_entity_ids or not target_entity_ids:
                    continue
                pairs_attempted += 1
                try:
                    mapping_result = map_cross_domain_concepts(
                        shared["all_entities"],
                        shared["all_relations"],
                        {"entityIds": source_entity_ids},
                        {"entityIds": target_entity_ids},
                    )
                    transfer_start = time.perf_counter()
                    transfer_result = cwsg_transfer(
                        mapping_result,
                        {
                            "temperature": slippage_temperature,
                            "allEntities": shared["all_entities"],
                            "allRelations": shared["all_relations"],
                            "purpose": params.purpose,
                            "computeAbsenceSurprise": True,
                        },
                    )
                    transfer_end = time.perf_counter()
                except Exception:
                    shared["candidate_proposal_counts"][i] = 0
                    continue

                absence = transfer_result.get("absenceSurprise")
                if absence is not None:
                    shared["absence_surprise_results"].append(absence)
                    shared["absence_surprise_duration_ms"] += (transfer_end - transfer_start) * 1000
                per_proposal_absence = absence.get("overallScore") if absence is not None else None

                pair_proposal_count = 0
                for proposal in transfer_result["proposals"]:
                    if per_proposal_absence is not None:
                        proposal["absenceSurpriseScore"] = per_proposal_absence
                    shared["collected_proposals"].append(proposal)
                    proposals_generated += 1
                    pair_proposal_count += 1
                shared["candidate_proposal_counts"][i] = pair_proposal_count
            return {
                "pairsAttempted": pairs_attempted,
                "proposalsGenerated": proposals_generated,
            }

        transfer = time_section(_transfer)

    # -- Section 4.5: Adaptability (default on) ---------------------------------
    def _adaptability() -> dict[str, Any]:
        proposals = shared["collected_proposals"]
        if not proposals:
            return {
                "proposalsAssessed": 0,
                "proposalsAccepted": 0,
                "proposalsWarned": 0,
                "proposalsRejected": 0,
            }

        entity_type_map = {e["id"]: e["entityType"] for e in shared["all_entities"]}
        existing_entity_ids = {e["id"] for e in shared["all_entities"]}
        # No per-proposal substituted relations at this stage -> empty list.
        results = assess_transfer_adaptability(
            proposals,
            [],
            shared["all_relations"],
            existing_entity_ids,
            entity_type_map,
        )

        accepted = warned = rejected = 0
        for i in range(len(proposals)):
            result = results[i]
            proposals[i] = {
                **proposals[i],
                "adaptabilityResult": {
                    "overallScore": result["overallScore"],
                    "decision": result["decision"],
                },
            }
            if result["decision"] == "reject":
                rejected += 1
            elif result["decision"] == "warn":
                warned += 1
            else:
                accepted += 1
        return {
            "proposalsAssessed": len(results),
            "proposalsAccepted": accepted,
            "proposalsWarned": warned,
            "proposalsRejected": rejected,
        }

    adaptability = time_section(_adaptability)

    # -- Section 5: Score -------------------------------------------------------
    def _score() -> dict[str, Any]:
        proposals = shared["collected_proposals"]
        if not proposals:
            return {"proposalsScored": 0, "proposalsReturned": 0}

        scored: list[dict[str, Any]] = []
        for proposal in proposals:
            relation_count = len(proposal["relations"])
            structural_novelty = min(
                1.0,
                (1 - proposal["confidence"]) * 0.5 + min(relation_count * 0.15, 0.5),
            )
            interestingness_score = compute_interestingness(
                {
                    "si": 0.5,
                    "structuralNovelty": structural_novelty,
                    "compressionProgress": 0,
                    "embeddingsAvailable": False,
                }
            )
            raw_absence = proposal.get("absenceSurpriseScore")
            proposal_absence = raw_absence if raw_absence is not None else 0
            blended_score = (
                interestingness_score * 0.9 + proposal_absence * 0.1
                if proposal_absence > 0
                else interestingness_score
            )
            scored.append({"proposal": proposal, "score": blended_score})

        scored.sort(key=lambda s: -s["score"])
        truncated = scored[:max_proposals]
        shared["collected_proposals"] = [
            {**s["proposal"], "confidence": s["score"]} for s in truncated
        ]
        return {
            "proposalsScored": len(scored),
            "proposalsReturned": len(shared["collected_proposals"]),
        }

    score = time_section(_score)

    # -- Assemble result --------------------------------------------------------
    absence_results = shared["absence_surprise_results"]
    absence_surprise_section: SectionResult | None = None
    if absence_results:
        absence_surprise_section = {
            "data": {
                "overallScore": max(r["overallScore"] for r in absence_results),
                "meanScore": sum(r["meanScore"] for r in absence_results) / len(absence_results),
                "schemaAbsenceCount": sum(len(r["schemaAbsences"]) for r in absence_results),
                "instanceAbsenceCount": sum(len(r["instanceAbsences"]) for r in absence_results),
            },
            "durationMs": round(shared["absence_surprise_duration_ms"]),
            "error": None,
        }

    section_specs: list[tuple[str, SectionResult]] = [
        ("fingerprint", fingerprint),
        ("match", match),
        ("slip", slip),
        ("transfer", transfer),
        ("score", score),
    ]
    if absence_surprise_section is not None:
        section_specs.append(("absenceSurprise", absence_surprise_section))
    section_specs.append(("adaptability", adaptability))

    composite = run_composite(section_specs, start=start)
    total_ms = composite["metadata"]["totalDurationMs"]
    sections = composite["result"]
    summary = _build_summary(sections, shared["collected_proposals"], total_ms)

    candidate_feedback = [
        {
            "sourceComponentId": candidate["sourceComponent"]["componentId"],
            "targetComponentId": candidate["targetComponent"]["componentId"],
            # Bridging potential is never computed from the CLI (no explorationState).
            "highBridgingRegion": False,
            "bridgingPotentialScore": None,
            "producedProposals": shared["candidate_proposal_counts"].get(i, 0) > 0,
            "proposalCount": shared["candidate_proposal_counts"].get(i, 0),
        }
        for i, candidate in enumerate(shared["candidates"])
    ]

    return {
        "composite": composite,
        "proposals": shared["collected_proposals"],
        "summary": summary,
        "explorationFeedback": {
            "explorationBoosted": False,
            "bridgingBoostApplied": 0,
            "candidateFeedback": candidate_feedback,
        },
    }


def _build_summary(
    sections: dict[str, SectionResult], proposals: list[dict[str, Any]], duration_ms: int
) -> str:
    """Human-readable pipeline summary."""
    lines: list[str] = []

    lines.append(f"Far-Analogy Retrieval Complete ({duration_ms}ms)")
    lines.append("")

    fingerprint = sections["fingerprint"]
    if fingerprint["data"] is not None:
        fp = fingerprint["data"]
        lines.append(
            f"Components: {fp['componentCount']}, Signatures: {fp['signatureCount']}, "
            f"Dimensions: {fp['globalHashDimensionality']}"
        )
    else:
        lines.append(f"Fingerprint: failed ({fingerprint['error']})")

    match = sections["match"]
    if match["data"] is not None:
        lines.append(f"Far-analogy candidates: {match['data']['candidateCount']}")
    else:
        lines.append(f"Match: skipped ({match['error']})")

    slip = sections["slip"]
    if slip["data"] is not None:
        sl = slip["data"]
        lines.append(
            f"Slippage: {sl['pairsAttempted']} pairs attempted, "
            f"{sl['pairsWithSlippages']} with slippages, "
            f"{sl['totalSlippageCandidates']} candidates"
        )
    else:
        lines.append(f"Slippage: skipped ({slip['error']})")

    transfer = sections["transfer"]
    if transfer["data"] is not None:
        lines.append(
            f"Transfer: {transfer['data']['proposalsGenerated']} proposals generated "
            f"from {transfer['data']['pairsAttempted']} pairs"
        )
    else:
        lines.append(f"Transfer: skipped ({transfer['error']})")

    score = sections["score"]
    if score["data"] is not None:
        lines.append(
            f"Score: {score['data']['proposalsScored']} scored, "
            f"{score['data']['proposalsReturned']} returned"
        )

    lines.append("")

    if not proposals:
        lines.append("No proposals generated.")
    else:
        lines.append(f"Top {len(proposals)} proposal(s):")
        for i in range(min(len(proposals), 5)):
            p = proposals[i]
            lines.append(
                f"  {i + 1}. {p['entity']['name']} "
                f"(score: {p['confidence']:.3f}, strategy: {p['strategy']})"
            )
        if len(proposals) > 5:
            lines.append(f"  ... and {len(proposals) - 5} more")

    return "\n".join(lines)
