"""Budget/truncation algebra: plain-data arithmetic, no store.

The single implementation behind two policies that consumption.py's four
commands need:

- **explore** — a byte budget, spent round-robin across sections, with the
  first row of every populated section unconditional (a section that exists
  must be visible, whatever the budget says).
- **blast-radius** — a row-count limit, spent round-robin across per-module
  groups, with no such floor (a narrow limit can legitimately leave a module
  unrepresented).

``allocate_rows`` is the one round-robin allocator both policies configure
(``floor=True`` for the first, ``floor=False`` for the second) rather than
two independent copies of the same loop. ``rollup``, ``truncation_block`` and
``percentile_threshold`` are the other pieces of arithmetic that used to be
hand-rolled per command: grouped counts for what did not fit, the shared
shape of the "here is what was cut and how to widen it" block, and the
degree cutoff blast-radius's hub rule compares against.

Every function here takes and returns plain dicts/lists/ints — no Entity, no
Relation, no store — so it is testable in milliseconds.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

T = TypeVar("T")


def json_cost(value: Any) -> int:
    """The compact-JSON byte length of a value — the currency ``allocate_rows``
    spends against a byte budget."""
    return len(json.dumps(value, separators=(",", ":")))


def allocate_rows(
    sections: Sequence[Sequence[T]],
    budget: int,
    cost: Callable[[T], int] = lambda _row: 1,
    *,
    floor: bool = False,
) -> list[int]:
    """How many rows of each section (in order) fit ``budget``.

    Round-robin: one row per section per pass, so a wide section can never
    eat the budget before a narrower one is reached. A section whose next row
    would overrun the budget is blocked from further consideration — the
    other sections keep going.

    ``floor=True`` unconditionally takes the first row of every non-empty
    section before the round-robin starts, even if that row alone exceeds
    ``budget``: a section that exists must be visible no matter how small the
    budget is. ``floor=False`` starts every section at zero, so a narrow
    budget can leave a populated section unrepresented.
    """
    counts = [0] * len(sections)
    spent = 0
    if floor:
        for index, rows in enumerate(sections):
            if rows:
                counts[index] = 1
                spent += cost(rows[0])
    blocked = [False] * len(sections)
    while True:
        progressed = False
        for index, rows in enumerate(sections):
            if blocked[index] or len(rows) <= counts[index]:
                continue
            row_cost = cost(rows[counts[index]])
            if spent + row_cost > budget:
                blocked[index] = True  # this row is too big; smaller rows elsewhere may still fit
                continue
            counts[index] += 1
            spent += row_cost
            progressed = True
        if not progressed:
            return counts


def rollup(
    rows: Sequence[dict[str, Any]],
    key: str,
    *,
    max_entries: int = 12,
    unknown: str = "(unknown)",
) -> list[dict[str, Any]]:
    """Grouped counts for rows that did not fit: the count and the group
    survive even when the individual rows do not. Grouped by ``row[key]``
    (``unknown`` when absent), sorted by count descending then name; past
    ``max_entries`` groups the tail collapses into one ``"(N more)"`` entry
    so the rollup itself cannot grow unbounded."""
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[str(row.get(key) or unknown)] += 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    if len(ordered) <= max_entries:
        return [{key: name, "count": count} for name, count in ordered]
    head = ordered[:max_entries]
    rest = sum(count for _, count in ordered[max_entries:])
    entries = [{key: name, "count": count} for name, count in head]
    entries.append({key: f"({len(ordered) - max_entries} more)", "count": rest})
    return entries


def truncation_block(
    *,
    shown: int,
    total: int,
    hint: str,
    applied: bool | None = None,
    cut: dict[str, int] | None = None,
) -> dict[str, Any]:
    """The shared truncation-block shape: what was cut and how to widen it.

    ``applied`` defaults to ``shown < total`` but can be forced — blast-radius
    needs it ``True`` even when every reached row is listed, because a
    suppressed hub withholds a whole subtree that enters neither number."""
    block: dict[str, Any] = {
        "applied": applied if applied is not None else shown < total,
        "shown": shown,
        "total": total,
    }
    if cut is not None:
        block["cut"] = cut
    block["hint"] = hint
    return block


def percentile_threshold(values: Sequence[int], percentile: float) -> int:
    """The value at ``percentile`` in ``values`` — the degree cutoff
    blast-radius's hub rule refuses to expand through. 0 on an empty
    population (percentiles are meaningless with nothing to rank)."""
    if not values:
        return 0
    ordered = sorted(values)
    index = int(math.floor(percentile / 100 * (len(ordered) - 1)))
    return ordered[index]
