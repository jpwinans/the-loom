"""Composite scoring helpers for explore-frontier (UCB bonus + weighted score).

The ``computeUcbBonus`` and ``computeCompositeScore`` helpers plus their shared
constants. These live here (not in the composite) so the composite can import
them without pulling in orchestration code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# UCB1 exploration constant (sqrt(2) is theoretically optimal; the rounded
# literal 1.41 is used).
UCB_CONSTANT = 1.41

# Default number of top regions the composite returns.
DEFAULT_TOP_K = 5

# Maximum entity names included per region for display.
MAX_DISPLAY_NAMES = 5

# Staleness threshold for the all-stale anti-pattern warning.
ALL_STALE_THRESHOLD = 0.8

# Weight of purpose relevance in the exploration composite score.
PURPOSE_WEIGHT_IN_EXPLORATION = 0.2


@dataclass(frozen=True)
class CompositeWeights:
    """Signal weights for the composite score."""

    age_staleness: float = 0.3
    bridging_potential: float = 0.3
    coverage_gap: float = 0.2
    ucb_bonus: float = 0.2


DEFAULT_WEIGHTS = CompositeWeights()


def compute_ucb(visit_count: int, total_invocations: int) -> float:
    """Compute the UCB1 exploration bonus.

    Formula: ``C * sqrt(ln(totalInvocations) / visitCount)``, clamped to 1.0.
    Unvisited regions (or a fresh graph with no invocations) get the maximum
    bonus of 1.0.
    """
    if total_invocations == 0 or visit_count == 0:
        return 1.0
    raw = UCB_CONSTANT * math.sqrt(math.log(total_invocations) / visit_count)
    return min(raw, 1.0)


def compute_composite_score(
    age_staleness: float | None,
    bridging_potential: float | None,
    coverage_gap: float | None,
    ucb_bonus: float,
    purpose_boost: float | None = None,
    weights: CompositeWeights = DEFAULT_WEIGHTS,
    purpose_weight: float = PURPOSE_WEIGHT_IN_EXPLORATION,
) -> float:
    """Weighted average over the signals that are present.

    ``None`` signals are dropped and the remaining weights are renormalized so
    they sum to 1. The UCB bonus is always present. Result clamped to [0, 1].
    """
    entries: list[tuple[float, float]] = []

    if age_staleness is not None:
        entries.append((age_staleness, weights.age_staleness))
    if bridging_potential is not None:
        entries.append((bridging_potential, weights.bridging_potential))
    if coverage_gap is not None:
        entries.append((coverage_gap, weights.coverage_gap))
    # UCB bonus is always available.
    entries.append((ucb_bonus, weights.ucb_bonus))
    # Purpose boost when provided.
    if purpose_boost is not None:
        entries.append((purpose_boost, purpose_weight))

    total_weight = sum(weight for _, weight in entries)
    if total_weight == 0:
        return 0.0

    weighted_sum = sum(value * (weight / total_weight) for value, weight in entries)
    return min(max(weighted_sum, 0.0), 1.0)
