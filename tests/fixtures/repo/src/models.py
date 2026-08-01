"""Domain models for the sample service."""

from dataclasses import dataclass


@dataclass
class Account:
    """A user account."""

    name: str
    balance: float

    def deposit(self, amount: float) -> float:
        """Add funds and return the new balance."""
        self.balance += amount
        return self.balance


def open_account(name: str) -> Account:
    """Create an empty account."""
    return Account(name=name, balance=0.0)
