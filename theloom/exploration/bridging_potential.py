"""BridgingPotential exploration signal.

Per-region bridging potential from cross-component connectivity. Regions in
smaller / more isolated components score higher (more value in bridging out).

    score = sum(sizes of other components) / (totalEntities - ownComponentSize)

- 0.0 = no bridging potential (single component)
- 1.0 = maximum bridging potential (singleton, or entity in no component)

Wrapped in :func:`time_section` for fault isolation and timing metadata.

Note on the composite's usage: explore-frontier calls this as
``compute_bridging_potential(components, components)`` (regions == components),
so every non-empty region that lies wholly inside one component scores 1.0 when
there are multiple components and 0.0 when there is a single component.
"""

from __future__ import annotations

from dataclasses import dataclass

from theloom.composites.framework import SectionResult, time_section


@dataclass(frozen=True)
class BridgingPotentialResult:
    """Per-region bridging potential output."""

    entity_ids: list[str]
    """Entity IDs in the region (mirrors the input)."""
    score: float
    """Bridging potential score normalized to [0, 1]."""
    component_index: int
    """Index of the component holding the region's first entity, or -1."""
    reachable_component_sizes: list[int]
    """Sizes of every other component (excluding the region's own)."""


def compute_bridging_potential(
    regions: list[list[str]],
    components: list[list[str]],
) -> SectionResult:
    """Compute bridging potential scores for a set of regions.

    :param regions: Regions to score, each a list of entity IDs.
    :param components: All connected components (from detect_components).
    :returns: SectionResult ``{data: list[BridgingPotentialResult], durationMs, error}``.
    """

    def _run() -> list[BridgingPotentialResult]:
        total_entities = sum(len(component) for component in components)

        entity_to_component: dict[str, int] = {}
        for index, component in enumerate(components):
            for entity_id in component:
                entity_to_component[entity_id] = index

        results: list[BridgingPotentialResult] = []
        for entity_ids in regions:
            # Empty region.
            if len(entity_ids) == 0:
                results.append(BridgingPotentialResult(entity_ids, 0.0, -1, []))
                continue

            # Component index from the first entity.
            component_index = entity_to_component.get(entity_ids[0], -1)

            # Single component: no bridging potential.
            if len(components) <= 1 and component_index != -1:
                results.append(BridgingPotentialResult(entity_ids, 0.0, component_index, []))
                continue

            own_component_size = len(components[component_index]) if component_index >= 0 else 0

            reachable_component_sizes = [
                len(components[i]) for i in range(len(components)) if i != component_index
            ]
            sum_target_sizes = sum(reachable_component_sizes)
            max_possible = total_entities - own_component_size

            if max_possible > 0:
                score = min(sum_target_sizes / max_possible, 1.0)
            elif total_entities == 0 and len(entity_ids) > 0:
                # Empty components array with non-empty region: maximum potential.
                score = 1.0
            else:
                score = 0.0

            results.append(
                BridgingPotentialResult(
                    entity_ids, score, component_index, reachable_component_sizes
                )
            )
        return results

    return time_section(_run)
