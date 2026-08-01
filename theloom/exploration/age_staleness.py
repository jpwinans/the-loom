"""AgeStaleness exploration signal.

Per-region staleness from entity timestamps via exponential decay: regions
untouched for longer score higher (more stale), making them better candidates
for re-exploration.

    staleness = 1 - exp(-lambda * clamp(days_since_update, 0, maxDays))

- 0.0 = perfectly fresh (just updated)
- 1.0 = maximally stale (empty region, or no resolvable timestamps)

Wrapped in :func:`time_section` for fault isolation and timing metadata.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from theloom.composites.framework import SectionResult, time_section
from theloom.exploration._numeric import js_round, parse_iso, to_iso_z

# Seconds in one day (used to convert time deltas into a day count).
_SECONDS_PER_DAY = 60 * 60 * 24


@dataclass(frozen=True)
class RegionStalenessScore:
    """Per-region staleness output."""

    entity_ids: list[str]
    """Entity IDs in the region (mirrors the input)."""
    score: float
    """Staleness score normalized to [0, 1]."""
    freshest_timestamp: str | None
    """ISO timestamp of the freshest entity, or None if the region is empty."""
    days_since_update: float | None
    """Days since the freshest update (clamped, rounded to 2dp), or None."""


@dataclass(frozen=True)
class AgeStalenessConfig:
    """Optional decay/capping parameters."""

    lambda_: float | None = None
    """Decay constant. Default: ln(2) / 15 (~0.0462, half-life 15 days)."""
    max_days: float | None = None
    """Maximum days cap for normalization. Default: 30."""


def compute_age_staleness(
    regions: list[list[str]],
    entities: list[dict[str, Any]],
    config: AgeStalenessConfig | None = None,
    now: datetime | None = None,
) -> SectionResult:
    """Compute staleness scores for a set of regions.

    :param regions: Regions, each a list of entity IDs.
    :param entities: All entities as dicts with ``id`` plus ``updated_at`` /
        ``created_at`` ISO strings (``updated_at`` preferred; ``created_at`` is
        the fallback when ``updated_at`` is missing/empty).
    :param config: Optional decay and capping parameters.
    :param now: Reference time for age calculation (defaults to ``datetime.now``).
    :returns: SectionResult ``{data: list[RegionStalenessScore], durationMs, error}``.
    """

    def _run() -> list[RegionStalenessScore]:
        entity_map = {entity["id"]: entity for entity in entities}
        lambda_ = (
            config.lambda_
            if config is not None and config.lambda_ is not None
            else math.log(2) / 15
        )
        max_days = config.max_days if config is not None and config.max_days is not None else 30.0
        reference_time = now if now is not None else datetime.now(UTC)

        results: list[RegionStalenessScore] = []
        for entity_ids in regions:
            timestamps: list[datetime] = []
            for entity_id in entity_ids:
                entity = entity_map.get(entity_id)
                if entity is None:
                    continue
                # Fallback: use created_at when updated_at is missing or empty.
                stamp = entity.get("updated_at") or entity.get("created_at")
                if stamp:
                    timestamps.append(parse_iso(stamp))

            if not timestamps:
                results.append(RegionStalenessScore(entity_ids, 1.0, None, None))
                continue

            freshest = max(timestamps)
            days_since_update = (reference_time - freshest).total_seconds() / _SECONDS_PER_DAY
            clamped_days = min(max(days_since_update, 0.0), float(max_days))
            score = 1 - math.exp(-lambda_ * clamped_days)

            results.append(
                RegionStalenessScore(
                    entity_ids,
                    score,
                    to_iso_z(freshest),
                    js_round(clamped_days * 100) / 100,
                )
            )
        return results

    return time_section(_run)
