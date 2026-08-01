"""Composite framework primitives.

Composites bundle several internal operations into one CLI call and return a
structured result with per-section timing and error metadata.

Design goals:
- **Resilience:** if one section fails, the others still execute — every section
  runs inside :func:`time_section`, which captures exceptions instead of raising.
- **Observability:** every section reports its wall-clock timing and error status.

These primitives live in a neutral shared module so lower layers never import
composite machinery (no framework-util layering leak).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

T = TypeVar("T")

# A section's outcome: {data, durationMs, error}. `data` is None on failure.
SectionResult = dict[str, Any]
# A composite's outcome: {result: {section: SectionResult, ...}, metadata: {...}}.
CompositeResult = dict[str, Any]


def _now_iso() -> str:
    """UTC ISO-8601 stamp with a trailing ``Z``.

    Test normalization collapses this to ``«ts»``; the value only needs to be
    a valid ISO timestamp.
    """
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def time_section(fn: Callable[[], T]) -> SectionResult:
    """Run ``fn`` and wrap its result with timing and error metadata.

    Never raises: an exception is captured as the ``error`` string with
    ``data=None`` (the exception message, or its string form).
    """
    start = time.perf_counter()
    try:
        data = fn()
        duration_ms = round((time.perf_counter() - start) * 1000)
        return {"data": data, "durationMs": duration_ms, "error": None}
    except Exception as err:  # noqa: BLE001 — resilience is the whole point.
        duration_ms = round((time.perf_counter() - start) * 1000)
        message = str(err) if str(err) else err.__class__.__name__
        return {"data": None, "durationMs": duration_ms, "error": message}


def failed_section(message: str) -> SectionResult:
    """A section that could not even be attempted (missing prerequisite)."""
    return {"data": None, "durationMs": 0, "error": message}


def build_composite_result(
    sections: dict[str, SectionResult], total_duration_ms: int
) -> CompositeResult:
    """Assemble the final composite envelope with aggregate metadata."""
    succeeded = sum(1 for s in sections.values() if s.get("error") is None)
    failed = sum(1 for s in sections.values() if s.get("error") is not None)
    return {
        "result": sections,
        "metadata": {
            "totalDurationMs": total_duration_ms,
            "sectionsSucceeded": succeeded,
            "sectionsFailed": failed,
            "executedAt": _now_iso(),
        },
    }
