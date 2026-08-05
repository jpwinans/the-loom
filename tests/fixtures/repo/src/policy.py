"""Policy checks applied before a transfer."""

MAX_TRANSFER = 10000.0


def allows(amount: float) -> bool:
    """Return True when the amount is within policy."""
    # WHY: the ceiling is regulatory, not technical — see RFC-0042.
    return amount <= MAX_TRANSFER


def under_review(amount: float) -> str:
    """Return the review state for an amount."""
    return "under_review" if amount > MAX_TRANSFER else "cleared"
