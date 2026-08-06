"""Verified Extract composite.

Runs document extraction then a battery of graph-integrity checks over the
(possibly newly-extracted) graph: verification, consistency, credit cascade, and
contradiction detection. Five sections, each inside :func:`time_section`;
extraction runs first so downstream credit propagation can target the created
entities.

Template mode (no LLM): the extraction section fails deterministically, the other
four run over the existing graph — so the whole command is testable.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from theloom.composites.framework import run_composite
from theloom.operations.common import CommandInput
from theloom.operations.epistemic import (
    EpistemicQueryInput,
    PropagateCreditInput,
    contested_claims,
    propagate_credit,
)
from theloom.operations.extraction import ExtractFromDocumentsInput, extract_from_documents
from theloom.operations.verification import (
    GraphOnlyInput,
    VerifyGraphInput,
    check_consistency,
    verify_graph,
)
from theloom.store.multigraph import MultiGraph

DEFAULT_CREDIT_DELTA = 0.1
DEFAULT_CREDIT_DAMPING = 0.5
DEFAULT_CREDIT_MAX_DEPTH = 3


class VerifiedExtractInput(CommandInput):
    category: str | None = None
    document_id: str | None = Field(default=None, alias="documentId")
    query: str | None = None
    entity_types: list[str] | None = Field(default=None, alias="entityTypes")
    max_chunks: int | None = Field(default=None, gt=0, alias="maxChunks")
    model: str | None = None
    section_synthesis: str | None = Field(default=None, alias="sectionSynthesis")
    context_window_size: int | None = Field(default=None, gt=0, alias="contextWindowSize")
    focus: str | None = None
    dry_run: bool | None = Field(default=None, alias="dryRun")
    credit_delta: float | None = Field(default=None, ge=-1, le=1, alias="creditDelta")
    credit_damping_factor: float | None = Field(
        default=None, ge=0, le=1, alias="creditDampingFactor"
    )
    credit_max_depth: int | None = Field(default=None, gt=0, le=10, alias="creditMaxDepth")
    credit_dry_run: bool | None = Field(default=None, alias="creditDryRun")
    graph: str | None = None


def _guard_view(violations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"guard": v["code"], "message": v["message"]} for v in violations]


def verified_extract(params: VerifiedExtractInput, multi: MultiGraph) -> dict[str, Any]:
    graph = params.graph
    extracted_ids: list[str] = []

    def _extraction() -> dict[str, Any]:
        result = extract_from_documents(
            ExtractFromDocumentsInput.model_validate(
                {
                    "category": params.category,
                    "documentId": params.document_id,
                    "query": params.query,
                    "maxChunks": params.max_chunks,
                    "model": params.model,
                    "focus": params.focus,
                    "dryRun": params.dry_run,
                    "graph": graph,
                }
            ),
            multi,
        )
        documents = result.get("documents", [])
        for doc in documents:
            extracted_ids.extend(doc.get("createdEntityIds", []))
        return {
            "runId": result.get("runId"),
            "status": result.get("status"),
            "totalEntitiesCreated": result.get("totalEntitiesCreated"),
            "totalEntitiesMerged": result.get("totalEntitiesMerged"),
            "totalRelationsCreated": result.get("totalRelationsCreated"),
            "totalErrors": result.get("totalErrors"),
            "documents": [
                {
                    "documentId": d.get("documentId"),
                    "entitiesCreated": d.get("entitiesCreated"),
                    "relationsCreated": d.get("relationsCreated"),
                }
                for d in documents
            ],
        }

    def _verification() -> dict[str, Any]:
        result = verify_graph(VerifyGraphInput.model_validate({"graph": graph}), multi)
        tier1 = result["tier1"]
        tier2 = result["tier2"]
        return {
            "pass": result["pass"],
            "tier1": {
                "consistent": tier1["consistent"],
                "entityViolationCount": len(tier1["entityViolations"]),
                "relationViolationCount": len(tier1["relationViolations"]),
            },
            "tier2Skipped": result["tier2Skipped"],
            "tier2PropertyCount": len(tier2) if tier2 is not None else None,
        }

    def _consistency() -> dict[str, Any]:
        result = check_consistency(GraphOnlyInput(graph=graph), multi)
        return {
            "consistent": result["consistent"],
            "entitiesChecked": result["entitiesChecked"],
            "relationsChecked": result["relationsChecked"],
            "entityViolations": [
                {"entityId": ev["entityId"], "violations": _guard_view(ev["violations"])}
                for ev in result["entityViolations"]
            ],
            "relationViolations": [
                {"from": rv["from"], "to": rv["to"], "violations": _guard_view(rv["violations"])}
                for rv in result["relationViolations"]
            ],
        }

    def _credit_updates() -> dict[str, Any]:
        if not extracted_ids:
            return {"triggers": []}
        results = propagate_credit(
            PropagateCreditInput.model_validate(
                {
                    "entityIds": extracted_ids,
                    "delta": params.credit_delta
                    if params.credit_delta is not None
                    else DEFAULT_CREDIT_DELTA,
                    "dampingFactor": params.credit_damping_factor
                    if params.credit_damping_factor is not None
                    else DEFAULT_CREDIT_DAMPING,
                    "maxDepth": params.credit_max_depth
                    if params.credit_max_depth is not None
                    else DEFAULT_CREDIT_MAX_DEPTH,
                    "dryRun": params.credit_dry_run if params.credit_dry_run is not None else True,
                    "graph": graph,
                }
            ),
            multi,
        )
        return {
            "triggers": [
                {
                    "triggerId": r["triggerId"],
                    "triggerDelta": r["triggerDelta"],
                    "totalEntitiesAffected": r["totalEntitiesAffected"],
                    "maxDepthReached": r["maxDepthReached"],
                }
                for r in results
            ]
        }

    def _contradictions() -> dict[str, Any]:
        results = contested_claims(EpistemicQueryInput.model_validate({"graph": graph}), multi)
        return {
            "contestedClaims": [
                {
                    "entityId": r["entity"]["id"],
                    "entityName": r["entity"]["name"],
                    "supportCount": r["supportCount"],
                    "contradictCount": r["contradictCount"],
                }
                for r in results
            ]
        }

    return run_composite(
        [
            ("extraction", _extraction),
            ("verification", _verification),
            ("consistency", _consistency),
            ("creditUpdates", _credit_updates),
            ("contradictions", _contradictions),
        ]
    )
