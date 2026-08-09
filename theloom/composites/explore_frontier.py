"""Explore-Frontier composite.

Aggregates the three exploration foraging signals (AgeStaleness,
BridgingPotential, CoverageGap) into per-region composite scores, ranks regions
by exploration priority, and adds MVT diminishing-returns advice and
anti-pattern warnings. Regions are the connected components computed at query
time via ``detect_components``.

Deterministic apart from the CoverageGap signal, which is embedding-ranked:
when the store has no entity vectors the signal degrades to a failed section
(``coverage_gap_unavailable``), which is exactly what fires the
``embedding-unavailable`` anti-pattern warning.

The exploration state is an in-memory, always-zeroed
:class:`ExplorationStateStore` (no sidecar file), so RUN guards against real
zeroed region states rather than being skipped. Every section runs inside
:func:`time_section`.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from theloom.composites.framework import run_composite, time_section
from theloom.exploration import (
    DEFAULT_TOP_K,
    MAX_DISPLAY_NAMES,
    ExplorationStateStore,
    compute_age_staleness,
    compute_bridging_potential,
    compute_composite_score,
    compute_coverage_gap,
    compute_ucb,
    coverage_gap_unavailable,
    embeddings_available,
    region_key,
    run_guards,
    run_mvt_section,
)
from theloom.exploration.composite_signals import ALL_STALE_THRESHOLD
from theloom.operations.analysis import DetectComponentsInput, detect_components
from theloom.operations.common import CommandInput
from theloom.operations.notices import notice, with_notices
from theloom.store.multigraph import MultiGraph

Doc = dict[str, Any]


def _world_partial_notices(params: ExploreFrontierInput) -> list[Doc]:
    """``WORLD_PROJECTION_PARTIAL`` (tension (a), Part 5): the CoverageGap
    signal (``theloom.exploration.coverage_gap``) is embedding-ranked --
    entity vectors are a direct Cypher property write outside the event log
    a world's overlay replays, so inside a fork this signal only ever sees
    vectors embedded within that fork, never ones its parent already had
    embedded. AgeStaleness and BridgingPotential are structural and fork
    correctly; CoverageGap alone is degraded."""
    if params.world in (None, "main"):
        return []
    return [
        notice(
            "WORLD_PROJECTION_PARTIAL",
            f"World '{params.world}' does not inherit its parent's embeddings — the CoverageGap "
            "signal reflects only entities embedded inside this world, not the ones inherited "
            "from its parent.",
        )
    ]


class ExploreFrontierInput(CommandInput):
    """Input for the explore-frontier composite."""

    graph: str | None = Field(default=None, max_length=200)
    top_k: int | None = Field(default=None, ge=1, le=100, alias="topK")
    include_mvt: bool | None = Field(default=None, alias="includeMvt")
    include_anti_patterns: bool | None = Field(default=None, alias="includeAntiPatterns")
    purpose: str | None = Field(default=None, max_length=10000)


def explore_frontier(params: ExploreFrontierInput, multi: MultiGraph) -> Doc:
    """Rank graph regions by exploration priority."""
    start = time.perf_counter()
    top_k = params.top_k if params.top_k is not None else DEFAULT_TOP_K
    include_mvt = params.include_mvt is not False
    include_anti_patterns = params.include_anti_patterns is not False

    store = multi.get_store(params.graph)
    entity_objs = store.list_entities()
    entities: list[Doc] = [e.model_dump(by_alias=True, exclude_unset=True) for e in entity_objs]

    # Short-circuit: empty graph (hand-built sections, not time_section).
    if not entities:
        empty_regions: Doc = {"data": [], "durationMs": 0, "error": None}
        empty_mvt: Doc = {"data": [], "durationMs": 0, "error": None}
        empty_anti_patterns: Doc = {
            "data": (
                [{"type": "empty-graph", "message": "Graph has no entities", "severity": "info"}]
                if include_anti_patterns
                else []
            ),
            "durationMs": 0,
            "error": None,
        }
        return with_notices(
            run_composite(
                [
                    ("regions", empty_regions),
                    ("mvtAdvice", empty_mvt),
                    ("antiPatterns", empty_anti_patterns),
                ],
                start=start,
            ),
            _world_partial_notices(params),
        )

    now = datetime.now(UTC)

    # Detect connected components (regions).
    component_result = detect_components(DetectComponentsInput(graph=params.graph), multi)
    components: list[list[str]] = component_result["components"]

    # Run the three foraging signals (each returns its own timed SectionResult).
    age_result = compute_age_staleness(components, entities, now=now)
    bridging_result = compute_bridging_potential(components, components)
    if embeddings_available(store):
        coverage_result = compute_coverage_gap(components, store)
    else:
        coverage_result = coverage_gap_unavailable()

    # Zeroed in-memory exploration state (always instantiated so guards RUN).
    effective_graph_name = params.graph or multi.default_graph
    exploration_store = ExplorationStateStore(effective_graph_name)
    total_invocations = exploration_store.get_state().total_invocations
    region_states = exploration_store.aggregate_to_regions(components)

    # Purpose keywords for region boosting.
    purpose_keywords = (
        [w for w in params.purpose.lower().split() if len(w) > 2] if params.purpose else []
    )

    entity_map: dict[str, Doc] = {e["id"]: e for e in entities}

    # -- Regions section -------------------------------------------------------
    def _regions() -> list[Doc]:
        scored: list[Doc] = []
        for i in range(len(components)):
            entity_ids = components[i]

            age_data = age_result["data"]
            age_staleness = (
                age_data[i].score if age_data is not None and i < len(age_data) else None
            )
            bridging_data = bridging_result["data"]
            bridging_potential = (
                bridging_data[i].score
                if bridging_data is not None and i < len(bridging_data)
                else None
            )
            coverage_data = coverage_result["data"]
            coverage_gap = (
                coverage_data[i].score
                if coverage_data is not None and i < len(coverage_data)
                else None
            )

            # Purpose boost: keyword overlap between purpose and region text.
            purpose_boost: float | None = None
            if purpose_keywords:
                parts: list[str] = []
                for entity_id in entity_ids:
                    e = entity_map.get(entity_id)
                    if e is None:
                        parts.append("")
                    else:
                        parts.append((e["name"] + " " + " ".join(e["observations"])).lower())
                region_words = set(" ".join(parts).split())
                match_count = sum(1 for kw in purpose_keywords if kw in region_words)
                purpose_boost = min(1.0, match_count / len(purpose_keywords))

            ucb_bonus = compute_ucb(region_states[i].total_visits, total_invocations)

            composite_score = compute_composite_score(
                age_staleness,
                bridging_potential,
                coverage_gap,
                ucb_bonus,
                purpose_boost,
            )

            entity_names = [
                (entity_map[entity_id]["name"] if entity_id in entity_map else entity_id)
                for entity_id in entity_ids[:MAX_DISPLAY_NAMES]
            ]

            scored.append(
                {
                    "entityIds": entity_ids,
                    "entityNames": entity_names,
                    "compositeScore": composite_score,
                    "signals": {
                        "ageStaleness": age_staleness,
                        "bridgingPotential": bridging_potential,
                        "coverageGap": coverage_gap,
                        "purposeBoost": purpose_boost,
                    },
                    "ucbBonus": ucb_bonus,
                    "rank": 0,  # assigned after sorting
                }
            )

        # Sort by compositeScore descending (stable).
        scored.sort(key=lambda r: r["compositeScore"], reverse=True)
        top_regions = scored[:top_k]
        for i, region in enumerate(top_regions):
            region["rank"] = i + 1
        return top_regions

    regions = time_section(_regions)

    # -- MVT advice section ----------------------------------------------------
    def _mvt_advice() -> list[Doc]:
        if not include_mvt:
            return []

        mvt_result = run_mvt_section(
            include_mvt=True,
            components=components,
            entities=entities,
            exploration_state=exploration_store,
            now=now,
        )
        if mvt_result["data"] is None:
            return []

        # Map component list identity -> index (entityIds refs are shared).
        component_ref_to_index = {id(components[i]): i for i in range(len(components))}

        advice: list[Doc] = []
        ranked_regions = regions["data"] or []
        for i, region in enumerate(ranked_regions):
            comp_index = component_ref_to_index.get(id(region["entityIds"]))
            if comp_index is None:
                continue
            mvt_rec = mvt_result["data"][comp_index]
            if mvt_rec is None or not mvt_rec.should_leave:
                continue
            advice.append(
                {
                    "regionIndex": i,
                    "recommendation": mvt_rec.recommendation,
                    "averageGain": mvt_rec.avg_gain,
                    "visitCount": region_states[comp_index].total_visits,
                }
            )
        return advice

    mvt_advice = time_section(_mvt_advice)

    # -- Anti-patterns section -------------------------------------------------
    def _anti_patterns() -> list[Doc]:
        if not include_anti_patterns:
            return []

        warnings: list[Doc] = []

        # Structural warnings (not covered by the six anti-pattern guards).
        if len(components) == 1:
            warnings.append(
                {
                    "type": "single-component-graph",
                    "message": (
                        "Only 1 connected component detected -- no bridging opportunity "
                        "between components"
                    ),
                    "severity": "warning",
                }
            )

        age_data = age_result["data"]
        if age_data and len(age_data) > 0:
            all_stale = all(r.score > ALL_STALE_THRESHOLD for r in age_data)
            if all_stale:
                warnings.append(
                    {
                        "type": "all-stale",
                        "message": (
                            "Every region has staleness above 0.8 -- graph needs a broad refresh"
                        ),
                        "severity": "warning",
                    }
                )

        if total_invocations == 0:
            warnings.append(
                {
                    "type": "no-visit-history",
                    "message": "No exploration history found -- this appears to be the first run",
                    "severity": "info",
                }
            )

        if coverage_result["error"] is not None:
            warnings.append(
                {
                    "type": "embedding-unavailable",
                    "message": (
                        "CoverageGap signal unavailable -- embedding pipeline not configured"
                    ),
                    "severity": "info",
                }
            )

        # Run the six anti-pattern guards.
        if len(region_states) > 1:
            entity_type_map: dict[str, str] = {e["id"]: e["entityType"] for e in entities}
            gain_history_by_region: dict[str, list[float]] = {
                str(i): exploration_store.get_region_gain_history(region_key(components[i]))
                for i in range(len(components))
            }
            guard_output = run_guards(
                regions=region_states,
                coverage_gap_scores=coverage_result["data"],
                entity_type_map=entity_type_map,
                gain_history_by_region=gain_history_by_region,
                include_anti_patterns=True,
            )
            for ap in guard_output.anti_patterns:
                warnings.append(
                    {
                        "type": ap.pattern,
                        "message": ap.recommendation,
                        "severity": "info" if ap.severity == "low" else "warning",
                    }
                )

        return warnings

    anti_patterns = time_section(_anti_patterns)

    return with_notices(
        run_composite(
            [
                ("regions", regions),
                ("mvtAdvice", mvt_advice),
                ("antiPatterns", anti_patterns),
            ],
            start=start,
        ),
        _world_partial_notices(params),
    )
