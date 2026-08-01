"""SKIP/LIMIT paging for reads that must never be truncated.

FalkorDB caps every result set at the server's RESULTSET_SIZE (default
10000) and silently drops rows past it, so any full-scan read on a large
graph returns wrong answers unless it pages. The cap applies to the
post-SKIP window, so paging with SKIP is sound under any cap value.

Queries passed here must carry a deterministic ORDER BY. Pages advance by
rows actually received (the server may cap a page below the requested
LIMIT) and stop on an empty page.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

PAGE_SIZE = 10_000

Rows = list[list[Any]]
RunQuery = Callable[[str, dict[str, Any]], Rows]


def fetch_all_rows(
    run: RunQuery,
    cypher: str,
    params: dict[str, Any] | None = None,
    limit: int | None = None,
) -> Rows:
    """All rows of an ORDER BY-carrying query, capped at ``limit`` if given."""
    rows: Rows = []
    skip = 0
    while True:
        want = PAGE_SIZE if limit is None else min(PAGE_SIZE, limit - len(rows))
        if want <= 0:
            return rows
        page = run(
            f"{cypher} SKIP $_skip LIMIT $_limit",
            {**(params or {}), "_skip": skip, "_limit": want},
        )
        rows.extend(page)
        if not page:
            return rows
        skip += len(page)
