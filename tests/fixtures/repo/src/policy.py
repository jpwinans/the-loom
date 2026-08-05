"""Policy checks applied before a transfer."""

MAX_TRANSFER = 10000.0


def allows(amount: float) -> bool:
    """Return True when the amount is within policy."""
    # WHY: the ceiling is regulatory, not technical — see RFC-0042.
    return amount <= MAX_TRANSFER
