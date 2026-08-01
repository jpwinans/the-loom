"""Unit tests for the exploration foraging-signals foundation.

Inline fixtures only — no FalkorDB. Asserts exact numeric outputs for the
signals (age-staleness, bridging, UCB, composite score, coverage gap), the
zeroed-state exploration store, the MVT policy, and a couple of guards firing /
not firing.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from theloom.exploration import (
    COVERAGE_GAP_UNAVAILABLE_MESSAGE,
    ExplorationStateStore,
    compute_age_staleness,
    compute_bridging_potential,
    compute_composite_score,
    compute_coverage_gap,
    compute_mvt_patch_leaving,
    compute_ucb,
    coverage_gap_unavailable,
    detect_comfort_zone,
    detect_echo_chamber,
    embeddings_available,
    region_key,
    run_guards,
)
from theloom.exploration.exploration_state import RegionExplorationState

REF_NOW = datetime(2026, 1, 31, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fakes / helpers
# ---------------------------------------------------------------------------


class FakeVectorStore:
    """Minimal store satisfying SupportsEntityVectors."""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors

    def get_entity_vectors(self) -> dict[str, list[float]]:
        return self._vectors


def zeroed_region(entity_ids: list[str]) -> RegionExplorationState:
    return RegionExplorationState(
        entity_ids=entity_ids,
        total_visits=0,
        last_visited=None,
        average_gain=None,
        visited_entity_count=0,
        entity_count=len(entity_ids),
    )


def visited_region(entity_ids: list[str], visits: int) -> RegionExplorationState:
    return RegionExplorationState(
        entity_ids=entity_ids,
        total_visits=visits,
        last_visited=None,
        average_gain=None,
        visited_entity_count=1 if visits else 0,
        entity_count=len(entity_ids),
    )


# ---------------------------------------------------------------------------
# AgeStaleness
# ---------------------------------------------------------------------------


def test_age_staleness_exact_scores() -> None:
    entities = [
        {"id": "fresh", "updated_at": "2026-01-31T00:00:00.000Z"},  # 0 days
        {"id": "half", "updated_at": "2026-01-16T00:00:00.000Z"},  # 15 days (half-life)
        {"id": "old", "updated_at": "2026-01-01T00:00:00.000Z"},  # 30 days (cap)
    ]
    result = compute_age_staleness(
        [["fresh"], ["half"], ["old"], ["missing"]], entities, now=REF_NOW
    )
    assert result["error"] is None
    data = result["data"]

    # 0 days -> score 0.
    assert data[0].score == 0.0
    assert data[0].days_since_update == 0.0
    assert data[0].freshest_timestamp == "2026-01-31T00:00:00.000Z"

    # 15 days == one half-life -> score 0.5.
    assert math.isclose(data[1].score, 0.5, abs_tol=1e-12)
    assert data[1].days_since_update == 15.0

    # 30 days == two half-lives -> score 0.75.
    assert math.isclose(data[2].score, 0.75, abs_tol=1e-12)
    assert data[2].days_since_update == 30.0
    assert data[2].freshest_timestamp == "2026-01-01T00:00:00.000Z"

    # Empty / unresolvable region -> maximally stale, null timestamps.
    assert data[3].score == 1.0
    assert data[3].freshest_timestamp is None
    assert data[3].days_since_update is None


def test_age_staleness_created_at_fallback() -> None:
    entities = [{"id": "e", "updated_at": "", "created_at": "2026-01-16T00:00:00.000Z"}]
    data = compute_age_staleness([["e"]], entities, now=REF_NOW)["data"]
    assert math.isclose(data[0].score, 0.5, abs_tol=1e-12)
    assert data[0].freshest_timestamp == "2026-01-16T00:00:00.000Z"


# ---------------------------------------------------------------------------
# BridgingPotential
# ---------------------------------------------------------------------------


def test_bridging_multi_component_all_one() -> None:
    components = [["a", "b"], ["c"], ["d", "e", "f"]]
    data = compute_bridging_potential(components, components)["data"]
    assert [r.score for r in data] == [1.0, 1.0, 1.0]
    assert data[0].component_index == 0
    assert data[2].reachable_component_sizes == [2, 1]


def test_bridging_single_component_zero() -> None:
    components = [["a", "b", "c"]]
    data = compute_bridging_potential(components, components)["data"]
    assert data[0].score == 0.0
    assert data[0].component_index == 0


def test_bridging_empty_region_zero() -> None:
    data = compute_bridging_potential([[]], [["a"]])["data"]
    assert data[0].score == 0.0
    assert data[0].component_index == -1


def test_bridging_region_not_found_is_one() -> None:
    data = compute_bridging_potential([["z"]], [["a"], ["b"]])["data"]
    assert data[0].score == 1.0
    assert data[0].component_index == -1


def test_bridging_empty_components_is_one() -> None:
    data = compute_bridging_potential([["x"]], [])["data"]
    assert data[0].score == 1.0


# ---------------------------------------------------------------------------
# UCB + composite score
# ---------------------------------------------------------------------------


def test_ucb_edge_cases() -> None:
    assert compute_ucb(0, 0) == 1.0
    assert compute_ucb(5, 0) == 1.0  # no invocations yet
    assert compute_ucb(0, 10) == 1.0  # unvisited region
    assert compute_ucb(2, 100) == 1.0  # raw > 1 is clamped


def test_ucb_computed_value() -> None:
    expected = 1.41 * math.sqrt(math.log(100) / 50)
    assert math.isclose(compute_ucb(50, 100), expected, rel_tol=1e-12)
    assert compute_ucb(50, 100) < 1.0


def test_composite_score_weight_normalization() -> None:
    # ageStaleness=0.75(0.3) + bridging=1.0(0.3) + ucb=1.0(0.2); coverage/purpose absent.
    score = compute_composite_score(0.75, 1.0, None, 1.0, None)
    assert math.isclose(score, 0.90625, abs_tol=1e-12)


def test_composite_score_all_present() -> None:
    score = compute_composite_score(0.5, 0.5, 0.5, 0.5, 0.5)
    assert math.isclose(score, 0.5, abs_tol=1e-12)


# ---------------------------------------------------------------------------
# CoverageGap
# ---------------------------------------------------------------------------


def test_coverage_gap_under_two_vectors_scores_zero() -> None:
    store = FakeVectorStore({"a": [1.0, 0.0]})
    data = compute_coverage_gap([["a"]], store)["data"]
    assert data[0].score == 0.0


def test_coverage_gap_identical_internal_with_external_is_one() -> None:
    store = FakeVectorStore({"a": [1.0, 0.0], "b": [1.0, 0.0], "c": [0.0, 1.0]})
    data = compute_coverage_gap([["a", "b"], ["c"]], store)["data"]
    assert data[0].score == 1.0  # internal distance 0, external exists
    assert data[1].score == 0.0  # single vector


def test_coverage_gap_computed_ratio() -> None:
    store = FakeVectorStore({"a": [1.0, 0.0], "b": [0.0, 1.0], "c": [1.0, 0.0], "d": [0.0, 1.0]})
    data = compute_coverage_gap([["a", "b"], ["c", "d"]], store)["data"]
    # internal dist = 1.0 (orthogonal pair); external avg dist = 0.5 -> gap 0.5.
    assert math.isclose(data[0].score, 0.5, abs_tol=1e-12)
    assert math.isclose(data[1].score, 0.5, abs_tol=1e-12)


def test_coverage_gap_availability_signal() -> None:
    assert embeddings_available(FakeVectorStore({"a": [1.0, 0.0]})) is True
    assert embeddings_available(FakeVectorStore({})) is False
    failed = coverage_gap_unavailable()
    assert failed["data"] is None
    assert failed["error"] == COVERAGE_GAP_UNAVAILABLE_MESSAGE
    assert COVERAGE_GAP_UNAVAILABLE_MESSAGE == (
        "Embedding pipeline not available -- cannot compute CoverageGap"
    )


# ---------------------------------------------------------------------------
# ExplorationStateStore (zeroed-state design decision)
# ---------------------------------------------------------------------------


def test_exploration_state_starts_zeroed() -> None:
    store = ExplorationStateStore("default")
    components = [["a", "b"], ["c"]]
    region_states = store.aggregate_to_regions(components)

    assert len(region_states) == len(components)
    assert store.get_state().total_invocations == 0
    assert region_states[0].entity_ids == ["a", "b"]
    assert region_states[0].total_visits == 0
    assert region_states[0].average_gain is None
    assert region_states[0].visited_entity_count == 0
    assert region_states[0].entity_count == 2
    assert store.get_region_gain_history(region_key(["b", "a"])) == []


def test_region_key() -> None:
    assert region_key(["b", "a", "c"]) == "a"
    assert region_key([]) == ""


def test_exploration_state_records_visits_and_gains() -> None:
    store = ExplorationStateStore()
    store.record_visit("a", 0.5)
    store.record_visit("a", 0.7)
    region_state = store.aggregate_to_regions([["a", "b"]])[0]
    assert region_state.total_visits == 2
    assert region_state.visited_entity_count == 1
    assert region_state.average_gain == 0.6

    store.record_region_gain("a", 0.3)
    assert store.get_region_gain_history("a") == [0.3]


# ---------------------------------------------------------------------------
# MVT patch-leaving
# ---------------------------------------------------------------------------


def test_mvt_cold_start_uses_staleness_proxy() -> None:
    entities = [{"id": "a", "updated_at": "2026-01-01T00:00:00.000Z"}]  # 30 days -> 0.75
    result = compute_mvt_patch_leaving([["a"]], [[]], entities, now=REF_NOW)
    rec = result["data"][0]
    assert rec.should_leave is False
    assert math.isclose(rec.marginal_gain, 0.75, abs_tol=1e-12)
    assert rec.recommendation == "Cold-start: staleness proxy = 0.75, awaiting gain history"


def test_mvt_steady_state_leave_and_stay() -> None:
    result = compute_mvt_patch_leaving([["a"], ["b"]], [[0.5, 0.8], [0.9, 0.2]], entities=[])
    stay, leave = result["data"]
    # region a marginal = 0.30 >= avg -0.20 -> stay
    assert stay.should_leave is False
    assert stay.recommendation == "Stay: marginal gain (0.30) at or above average (-0.20)"
    # region b marginal = -0.70 < avg -0.20 -> leave
    assert leave.should_leave is True
    assert leave.recommendation == "Leave: marginal gain (-0.70) below average (-0.20)"


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def test_comfort_zone_fires_on_few_types() -> None:
    # Two real EntityType values -> 2/19 represented, 17 missing.
    regions = [zeroed_region(["a", "b"]), zeroed_region(["c"])]
    entity_type_map = {"a": "concept", "b": "concept", "c": "claim"}
    result = detect_comfort_zone(regions, entity_type_map)
    assert result is not None
    assert result.pattern == "comfort_zone"
    assert result.severity == "medium"  # 2/19 ~ 0.105 -> medium band
    assert "only 2/19 entity types" in result.recommendation
    assert "represented (claim, concept)" in result.recommendation  # present, sorted
    assert "and 12 more" in result.recommendation  # 17 missing - 5 shown


def test_comfort_zone_not_fires_when_diverse() -> None:
    # 6 distinct types -> 6/19 ~ 0.316 >= 0.3 threshold -> no detection.
    entity_type_map = {
        "a": "concept",
        "b": "claim",
        "c": "source",
        "d": "question",
        "e": "evidence",
        "f": "pattern",
    }
    regions = [zeroed_region(list(entity_type_map.keys()))]
    assert detect_comfort_zone(regions, entity_type_map) is None
    assert detect_comfort_zone(regions, {}) is None  # no type data


def test_echo_chamber_fires_on_low_diversity() -> None:
    regions = [visited_region([f"r{i}"], 6 if i == 0 else 0) for i in range(10)]
    result = detect_echo_chamber(regions)
    assert result is not None
    assert result.pattern == "echo_chamber"
    assert result.severity == "low"  # visit ratio 6/5 = 1.2
    assert "1 region(s)" in result.recommendation
    assert "up to 6 visits" in result.recommendation
    assert "only 1/10 regions" in result.recommendation


def test_echo_chamber_not_fires_without_hot_regions() -> None:
    regions = [zeroed_region(["a"]), zeroed_region(["b"])]
    assert detect_echo_chamber(regions) is None


def test_run_guards_on_zeroed_state_flags_comfort_zone_only() -> None:
    regions = [zeroed_region(["a", "b"]), zeroed_region(["c"])]
    entity_type_map = {"a": "concept", "b": "concept", "c": "claim"}
    output = run_guards(
        regions=regions,
        coverage_gap_scores=None,
        entity_type_map=entity_type_map,
        gain_history_by_region={},
    )
    assert output.skipped is False
    assert output.tier_b_available is False
    assert [ap.pattern for ap in output.anti_patterns] == ["comfort_zone"]


def test_run_guards_skips_when_disabled() -> None:
    output = run_guards(
        regions=[zeroed_region(["a"])],
        coverage_gap_scores=None,
        entity_type_map={"a": "concept"},
        gain_history_by_region={},
        include_anti_patterns=False,
    )
    assert output.skipped is True
    assert output.anti_patterns == []
