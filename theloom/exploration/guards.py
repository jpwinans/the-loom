"""Anti-pattern detection guards for exploration.

Provides the types, the six detectors, and the runner. Each detector inspects
aggregated region exploration state and flags a pathological exploration
behavior, returning an :class:`AntiPatternResult` or ``None``.

The six patterns: echo_chamber, semantic_gravity_well, comfort_zone,
random_walk, noisy_tv_trap, breadth_addiction.

All recommendation strings, thresholds, and severity ladders are fixed. The
comfort-zone divisor is ``len(ALL_ENTITY_TYPES)`` which is 20 for this model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from theloom.exploration._numeric import js_round, to_fixed
from theloom.exploration.coverage_gap import CoverageGapResult
from theloom.exploration.exploration_state import RegionExplorationState
from theloom.model import ALL_ENTITY_TYPES

# =============================================================================
# Types
# =============================================================================

AntiPatternSeverity = Literal["low", "medium", "high"]
AntiPatternType = Literal[
    "echo_chamber",
    "semantic_gravity_well",
    "comfort_zone",
    "random_walk",
    "noisy_tv_trap",
    "breadth_addiction",
]

# Total number of entity types in the system (comfort-zone divisor).
TOTAL_ENTITY_TYPES = len(ALL_ENTITY_TYPES)

# Visit concentration threshold for the gravity-well fallback path (no Tier B).
FALLBACK_CONCENTRATION_THRESHOLD = 0.6


@dataclass(frozen=True)
class AntiPatternResult:
    """Result of a single anti-pattern detection."""

    pattern: AntiPatternType
    severity: AntiPatternSeverity
    affected_regions: list[list[str]]
    recommendation: str


@dataclass(frozen=True)
class GuardThresholds:
    """Configurable thresholds for all detectors."""

    echo_chamber_consecutive_visits: int = 5
    echo_chamber_max_diversity: float = 0.2
    semantic_gravity_well_max_coverage_gap: float = 0.3
    semantic_gravity_well_min_density: float = 0.7
    comfort_zone_min_type_diversity: float = 0.3
    random_walk_max_avg_gain: float = 0.2
    random_walk_min_diversity: float = 0.8
    noisy_tv_min_visits: int = 3
    noisy_tv_max_gain_variance: float = 0.05
    breadth_addiction_min_single_visit_ratio: float = 0.7
    breadth_addiction_max_deep_visit_ratio: float = 0.1
    breadth_addiction_deep_visit_threshold: int = 3


DEFAULT_THRESHOLDS = GuardThresholds()


@dataclass(frozen=True)
class RunGuardsOutput:
    """Output from the guard runner."""

    anti_patterns: list[AntiPatternResult]
    skipped: bool
    tier_b_available: bool


# =============================================================================
# Echo Chamber
# =============================================================================


def detect_echo_chamber(
    regions: list[RegionExplorationState],
    thresholds: GuardThresholds = DEFAULT_THRESHOLDS,
) -> AntiPatternResult | None:
    """Same region revisited repeatedly with low diversity across regions."""
    if len(regions) == 0:
        return None

    min_visits = thresholds.echo_chamber_consecutive_visits
    max_diversity = thresholds.echo_chamber_max_diversity

    hot_regions = [r for r in regions if r.total_visits >= min_visits]
    if len(hot_regions) == 0:
        return None

    visited_region_count = sum(1 for r in regions if r.total_visits > 0)
    diversity = visited_region_count / len(regions)
    if diversity > max_diversity:
        return None

    max_visits = max(r.total_visits for r in hot_regions)
    visit_ratio = max_visits / min_visits

    severity: AntiPatternSeverity = (
        "high" if visit_ratio >= 2 else "medium" if visit_ratio >= 1.5 else "low"
    )
    affected_regions = [r.entity_ids for r in hot_regions]

    return AntiPatternResult(
        pattern="echo_chamber",
        severity=severity,
        affected_regions=affected_regions,
        recommendation=(
            f"Echo chamber detected: {len(hot_regions)} region(s) are being revisited excessively "
            f"(up to {max_visits} visits) while only {visited_region_count}/{len(regions)} regions "
            "have been explored. Consider branching out to unvisited regions to discover new "
            "knowledge."
        ),
    )


# =============================================================================
# Semantic Gravity Well
# =============================================================================


def _gravity_well_severity(concentration: float) -> AntiPatternSeverity:
    if concentration >= 0.8:
        return "high"
    if concentration >= 0.7:
        return "medium"
    return "low"


def _detect_with_tier_b(
    regions: list[RegionExplorationState],
    coverage_gap_scores: list[CoverageGapResult],
    total_visits: int,
    thresholds: GuardThresholds,
) -> AntiPatternResult | None:
    max_coverage_gap = thresholds.semantic_gravity_well_max_coverage_gap

    coverage_gap_map: dict[str, float] = {}
    for gap in coverage_gap_scores:
        key = ",".join(sorted(gap.entity_ids))
        coverage_gap_map[key] = gap.score

    affected_regions: list[list[str]] = []
    max_concentration = 0.0
    for region in regions:
        key = ",".join(sorted(region.entity_ids))
        gap_score = coverage_gap_map.get(key)
        if gap_score is not None and gap_score < max_coverage_gap:
            concentration = region.total_visits / total_visits
            if concentration > FALLBACK_CONCENTRATION_THRESHOLD:
                affected_regions.append(region.entity_ids)
                max_concentration = max(max_concentration, concentration)

    if len(affected_regions) == 0:
        return None

    return AntiPatternResult(
        pattern="semantic_gravity_well",
        severity=_gravity_well_severity(max_concentration),
        affected_regions=affected_regions,
        recommendation=(
            f"Semantic gravity well detected: {len(affected_regions)} dense region(s) are "
            "attracting a disproportionate share of visits "
            f"(up to {js_round(max_concentration * 100)}% of total). "
            "These areas have low coverage gaps, suggesting they are already "
            "well-explored. Consider exploring sparser regions with higher coverage gaps."
        ),
    )


def _detect_with_fallback(
    regions: list[RegionExplorationState],
    total_visits: int,
) -> AntiPatternResult | None:
    affected_regions: list[list[str]] = []
    max_concentration = 0.0
    for region in regions:
        concentration = region.total_visits / total_visits
        if concentration > FALLBACK_CONCENTRATION_THRESHOLD:
            affected_regions.append(region.entity_ids)
            max_concentration = max(max_concentration, concentration)

    if len(affected_regions) == 0:
        return None

    return AntiPatternResult(
        pattern="semantic_gravity_well",
        severity=_gravity_well_severity(max_concentration),
        affected_regions=affected_regions,
        recommendation=(
            f"Semantic gravity well detected (without embedding data): {len(affected_regions)} "
            f"region(s) concentrate {js_round(max_concentration * 100)}% of total visits. "
            "Consider distributing exploration more evenly across regions."
        ),
    )


def detect_semantic_gravity_well(
    regions: list[RegionExplorationState],
    coverage_gap_scores: list[CoverageGapResult] | None,
    thresholds: GuardThresholds = DEFAULT_THRESHOLDS,
) -> AntiPatternResult | None:
    """Being pulled toward high-density areas (Tier B) or high-concentration regions."""
    if len(regions) <= 1:
        return None

    total_visits = sum(r.total_visits for r in regions)
    if total_visits == 0:
        return None

    if coverage_gap_scores is not None and len(coverage_gap_scores) > 0:
        return _detect_with_tier_b(regions, coverage_gap_scores, total_visits, thresholds)

    return _detect_with_fallback(regions, total_visits)


# =============================================================================
# Comfort Zone
# =============================================================================


def _comfort_zone_severity(diversity_ratio: float) -> AntiPatternSeverity:
    if diversity_ratio < 0.1:
        return "high"
    if diversity_ratio < 0.2:
        return "medium"
    return "low"


def detect_comfort_zone(
    regions: list[RegionExplorationState],
    entity_type_map: dict[str, str],
    thresholds: GuardThresholds = DEFAULT_THRESHOLDS,
) -> AntiPatternResult | None:
    """Exploration covers too few of the system's entity types."""
    if len(entity_type_map) == 0:
        return None

    min_diversity = thresholds.comfort_zone_min_type_diversity

    region_entity_ids: set[str] = set()
    for region in regions:
        for entity_id in region.entity_ids:
            region_entity_ids.add(entity_id)

    type_counts: dict[str, int] = {}
    for entity_id, entity_type in entity_type_map.items():
        if len(region_entity_ids) == 0 or entity_id in region_entity_ids:
            type_counts[entity_type] = type_counts.get(entity_type, 0) + 1

    if len(type_counts) == 0:
        return None

    diversity_ratio = len(type_counts) / TOTAL_ENTITY_TYPES
    if diversity_ratio >= min_diversity:
        return None

    severity = _comfort_zone_severity(diversity_ratio)

    present_types = sorted(type_counts.keys())
    missing_types = [t for t in ALL_ENTITY_TYPES if t not in type_counts]
    suggested_types = missing_types[:5]
    affected_regions = [r.entity_ids for r in regions]

    extra = f" and {len(missing_types) - 5} more" if len(missing_types) > 5 else ""

    return AntiPatternResult(
        pattern="comfort_zone",
        severity=severity,
        affected_regions=affected_regions,
        recommendation=(
            f"Comfort zone detected: only {len(type_counts)}/{TOTAL_ENTITY_TYPES} entity types "
            f"are represented ({', '.join(present_types)}). "
            f"Consider exploring entities of type: {', '.join(suggested_types)}{extra}."
        ),
    )


