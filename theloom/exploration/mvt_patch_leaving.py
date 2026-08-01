"""MVT Patch-Leaving Policy signal.

Implements the Marginal Value Theorem (Charnov 1976) for epistemic foraging:
recommend leaving a knowledge region when its marginal information gain drops
below the average gain rate across all regions with sufficient history.

    marginalGain(region) = gains[last] - gains[last-1]
    avgGain              = mean(marginalGain over regions with history)
    shouldLeave          = marginalGain(region) < avgGain

Cold-start (history < minHistoryLength): AgeStaleness is used as a proxy gain
estimate and the region never receives a "leave" recommendation.

Wrapped in :func:`time_section` for fault isolation and timing metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from theloom.composites.framework import SectionResult, time_section
from theloom.exploration._numeric import to_fixed
from theloom.exploration.age_staleness import AgeStalenessConfig, compute_age_staleness


@dataclass(frozen=True)
class MvtRegionRecommendation:
    """Per-region MVT recommendation."""

    entity_ids: list[str]
    """Entity IDs in the region (mirrors the input component)."""
    should_leave: bool
    """Whether the policy recommends leaving this region."""
    marginal_gain: float
    """gain(t) - gain(t-1), or the AgeStaleness proxy during cold-start."""
    avg_gain: float
    """Average marginal gain across all regions with sufficient history."""
    recommendation: str
    """Human-readable recommendation string."""


@dataclass(frozen=True)
class MvtPolicyConfig:
    """Configuration for the MVT computation."""

    min_history_length: int | None = None
    """Minimum gain-history entries before MVT can activate (default: 2)."""


def compute_mvt_patch_leaving(
    regions: list[list[str]],
    region_gain_history: list[list[float]],
    entities: list[dict[str, Any]],
    config: MvtPolicyConfig | None = None,
    age_staleness_config: AgeStalenessConfig | None = None,
    now: datetime | None = None,
) -> SectionResult:
    """Compute MVT patch-leaving recommendations for a set of regions.

    :param regions: Regions, each a list of entity IDs.
    :param region_gain_history: Per-region ordered gain values (caller-extracted;
        index-aligned with ``regions``).
    :param entities: All entities (dicts) for AgeStaleness cold-start lookup.
    :param config: Optional MVT policy configuration.
    :param age_staleness_config: Optional config forwarded to AgeStaleness.
    :param now: Reference time for AgeStaleness (defaults to now).
    :returns: SectionResult ``{data: list[MvtRegionRecommendation], durationMs, error}``.
    """

    def _run() -> list[MvtRegionRecommendation]:
        min_history_length = (
            config.min_history_length
            if config is not None and config.min_history_length is not None
            else 2
        )

        if len(regions) == 0:
            return []

        is_cold_start = [len(gains) < min_history_length for gains in region_gain_history]

        marginal_gains: list[float | None] = []
        for i, gains in enumerate(region_gain_history):
            if is_cold_start[i]:
                marginal_gains.append(None)
            else:
                marginal_gains.append(gains[-1] - gains[-2])

        valid_gains = [gain for gain in marginal_gains if gain is not None]
        avg_gain = sum(valid_gains) / len(valid_gains) if valid_gains else 0.0

        cold_start_indices = [i for i, cold in enumerate(is_cold_start) if cold]
        staleness_scores: dict[int, float] = {}
        if cold_start_indices:
            cold_regions = [regions[i] for i in cold_start_indices]
            staleness_result = compute_age_staleness(
                cold_regions, entities, age_staleness_config, now
            )
            data = staleness_result["data"]
            if data is not None:
                for j, region_index in enumerate(cold_start_indices):
                    staleness_scores[region_index] = data[j].score

        recommendations: list[MvtRegionRecommendation] = []
        for i, entity_ids in enumerate(regions):
            if is_cold_start[i]:
                proxy = staleness_scores.get(i, 0.0)
                recommendations.append(
                    MvtRegionRecommendation(
                        entity_ids=entity_ids,
                        should_leave=False,
                        marginal_gain=proxy,
                        avg_gain=avg_gain,
                        recommendation=(
                            f"Cold-start: staleness proxy = {to_fixed(proxy, 2)}, "
                            "awaiting gain history"
                        ),
                    )
                )
                continue

            marginal_gain = marginal_gains[i]
            assert marginal_gain is not None  # not cold-start => concrete value
            should_leave = marginal_gain < avg_gain
            if should_leave:
                recommendation = (
                    f"Leave: marginal gain ({to_fixed(marginal_gain, 2)}) "
                    f"below average ({to_fixed(avg_gain, 2)})"
                )
            else:
                recommendation = (
                    f"Stay: marginal gain ({to_fixed(marginal_gain, 2)}) "
                    f"at or above average ({to_fixed(avg_gain, 2)})"
                )

            recommendations.append(
                MvtRegionRecommendation(
                    entity_ids=entity_ids,
                    should_leave=should_leave,
                    marginal_gain=marginal_gain,
                    avg_gain=avg_gain,
                    recommendation=recommendation,
                )
            )
        return recommendations

    return time_section(_run)
