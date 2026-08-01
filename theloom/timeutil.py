"""Timestamps in the canonical wire format.

Every timestamp is ISO 8601 with millisecond precision and a Z suffix. Using a
single fixed shape keeps documents byte-comparable and lexicographically ordered.
"""

from __future__ import annotations

from datetime import UTC, datetime


def iso_now() -> str:
    """Current UTC time as YYYY-MM-DDTHH:MM:SS.mmmZ."""
    now = datetime.now(UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
