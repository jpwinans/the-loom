"""Exploration foraging-signals foundation for the explore-frontier composite.

Each module is an independent foraging signal or helper; the explore-frontier
composite orchestrates them. Public API is re-exported here for convenient
single-import wiring by the composite.
"""

from __future__ import annotations

from theloom.exploration.age_staleness import (
    AgeStalenessConfig,
    RegionStalenessScore,
    compute_age_staleness,
)
from theloom.exploration.bridging_potential import (
    BridgingPotentialResult,
    compute_bridging_potential,
)
from theloom.exploration.composite_signals import (
    ALL_STALE_THRESHOLD,
    DEFAULT_TOP_K,
    DEFAULT_WEIGHTS,
    MAX_DISPLAY_NAMES,
    PURPOSE_WEIGHT_IN_EXPLORATION,
    UCB_CONSTANT,
    CompositeWeights,
    compute_composite_score,
    compute_ucb,
)
from theloom.exploration.coverage_gap import (
    COVERAGE_GAP_UNAVAILABLE_MESSAGE,
    CoverageGapResult,
    SupportsEntityVectors,
    compute_coverage_gap,
    coverage_gap_unavailable,
    embeddings_available,
)
from theloom.exploration.exploration_state import (
    EntityExplorationState,
    ExplorationState,
    ExplorationStateStore,
    RegionExplorationState,
    RegionGainSnapshot,
    region_key,
)
from theloom.exploration.guards import (
    DEFAULT_THRESHOLDS,
    AntiPatternResult,
    AntiPatternSeverity,
    AntiPatternType,
    GuardThresholds,
    RunGuardsOutput,
    detect_breadth_addiction,
    detect_comfort_zone,
    detect_echo_chamber,
    detect_noisy_tv_trap,
    detect_random_walk,
    detect_semantic_gravity_well,
    run_guards,
)
from theloom.exploration.mvt import run_mvt_section
from theloom.exploration.mvt_patch_leaving import (
    MvtPolicyConfig,
    MvtRegionRecommendation,
    compute_mvt_patch_leaving,
)

__all__ = [
    "ALL_STALE_THRESHOLD",
    "COVERAGE_GAP_UNAVAILABLE_MESSAGE",
    "DEFAULT_THRESHOLDS",
    "DEFAULT_TOP_K",
    "DEFAULT_WEIGHTS",
    "MAX_DISPLAY_NAMES",
    "PURPOSE_WEIGHT_IN_EXPLORATION",
    "UCB_CONSTANT",
    "AgeStalenessConfig",
    "AntiPatternResult",
    "AntiPatternSeverity",
    "AntiPatternType",
    "BridgingPotentialResult",
    "CompositeWeights",
    "CoverageGapResult",
    "EntityExplorationState",
    "ExplorationState",
    "ExplorationStateStore",
    "GuardThresholds",
    "MvtPolicyConfig",
    "MvtRegionRecommendation",
    "RegionExplorationState",
    "RegionGainSnapshot",
    "RegionStalenessScore",
    "RunGuardsOutput",
    "SupportsEntityVectors",
    "compute_age_staleness",
    "compute_bridging_potential",
    "compute_composite_score",
    "compute_coverage_gap",
    "compute_mvt_patch_leaving",
    "compute_ucb",
    "coverage_gap_unavailable",
    "detect_breadth_addiction",
    "detect_comfort_zone",
    "detect_echo_chamber",
    "detect_noisy_tv_trap",
    "detect_random_walk",
    "detect_semantic_gravity_well",
    "embeddings_available",
    "region_key",
    "run_guards",
    "run_mvt_section",
]
