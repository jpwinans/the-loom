"""Transfer service built on the domain models."""

from src.models import Account, open_account


def transfer(source: Account, target: Account, amount: float) -> None:
    source.balance -= amount
    target.deposit(amount)


def onboard(name: str) -> Account:
    account = open_account(name)
    account.deposit(10.0)
    return account
