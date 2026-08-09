"""Provenance Audit composite.

Full provenance audit for one entity: the provenance chain, claims derived from
source entities in that chain,
inferred claims filtered to the chain, and a dry-run credit-cascade preview.
Every section runs through :func:`run_composite`'s :func:`time_section`.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from theloom.composites.framework import run_composite
from theloom.operations.common import CommandInput, UuidStr
from theloom.operations.epistemic import (
    ClaimsFromSourceInput,
    PropagateCreditInput,
    ProvenanceChainInput,
    TypedEpistemicInput,
    claims_from_source,
    inferred_claims,
    propagate_credit,
    provenance_chain,
)
from theloom.store.multigraph import MultiGraph

DEFAULT_CASCADE_DELTA = -0.5


class ProvenanceAuditInput(CommandInput):
    entity_id: UuidStr = Field(alias="entityId")
    max_depth: int | None = Field(default=None, ge=1, alias="maxDepth")
    delta: float | None = None
    graph: str | None = None


def _confidence_score(entity: dict[str, Any]) -> Any:
    confidence = entity.get("confidence")
    return confidence.get("score") if confidence else None


def provenance_audit(params: ProvenanceAuditInput, multi: MultiGraph) -> dict[str, Any]:
    graph = params.graph
    entity_id = params.entity_id

    # State threaded across sections (sections run sequentially).
    state: dict[str, Any] = {"chain_data": None}

    def _provenance_chain() -> list[dict[str, Any]]:
        chain = provenance_chain(
            ProvenanceChainInput.model_validate(
                {"entityId": entity_id, "maxDepth": params.max_depth, "graph": graph}
            ),
            multi,
        )
        result = [
            {
                "entityId": node["entity"]["id"],
                "entityName": node["entity"]["name"],
                "entityType": node["entity"]["entityType"],
                "depth": node["depth"],
                "confidence": _confidence_score(node["entity"]),
            }
            for node in chain["items"]
        ]
        state["chain_data"] = result
        return result

    def _derived_claims() -> list[dict[str, Any]]:
        source_ids = [
            entry["entityId"]
            for entry in (state["chain_data"] or [])
            if entry["entityType"] == "source"
        ]
        if not source_ids:
            return []
        seen: set[str] = set()
        results: list[dict[str, Any]] = []
        for source_id in source_ids:
            claims = claims_from_source(
                ClaimsFromSourceInput.model_validate({"sourceId": source_id, "graph": graph}), multi
            )
            for entity in claims["items"]:
                if entity["id"] not in seen:
                    seen.add(entity["id"])
                    results.append(
                        {
                            "entityId": entity["id"],
                            "entityName": entity["name"],
                            "entityType": entity["entityType"],
                            "confidence": _confidence_score(entity),
                        }
                    )
        return results

    def _inferred_claims() -> list[dict[str, Any]]:
        all_inferred = inferred_claims(TypedEpistemicInput.model_validate({"graph": graph}), multi)
        chain_ids = {entry["entityId"] for entry in (state["chain_data"] or [])}
        return [
            {
                "entityId": entity["id"],
                "entityName": entity["name"],
                "entityType": entity["entityType"],
                "basis": (entity.get("confidence") or {}).get("basis"),
            }
            for entity in all_inferred["items"]
            if entity["id"] in chain_ids
        ]

    def _cascade_preview() -> dict[str, Any]:
        return propagate_credit(
            PropagateCreditInput.model_validate(
                {
                    "entityIds": [entity_id],
                    "delta": params.delta if params.delta is not None else DEFAULT_CASCADE_DELTA,
                    "dryRun": True,
                    "graph": graph,
                }
            ),
            multi,
        )

    return run_composite(
        [
            ("provenanceChain", _provenance_chain),
            ("derivedClaims", _derived_claims),
            ("inferredClaims", _inferred_claims),
            ("cascadePreview", _cascade_preview),
        ]
    )