# =============================================================================
# Random Walk
# =============================================================================


def detect_random_walk(
    regions: list[RegionExplorationState],
    thresholds: GuardThresholds = DEFAULT_THRESHOLDS,
) -> AntiPatternResult | None:
    """High region diversity but low average gain — aimless wandering."""
    if len(regions) == 0:
        return None

    max_avg_gain = thresholds.random_walk_max_avg_gain
    min_diversity = thresholds.random_walk_min_diversity

    gains = [g for r in regions if (g := r.average_gain) is not None]
    if len(gains) == 0:
        return None

    avg_gain = sum(gains) / len(gains)
    if avg_gain >= max_avg_gain:
        return None

    visited_count = sum(1 for r in regions if r.total_visits > 0)
    diversity = visited_count / len(regions)
    if diversity < min_diversity:
        return None

    gain_ratio = avg_gain / max_avg_gain
    severity: AntiPatternSeverity = (
        "high" if gain_ratio < 0.5 else "medium" if gain_ratio < 0.75 else "low"
    )

    affected_regions = [
        r.entity_ids
        for r in regions
        if r.total_visits > 0 and (r.average_gain is None or r.average_gain < max_avg_gain)
    ]

    return AntiPatternResult(
        pattern="random_walk",
        severity=severity,
        affected_regions=affected_regions,
        recommendation=(
            f"Random walk detected: average gain is {to_fixed(avg_gain, 3)} "
            f"(threshold: {max_avg_gain}) "
            f"across {visited_count}/{len(regions)} visited regions. "
            "Consider exploiting promising discoveries instead of continuing to wander broadly."
        ),
    )


