"""Semantic Landscape composite.

Semantic overview of a graph: semantic gaps, relation suggestions and semantic
neighbors (both need a ``seedEntity``), and category analysis (needs a
``category``). The seed/category sections degrade to :func:`failed_section`
when their parameter is absent.

The ``gaps``/``suggestions``/``neighbors`` sections rank by live query
embeddings; ``categoryAnalysis`` runs over stored chunk vectors (deterministic).
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import Field

from theloom.composites.framework import failed_section, run_composite, time_section
from theloom.operations.common import CommandInput
from theloom.operations.documents import AnalyzeCategoryInput, analyze_category
from theloom.operations.semantic import (
    SemanticGapsInput,
    SemanticNeighborsInput,
    SuggestRelationsInput,
    semantic_gaps,
    semantic_neighbors,
    suggest_relations,
)
from theloom.store.multigraph import MultiGraph


class SemanticLandscapeInput(CommandInput):
    seed_entity: str | None = Field(default=None, alias="seedEntity")
    category: str | None = None
    limit: int | None = Field(default=None, gt=0)
    min_similarity: float | None = Field(default=None, ge=0, le=1, alias="minSimilarity")
    graph: str | None = None


def semantic_landscape(params: SemanticLandscapeInput, multi: MultiGraph) -> dict[str, Any]:
    # Several sections execute before the runner is called; hand it the real
    # start so totalDurationMs covers them too.
    start = time.perf_counter()
    limit = params.limit
    min_similarity = params.min_similarity
    graph = params.graph
    seed = params.seed_entity

    def _gaps() -> Any:
        return semantic_gaps(
            SemanticGapsInput.model_validate(
                {"limit": limit, "minSimilarity": min_similarity, "graph": graph}
            ),
            multi,
        )

    if seed:

        def _suggestions() -> Any:
            return suggest_relations(
                SuggestRelationsInput.model_validate(
                    {
                        "entityId": seed,
                        "limit": limit,
                        "minSimilarity": min_similarity,
                        "graph": graph,
                    }
                ),
                multi,
            )

        def _neighbors() -> Any:
            return semantic_neighbors(
                SemanticNeighborsInput.model_validate(
                    {
                        "entityId": seed,
                        "limit": limit,
                        "minSimilarity": min_similarity,
                        "graph": graph,
                    }
                ),
                multi,
            )

        suggestions_section = time_section(_suggestions)
        neighbors_section = time_section(_neighbors)
    else:
        suggestions_section = failed_section("seedEntity required for suggest-relations")
        neighbors_section = failed_section("seedEntity required for semantic-neighbors")

    if params.category:

        def _category() -> Any:
            return analyze_category(
                AnalyzeCategoryInput.model_validate({"category": params.category}), multi
            )

        category_section = time_section(_category)
    else:
        category_section = failed_section("category parameter required for analyze-category")

    return run_composite(
        [
            ("gaps", _gaps),
            ("suggestions", suggestions_section),
            ("neighbors", neighbors_section),
            ("categoryAnalysis", category_section),
        ],
        start=start,
    )
