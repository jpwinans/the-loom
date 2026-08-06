"""Hybrid-search ranking stages as pure functions.

Each stage is exercised on plain dicts with orderings worked out by hand — no
store, no embedder, no ANN index. hybrid_search itself is fetch-then-rank over
these.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from theloom.semantic.ranking import (
    Neighbor,
    apply_mmr,
    apply_recency_boost,
    assign_quality_groups,
    expand_by_graph,
    fuse_scores,
    select_seeds,
)


def _adjacency(graph: dict[str, list[Neighbor]]):  # type: ignore[no-untyped-def]
    def neighbors_of(entity_id: str) -> Sequence[Neighbor]:
        return graph.get(entity_id, [])

    return neighbors_of


def test_expand_by_graph_scores_one_over_hop_and_skips_seeds() -> None:
    """Two hops from one seed: direct neighbours score 1/1, their neighbours
    1/2, and an edge back to a seed is not a result."""
    graph = {
        "seed": [("a", "A", "concept"), ("b", "B", "claim")],
        "a": [("c", "C", "concept"), ("seed", "Seed", "concept")],
    }

    expanded = expand_by_graph(["seed"], _adjacency(graph), hops=2)

    assert {row_id: row["score"] for row_id, row in expanded.items()} == {
        "a": 1.0,
        "b": 1.0,
        "c": 0.5,
    }
    assert expanded["c"] == {
        "entityId": "c",
        "name": "C",
        "entityType": "concept",
        "hopDistance": 2,
        "expandedFrom": "seed",
        "score": 0.5,
    }


def test_expand_by_graph_keeps_the_shortest_hop_and_caps_the_fan_out() -> None:
    """A node reachable at hop 1 and hop 2 keeps the hop-1 score; each node
    contributes at most ``max_neighbors`` edges to the frontier."""
    graph = {
        "seed": [("a", "A", "concept"), ("b", "B", "concept"), ("c", "C", "concept")],
        "a": [("b", "B", "concept")],
    }

    capped = expand_by_graph(["seed"], _adjacency(graph), hops=2, max_neighbors=2)

    assert set(capped) == {"a", "b"}
    assert capped["b"]["hopDistance"] == 1
    assert capped["b"]["score"] == 1.0


WEIGHTS = {"vector": 0.6, "keyword": 0.25, "graph": 0.15}


def test_select_seeds_ranks_by_the_vector_keyword_blend_and_caps_the_count() -> None:
    """Seeds are the top half of the vector hits (max 5) ranked by
    0.6*vector + 0.25*keyword: 0.55 for a, 0.48 for b, 0.47 for c — so with
    three hits the two seeds are a and b, not the best vector score alone."""
    vector_rows = [
        {"entityId": "a", "score": 0.5},
        {"entityId": "b", "score": 0.8},
        {"entityId": "c", "score": 0.7},
    ]
    keyword_matches = {
        "a": {"score": 1.0, "matchedTerms": ["x"]},
        "c": {"score": 0.2, "matchedTerms": ["x"]},
    }

    assert select_seeds(vector_rows, keyword_matches, WEIGHTS) == ["a", "b"]


def test_fuse_scores_blends_signals_and_labels_the_match_source() -> None:
    """0.6*vector + 0.25*keyword + 0.15*graph, worked by hand:
    a = .6*.8 + .25*.5 + .15*.5 = .68 and b = .15*1.0 = .15."""
    vector_rows = [
        {
            "entityId": "a",
            "name": "Alpha",
            "entityType": "concept",
            "entryType": "entity",
            "score": 0.8,
        }
    ]
    keyword_matches = {"a": {"score": 0.5, "matchedTerms": ["alpha"]}}
    graph_rows = {
        "a": {
            "entityId": "a",
            "name": "Alpha",
            "entityType": "concept",
            "hopDistance": 2,
            "expandedFrom": "z",
            "score": 0.5,
        },
        "b": {
            "entityId": "b",
            "name": "Beta",
            "entityType": "claim",
            "hopDistance": 1,
            "expandedFrom": "a",
            "score": 1.0,
        },
    }

    fused = fuse_scores(vector_rows, keyword_matches, graph_rows, WEIGHTS)

    assert [row["entityId"] for row in fused] == ["a", "b"]
    assert fused[0]["score"] == pytest.approx(0.68)
    assert fused[0]["scores"] == {"vector": 0.8, "keyword": 0.5, "graph": 0.5}
    assert fused[0]["matchSource"] == "semantic+keyword+graph"
    assert fused[0]["matchedTerms"] == ["alpha"]
    assert fused[0]["hopDistance"] == 2
    assert fused[1]["score"] == pytest.approx(0.15)
    assert fused[1]["matchSource"] == "graph"
    assert fused[1]["scores"] == {"vector": 0.0, "keyword": 0.0, "graph": 1.0}


def test_recency_boost_decays_by_half_life_and_reorders() -> None:
    """An entity as old as one half-life gets half the boost: 1 + 0.15*0.5 =
    1.075 against 1 + 0.15*1 = 1.15 for one timestamped 'now'. A row with no
    timestamp is left alone, which is what drops it below both."""
    now = datetime(2026, 1, 8, tzinfo=UTC)
    rows = [
        {"entityId": "stale", "score": 1.0},
        {"entityId": "fresh", "score": 1.0},
        {"entityId": "undated", "score": 1.0},
    ]
    timestamps = {"stale": "2026-01-01T00:00:00Z", "fresh": "2026-01-08T00:00:00Z"}

    boosted = apply_recency_boost(
        rows,
        timestamps,
        now_ms=now.timestamp() * 1000,
        max_boost=0.15,
        half_life_days=7,
    )

    assert [row["entityId"] for row in boosted] == ["fresh", "stale", "undated"]
    assert [row["score"] for row in boosted] == [
        pytest.approx(1.15),
        pytest.approx(1.075),
        1.0,
    ]


def test_mmr_prefers_a_different_second_result_over_a_near_duplicate() -> None:
    """lambda 0.5 over normalized scores 1.0/0.9/0.8. Jaccard on name+type
    tokens: the runner-up overlaps the leader 3/4 = 0.75, the third row 0/6.
    MMR = 0.5*0.9 - 0.5*0.75 = 0.075 against 0.5*0.8 - 0 = 0.4, so the third
    row is picked second even though it scores lower."""
    rows = [
        {"entityId": "1", "name": "Neural Networks", "entityType": "concept", "score": 1.0},
        {"entityId": "2", "name": "Neural Networks Deep", "entityType": "concept", "score": 0.9},
        {"entityId": "3", "name": "Ocean Currents", "entityType": "system", "score": 0.8},
    ]

    assert [row["entityId"] for row in apply_mmr(rows, 0.5, limit=2)] == ["1", "3"]
    assert [row["entityId"] for row in apply_mmr(rows, 1.0, limit=2)] == ["1", "2"]


def test_quality_groups_split_on_gaps_wider_than_the_mean() -> None:
    """Scores .9/.85/.4/.35 have gaps .05/.45/.05, mean gap .18333; the
    'similar' threshold is .18333*1.5*1.5 = .4125 (well above the .05 floor), so
    only the .45 gap splits — two groups, numbered from the top."""
    rows = [
        {"entityId": "a", "score": 0.9},
        {"entityId": "b", "score": 0.85},
        {"entityId": "c", "score": 0.4},
        {"entityId": "d", "score": 0.35},
    ]

    grouped, count = assign_quality_groups(rows, "similar")

    assert count == 2
    assert [(row["entityId"], row["qualityGroup"]) for row in grouped] == [
        ("a", 1),
        ("b", 1),
        ("c", 2),
        ("d", 2),
    ]


def test_quality_groups_of_one_row_is_a_single_group() -> None:
    grouped, count = assign_quality_groups([{"entityId": "a", "score": 0.5}], "similar")

    assert count == 1
    assert grouped[0]["qualityGroup"] == 1