# =============================================================================
# Noisy TV Trap
# =============================================================================


def _population_variance(values: list[float]) -> float:
    if len(values) == 0:
        return 0.0
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def detect_noisy_tv_trap(
    regions: list[RegionExplorationState],
    gain_history_by_region: dict[str, list[float]],
    thresholds: GuardThresholds = DEFAULT_THRESHOLDS,
) -> AntiPatternResult | None:
    """Fixation on heavily-visited regions with consistently low gain variance."""
    if len(regions) == 0:
        return None

    min_visits = thresholds.noisy_tv_min_visits
    max_variance = thresholds.noisy_tv_max_gain_variance

    trapped_regions: list[list[str]] = []
    for i, region in enumerate(regions):
        if region.total_visits < min_visits:
            continue
        gain_history = gain_history_by_region.get(str(i))
        if not gain_history:
            continue
        variance = _population_variance(gain_history)
        if variance <= max_variance:
            trapped_regions.append(region.entity_ids)

    if len(trapped_regions) == 0:
        return None

    severity: AntiPatternSeverity = (
        "high" if len(trapped_regions) >= 3 else "medium" if len(trapped_regions) >= 2 else "low"
    )

    return AntiPatternResult(
        pattern="noisy_tv_trap",
        severity=severity,
        affected_regions=trapped_regions,
        recommendation=(
            f"Noisy TV trap detected: {len(trapped_regions)} region(s) have high visit counts "
            f"but consistently low gain variance (threshold: {max_variance}). "
            "These regions produce little new information. Consider redirecting exploration "
            "elsewhere."
        ),
    )


