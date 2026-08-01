"""Explore-Frontier MVT integration helper.

Orchestrates the MVT patch-leaving policy section within the explore-frontier
workflow: optionally records new region gains, extracts each region's gain
history from the ExplorationStateStore, and calls :func:`compute_mvt_patch_leaving`.

The ``include_mvt`` flag gates the whole section: when False, a skipped
SectionResult is returned immediately with no computation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from theloom.composites.framework import SectionResult
from theloom.exploration.age_staleness import AgeStalenessConfig
from theloom.exploration.exploration_state import ExplorationStateStore, region_key
from theloom.exploration.mvt_patch_leaving import MvtPolicyConfig, compute_mvt_patch_leaving

# Re-exported for callers that need the stable region key (the definition lives
# in exploration_state to avoid import cycles).
__all__ = ["region_key", "run_mvt_section"]


def run_mvt_section(
    *,
    include_mvt: bool,
    components: list[list[str]],
    entities: list[dict[str, Any]],
    exploration_state: ExplorationStateStore,
    current_region_gains: dict[str, float] | None = None,
    mvt_config: MvtPolicyConfig | None = None,
    age_staleness_config: AgeStalenessConfig | None = None,
    now: datetime | None = None,
) -> SectionResult:
    """Run the MVT patch-leaving policy section.

    :param include_mvt: When False, returns a skipped section immediately.
    :param components: Connected components (regions) from detect_components.
    :param entities: All entities (dicts) for AgeStaleness cold-start.
    :param exploration_state: The in-memory exploration state store.
    :param current_region_gains: Optional {region_key: gain} to record first.
    :param mvt_config: Optional MVT policy configuration.
    :param age_staleness_config: Optional AgeStaleness config for cold-start.
    :param now: Optional reference time.
    :returns: SectionResult ``{data: list[MvtRegionRecommendation], durationMs, error}``;
        when skipped, ``data`` is None and ``error`` is
        ``"MVT section skipped (includeMVT=false)"``.
    """
    if not include_mvt:
        return {"data": None, "durationMs": 0, "error": "MVT section skipped (includeMVT=false)"}

    if current_region_gains:
        for key, gain in current_region_gains.items():
            exploration_state.record_region_gain(key, gain)

    region_gain_history = [
        exploration_state.get_region_gain_history(region_key(entity_ids))
        for entity_ids in components
    ]

    return compute_mvt_patch_leaving(
        components,
        region_gain_history,
        entities,
        mvt_config,
        age_staleness_config,
        now,
    )
