"""Synthesis planner.

Focus defaults: narrow 1/20, balanced 2/50, broad 3/100. selectionConfig
carries entityGraphOrigin verbatim; in cross-graph mode the output layer must
render it as an empty object ({}).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from theloom.graph.analytics import connected_components
from theloom.graph.hydrate import hydrate_graph
from theloom.synthesis.decomposer import decompose_query
from theloom.synthesis.llm import SynthesisLlmClient
from theloom.synthesis.orderer import (
    assign_regions_to_sub_questions,
    compute_core_numbers,
    group_into_regions,
    order_regions,
)
from theloom.synthesis.selector import DocStore, HybridSearch, select_subgraph

Doc = dict[str, Any]


def _build_empty_plan(
    query: str, selection_config: Doc, start_time: float, ordering_metric: str
) -> Doc:
    return {
        "query": query,
        "subQuestions": [
            {"id": str(uuid.uuid4()), "text": query, "dependsOn": [], "assignedRegionIds": []}
        ],
        "regions": [],
        "entityCount": 0,
        "relationCount": 0,
        "estimatedComplexity": "simple",
        "orderingMetric": ordering_metric,
        "wasDecomposed": False,
        "metadata": {
            "planningTimeMs": int((time.time() - start_time) * 1000),
            "selectionConfig": selection_config,
            "anchorEntityIds": [],
        },
    }


def plan_synthesis(
    store: DocStore,
    *,
    query: str,
    focus: str | None = None,
    max_depth: int | None = None,
    max_entities: int | None = None,
    ordering_metric: str | None = None,
    llm_client: SynthesisLlmClient | None = None,
    hybrid_search: HybridSearch | None = None,
    entity_graph_origin: dict[str, str] | None = None,
    graph_count: int | None = None,
) -> Doc:
    start_time = time.time()
    focus = focus or "balanced"
    ordering_metric = ordering_metric or "core-number"

    selection_config: Doc = {
        "focus": focus,
        "maxDepth": (
            max_depth
            if max_depth is not None
            else (1 if focus == "narrow" else 2 if focus == "balanced" else 3)
        ),
        "maxEntities": (
            max_entities
            if max_entities is not None
            else (20 if focus == "narrow" else 50 if focus == "balanced" else 100)
        ),
    }
    if entity_graph_origin is not None:
        selection_config["entityGraphOrigin"] = entity_graph_origin
    if graph_count is not None:
        selection_config["graphCount"] = graph_count

    all_entities = store.list_entities()
    total_entities = len(all_entities)
    warnings: list[str] = []
    if total_entities > 0:
        embedded_count = sum(1 for e in all_entities if e.get("embeddingStatus") == "completed")
        if embedded_count == 0:
            warnings.append(
                f"No entity embeddings found ({total_entities} entities). "
                "Synthesis will use keyword search only. "
                "Run embed-entities to improve results."
            )
        elif embedded_count < total_entities * 0.5:
            warnings.append(
                f"Only {embedded_count}/{total_entities} entities have embeddings. "
                "Run embed-entities to improve synthesis quality."
            )

    selection = select_subgraph(query, store, selection_config, hybrid_search)
    if not selection["entities"]:
        empty = _build_empty_plan(query, selection_config, start_time, ordering_metric)
        empty["warnings"] = warnings
        return empty

    core_numbers = compute_core_numbers(selection["entities"], selection["relations"])
    graph = hydrate_graph(selection["entities"], selection["relations"])
    cluster_count = len(connected_components(graph))

    decomposition = decompose_query(
        {
            "query": query,
            "entityCount": len(selection["entities"]),
            "clusterCount": cluster_count,
            "entityNames": [e["name"] for e in selection["entities"]],
        },
        llm_client,
    )

    regions = group_into_regions(
        selection["entities"], selection["relations"], core_numbers, decomposition["subQuestions"]
    )
    ordered_regions = order_regions(
        regions, selection["entities"], selection["relations"], ordering_metric
    )
    assign_regions_to_sub_questions(ordered_regions, decomposition["subQuestions"])

    return {
        "query": query,
        "subQuestions": decomposition["subQuestions"],
        "regions": ordered_regions,
        "entityCount": len(selection["entities"]),
        "relationCount": len(selection["relations"]),
        "estimatedComplexity": decomposition["estimatedComplexity"],
        "orderingMetric": ordering_metric,
        "wasDecomposed": decomposition["wasDecomposed"],
        "metadata": {
            "planningTimeMs": int((time.time() - start_time) * 1000),
            "selectionConfig": selection_config,
            "anchorEntityIds": selection["anchorEntityIds"],
        },
        "warnings": warnings,
    }