# =============================================================================
# Breadth Addiction
# =============================================================================


def detect_breadth_addiction(
    regions: list[RegionExplorationState],
    thresholds: GuardThresholds = DEFAULT_THRESHOLDS,
) -> AntiPatternResult | None:
    """Many regions visited superficially but none explored in depth."""
    if len(regions) <= 1:
        return None

    min_single_visit_ratio = thresholds.breadth_addiction_min_single_visit_ratio
    max_deep_visit_ratio = thresholds.breadth_addiction_max_deep_visit_ratio
    deep_visit_threshold = thresholds.breadth_addiction_deep_visit_threshold

    visited_regions = [r for r in regions if r.total_visits > 0]
    single_visit_ratio = len(visited_regions) / len(regions)

    deeply_visited_regions = [r for r in regions if r.total_visits >= deep_visit_threshold]
    deep_visit_ratio = len(deeply_visited_regions) / len(regions)

    if single_visit_ratio < min_single_visit_ratio or deep_visit_ratio > max_deep_visit_ratio:
        return None

    severity: AntiPatternSeverity = (
        "high" if deep_visit_ratio == 0 else "medium" if deep_visit_ratio <= 0.05 else "low"
    )

    affected_regions = [
        r.entity_ids for r in visited_regions if r.total_visits < deep_visit_threshold
    ]

    return AntiPatternResult(
        pattern="breadth_addiction",
        severity=severity,
        affected_regions=affected_regions,
        recommendation=(
            f"Breadth addiction detected: {len(visited_regions)}/{len(regions)} regions visited "
            f"but only {len(deeply_visited_regions)} explored deeply "
            f"(>= {deep_visit_threshold} visits). "
            "Consider deepening exploration in promising regions rather than constantly moving on."
        ),
    )


# =============================================================================
# Runner
# =============================================================================


def run_guards(
    *,
    regions: list[RegionExplorationState],
    coverage_gap_scores: list[CoverageGapResult] | None,
    entity_type_map: dict[str, str],
    gain_history_by_region: dict[str, list[float]],
    include_anti_patterns: bool = True,
    thresholds: GuardThresholds | None = None,
) -> RunGuardsOutput:
    """Run all six anti-pattern detectors and aggregate the non-null results.

    Detector order is fixed: echo chamber, semantic gravity well, comfort zone,
    random walk, noisy TV trap, breadth addiction.
    """
    if not include_anti_patterns:
        return RunGuardsOutput(anti_patterns=[], skipped=True, tier_b_available=False)

    active = thresholds if thresholds is not None else DEFAULT_THRESHOLDS

    candidates = [
        detect_echo_chamber(regions, active),
        detect_semantic_gravity_well(regions, coverage_gap_scores, active),
        detect_comfort_zone(regions, entity_type_map, active),
        detect_random_walk(regions, active),
        detect_noisy_tv_trap(regions, gain_history_by_region, active),
        detect_breadth_addiction(regions, active),
    ]
    anti_patterns = [result for result in candidates if result is not None]

    return RunGuardsOutput(
        anti_patterns=anti_patterns,
        skipped=False,
        tier_b_available=coverage_gap_scores is not None,
    )
