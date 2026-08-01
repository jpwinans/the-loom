"""Numeric formatting helpers that mirror JavaScript rounding semantics exactly.

The exploration signals build human-readable recommendation strings with
``Number.prototype.toFixed`` and ``Math.round`` and compute a rounded
``daysSinceUpdate`` with ``Math.round``. Python's built-in ``round`` uses
banker's rounding and ``f"{x:.2f}"`` uses round-half-to-even, so the JS rounding
rules are reproduced here for stable, consistent output.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal


def js_round(value: float) -> int:
    """Reproduce ``Math.round``: round half toward +Infinity.

    ``Math.round(2.5) === 3`` and ``Math.round(-0.5) === 0`` — i.e. ties always
    go up, never to even. ``math.floor(value + 0.5)`` matches this exactly for
    the non-negative magnitudes used here.
    """
    return math.floor(value + 0.5)


def to_fixed(value: float, digits: int) -> str:
    """Reproduce ``Number.prototype.toFixed``: fixed decimals, ties away from 0.

    Operates on the exact IEEE-754 value (``Decimal(value)``) and quantizes with
    ``ROUND_HALF_UP`` (round half away from zero), which is what ``toFixed``
    does for the finite, in-range magnitudes produced by the signals.
    """
    quantum = Decimal(1).scaleb(-digits)
    return str(Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP))


def now_iso() -> str:
    """UTC ISO-8601 stamp with a trailing ``Z`` (matches JS ``toISOString()``)."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def to_iso_z(moment: datetime) -> str:
    """Format an aware datetime the way JS ``Date.prototype.toISOString`` does.

    Always three-digit milliseconds and a ``Z`` suffix, e.g.
    ``2026-01-01T00:00:00.000Z``.
    """
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    """Parse a stored ISO-8601 UTC timestamp into an aware datetime.

    Mirrors the rest of the codebase (``.replace("Z", "+00:00")``) so the ``Z``
    suffix is accepted on every supported Python version.
    """
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
