"""Analogy-Transfer composite.

Generates novel entity proposals via CWSG (Copying with Substitution and
Generation) over a cross-domain structural mapping.

Fully deterministic from the CLI: ``temperature`` defaults to 0 and no
purpose/embedding options are exposed, so no LLM or embedding calls occur and
the output is deterministic. The result is the *raw* :func:`cwsg_transfer` dict
(a bespoke envelope — ``copiedRelations`` / ``substitutedRelations`` /
``proposals`` / ``systematicityExcluded`` / ``totalSourceRelations`` /
``temperature`` plus optional keys), NOT a :func:`build_composite_result`
envelope.

Wiring:
1. list ALL-status entities + relations as wire dicts;
2. ``map_cross_domain_concepts(entities, relations, sourceDomain, targetDomain)``;
3. ``cwsg_transfer(mapping, {temperature, allEntities, allRelations})``.

``map_cross_domain_concepts`` raises ``ValueError`` for a domain spec missing
``entityIds``/``entityType``, an empty domain, or an oversized (>100) domain;
these propagate for ``run_handler`` to classify (they are not caught here).
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from theloom.analysis.crossdomain import map_cross_domain_concepts
from theloom.analysis.cwsg import cwsg_transfer
from theloom.model import ALL_ENTITY_STATUSES, EntityFilter
from theloom.operations.common import CommandInput
from theloom.store.multigraph import MultiGraph


class AnalogyTransferInput(CommandInput):
    source_domain: dict[str, Any] = Field(alias="sourceDomain")
    target_domain: dict[str, Any] = Field(alias="targetDomain")
    temperature: float | None = Field(default=None, ge=0, le=1)
    graph: str | None = None


def analogy_transfer(params: AnalogyTransferInput, multi: MultiGraph) -> dict[str, Any]:
    """Map source/target domains structurally, then run CWSG transfer."""
    store = multi.get_store(params.graph)
    all_statuses = EntityFilter.model_validate({"statusFilter": list(ALL_ENTITY_STATUSES)})
    entities = [
        e.model_dump(by_alias=True, exclude_unset=True) for e in store.list_entities(all_statuses)
    ]
    relations = [r.model_dump(by_alias=True, exclude_unset=True) for r in store.list_relations()]
    mapping_result = map_cross_domain_concepts(
        entities, relations, params.source_domain, params.target_domain
    )
    options: dict[str, Any] = {
        "temperature": params.temperature if params.temperature is not None else 0,
        "allEntities": entities,
        "allRelations": relations,
    }
    return cwsg_transfer(mapping_result, options)
