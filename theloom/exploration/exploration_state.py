"""Exploration state — visit counts, gain history, and region aggregation.

Defines the ExplorationState, EntityExplorationState, RegionExplorationState,
and RegionGainSnapshot structures.

DESIGN DECISION — no sidecar file.
----------------------------------
State is not persisted to disk. This store is a pure in-memory
``ExplorationStateStore`` that starts ZEROED. Rather than skip UCB / MVT /
guards, it always presents real (zeroed) state on a fresh graph. Concretely,
for a fresh graph:

- ``get_state().total_invocations == 0``
- ``aggregate_to_regions(components)`` returns one zeroed RegionExplorationState
  per component (``total_visits == 0``, ``average_gain is None``, empty history)
- ``get_region_gain_history(key) == []``

This makes the composite's guards RUN against real (zeroed) region states on
first run instead of being skipped. The mutation methods (``record_visit`` /
``record_region_gain`` / ``increment_total_invocations``) populate the state
in-process, but nothing is written to disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from theloom.exploration._numeric import now_iso

# Maximum gain history entries per entity before oldest are dropped.
MAX_ENTITY_GAIN_HISTORY = 100

# Maximum gain snapshots per region before oldest are dropped.
MAX_REGION_GAIN_HISTORY = 100


@dataclass
class GainHistoryEntry:
    """A single information-gain observation for an entity."""

    timestamp: str
    gain: float


@dataclass
class EntityExplorationState:
    """Per-entity exploration state."""

    visit_count: int = 0
    last_visited: str = ""
    gain_history: list[GainHistoryEntry] = field(default_factory=list)


@dataclass
class RegionGainSnapshot:
    """A snapshot of gain for a region at a point in time."""

    region_key: str
    gain: float
    timestamp: str


@dataclass
class RegionExplorationState:
    """Aggregated per-region exploration state.

    Computed at query time from entity-level states — never persisted.
    """

    entity_ids: list[str]
    total_visits: int
    last_visited: str | None
    average_gain: float | None
    visited_entity_count: int
    entity_count: int


@dataclass
class ExplorationState:
    """Top-level exploration state."""

    graph_name: str
    last_updated: str
    total_invocations: int = 0
    entity_states: dict[str, EntityExplorationState] = field(default_factory=dict)
    region_gain_history: dict[str, list[RegionGainSnapshot]] = field(default_factory=dict)


def region_key(entity_ids: list[str]) -> str:
    """Stable region key: the first entity ID in sorted order (or "").

    Deterministic as long as the component holds the same entities — the
    smallest-ID entity anchors the region's identity across invocations.
    """
    if len(entity_ids) == 0:
        return ""
    return sorted(entity_ids)[0]


class ExplorationStateStore:
    """In-memory per-graph exploration state (no sidecar file; starts zeroed)."""

    def __init__(self, graph_name: str = "default") -> None:
        self._state = ExplorationStateStore.create_empty(graph_name)

    @staticmethod
    def create_empty(graph_name: str) -> ExplorationState:
        """Create a default empty exploration state."""
        return ExplorationState(graph_name=graph_name, last_updated=now_iso())

    def get_state(self) -> ExplorationState:
        """Return the current in-memory exploration state."""
        return self._state

    def record_visit(self, entity_id: str, gain: float | None = None) -> None:
        """Record a visit to an entity, optionally with an information gain."""
        now = now_iso()
        entity_state = self._state.entity_states.get(entity_id)
        if entity_state is None:
            entity_state = EntityExplorationState(visit_count=0, last_visited=now, gain_history=[])
            self._state.entity_states[entity_id] = entity_state

        entity_state.visit_count += 1
        entity_state.last_visited = now

        if gain is not None:
            entity_state.gain_history.append(GainHistoryEntry(timestamp=now, gain=gain))
            if len(entity_state.gain_history) > MAX_ENTITY_GAIN_HISTORY:
                entity_state.gain_history = entity_state.gain_history[-MAX_ENTITY_GAIN_HISTORY:]

    def increment_total_invocations(self) -> None:
        """Increment the total invocations counter."""
        self._state.total_invocations += 1

    def get_entity_state(self, entity_id: str) -> EntityExplorationState | None:
        """Return the exploration state for an entity, or None if unvisited."""
        return self._state.entity_states.get(entity_id)

    def record_region_gain(self, key: str, gain: float) -> None:
        """Record a gain snapshot for a region (used by the MVT policy)."""
        snapshots = self._state.region_gain_history.setdefault(key, [])
        snapshots.append(RegionGainSnapshot(region_key=key, gain=gain, timestamp=now_iso()))
        if len(snapshots) > MAX_REGION_GAIN_HISTORY:
            self._state.region_gain_history[key] = snapshots[-MAX_REGION_GAIN_HISTORY:]

    def get_region_gain_history(self, key: str) -> list[float]:
        """Return the ordered gain values for a region (empty if none)."""
        snapshots = self._state.region_gain_history.get(key)
        if not snapshots:
            return []
        return [snapshot.gain for snapshot in snapshots]

    def aggregate_to_regions(self, components: list[list[str]]) -> list[RegionExplorationState]:
        """Aggregate entity-level state to region level via connected components.

        Returns one RegionExplorationState per component. Because regions are
        computed at query time, component merges/splits preserve entity state.
        """
        results: list[RegionExplorationState] = []
        for entity_ids in components:
            total_visits = 0
            latest_timestamp: str | None = None
            visited_entity_count = 0
            all_gains: list[float] = []

            for entity_id in entity_ids:
                entity_state = self._state.entity_states.get(entity_id)
                if entity_state is None:
                    continue

                total_visits += entity_state.visit_count
                if entity_state.visit_count > 0:
                    visited_entity_count += 1
                if entity_state.last_visited and (
                    latest_timestamp is None or entity_state.last_visited > latest_timestamp
                ):
                    latest_timestamp = entity_state.last_visited
                for entry in entity_state.gain_history:
                    all_gains.append(entry.gain)

            average_gain = sum(all_gains) / len(all_gains) if all_gains else None

            results.append(
                RegionExplorationState(
                    entity_ids=entity_ids,
                    total_visits=total_visits,
                    last_visited=latest_timestamp,
                    average_gain=average_gain,
                    visited_entity_count=visited_entity_count,
                    entity_count=len(entity_ids),
                )
            )
        return results
