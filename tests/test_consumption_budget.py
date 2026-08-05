"""Pure budget/truncation algebra: no store, no docker, milliseconds.

These pin the arithmetic that governs how consumption commands degrade under
a budget — round-robin allocation (with and without a per-section floor),
grouped-count rollups, the shared truncation-block shape, and the hub
percentile threshold — independent of any live graph.
"""

from __future__ import annotations

from theloom.operations.consumption_budget import (
    allocate_rows,
    json_cost,
    percentile_threshold,
    rollup,
    truncation_block,
)

# =============================================================================
# allocate_rows
# =============================================================================


def test_allocate_rows_with_floor_guarantees_first_row_then_spreads_evenly() -> None:
    """explore's policy: the first row of every populated section is free,
    then round-robin spends the remaining budget one row at a time, blocking
    a section (but not the others) once its next row would overrun.

    sections: a = four rows costing 1 each, b = one row costing 3, c = empty.
    budget = 6. Floor pre-spends a[0] (1) + b[0] (3) = 4, leaving 2 to spend
    one row at a time on a (the only section with anything left to take):
    a gets two more rows (spend 1, then 1, total 6) and its fourth row (cost
    1, would make 7) is blocked. b and c take no more.
    """
    sections = [[1, 1, 1, 1], [3], []]
    counts = allocate_rows(sections, budget=6, cost=lambda x: x, floor=True)
    assert counts == [3, 1, 0]


def test_allocate_rows_with_floor_is_unconditional_even_over_budget() -> None:
    """The floor is a promise, not a suggestion: a section's first row is
    taken even when it alone blows the budget, and nothing further is added."""
    sections = [[100], [1, 1]]
    counts = allocate_rows(sections, budget=1, cost=lambda x: x, floor=True)
    assert counts == [1, 1]


def test_allocate_rows_without_floor_spreads_from_zero() -> None:
    """blast-radius's policy: no guaranteed first row, unit cost per row,
    budget is a row count (the limit). Three groups of sizes 3/1/2, budget 4:
    round 1 takes one from each (x1, y1, z1 — 3 spent, 1 left); round 2 takes
    only x2 (the first group still has room) and the budget is exhausted
    before y or z are reconsidered.
    """
    sections = [["x1", "x2", "x3"], ["y1"], ["z1", "z2"]]
    counts = allocate_rows(sections, budget=4, floor=False)
    assert counts == [2, 1, 1]


def test_allocate_rows_without_floor_can_starve_a_section() -> None:
    """No floor means a narrow budget can leave a populated section at zero —
    the opposite of the floor policy, and the whole reason the two modes
    must stay distinct rather than merging into one default."""
    sections = [["a"], ["b1", "b2", "b3"]]
    counts = allocate_rows(sections, budget=1, floor=False)
    # Round-robin visits section 0 first each round, so with budget 1 only
    # "a" is ever taken.
    assert counts == [1, 0]


def test_allocate_rows_stops_when_every_section_is_exhausted() -> None:
    sections = [["a", "b"], ["c"]]
    counts = allocate_rows(sections, budget=100, floor=False)
    assert counts == [2, 1]


# =============================================================================
# rollup
# =============================================================================


def test_rollup_groups_and_sorts_by_count_then_key() -> None:
    rows = [
        {"file": "b.py"},
        {"file": "a.py"},
        {"file": "b.py"},
        {"file": "c.py"},
    ]
    assert rollup(rows, key="file") == [
        {"file": "b.py", "count": 2},
        {"file": "a.py", "count": 1},
        {"file": "c.py", "count": 1},
    ]


def test_rollup_uses_unknown_placeholder_for_missing_key() -> None:
    rows = [{"file": None}, {}]
    assert rollup(rows, key="file", unknown="(unknown)") == [{"file": "(unknown)", "count": 2}]


def test_rollup_caps_entries_and_bundles_the_remainder() -> None:
    rows = [{"file": f"f{i}.py"} for i in range(14)]
    entries = rollup(rows, key="file", max_entries=12)
    assert len(entries) == 13  # 12 named + one "(N more)" bucket
    assert entries[-1] == {"file": "(2 more)", "count": 2}
    assert sum(entry["count"] for entry in entries) == 14


# =============================================================================
# truncation_block
# =============================================================================


def test_truncation_block_default_applied_is_shown_less_than_total() -> None:
    block = truncation_block(shown=3, total=8, hint="3 rows shown of 8.")
    assert block == {"applied": True, "shown": 3, "total": 8, "hint": "3 rows shown of 8."}


def test_truncation_block_omits_cut_when_not_given() -> None:
    block = truncation_block(shown=1, total=1, hint="Nothing was cut.")
    assert "cut" not in block
    assert block["applied"] is False


def test_truncation_block_includes_cut_when_given() -> None:
    block = truncation_block(shown=5, total=8, hint="h", cut={"callersIn": 3})
    assert block["cut"] == {"callersIn": 3}


def test_truncation_block_applied_can_be_forced_true_past_shown_equals_total() -> None:
    """blast-radius: a suppressed hub withholds a whole subtree that enters
    neither shown nor total, so applied must be forceable even when the two
    numbers already agree."""
    block = truncation_block(shown=4, total=4, hint="a hub was withheld", applied=True)
    assert block["applied"] is True


# =============================================================================
# percentile_threshold
# =============================================================================


def test_percentile_threshold_hand_worked_example() -> None:
    # sorted degrees: [1, 1, 1, 2, 2, 13]; 99th percentile index =
    # floor(0.99 * 5) = 4 -> value 2.
    assert percentile_threshold([13, 1, 2, 1, 2, 1], 99.0) == 2


def test_percentile_threshold_of_empty_is_zero() -> None:
    assert percentile_threshold([], 99.0) == 0


# =============================================================================
# json_cost
# =============================================================================


def test_json_cost_is_the_compact_json_byte_length() -> None:
    assert json_cost({"a": 1}) == len('{"a":1}')
