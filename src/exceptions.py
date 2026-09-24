"""Custom exception hierarchy of the bank platform.

Every domain error inherits from ``BankError`` so callers can catch the whole
family with a single ``except BankError`` clause when they do not care about
the exact reason.
"""

from decimal import Decimal


class BankError(Exception):
    """Base class for all domain errors raised by the bank platform."""


class InvalidOperationError(BankError):
    """Raised when input data or a requested action violates business rules."""


class AccountFrozenError(BankError):
    """Raised when an operation is attempted on a frozen account."""

    def __init__(self, account_id: str) -> None:
        self.account_id = account_id
        super().__init__(f"Account {account_id} is frozen; operation rejected.")


class AccountClosedError(BankError):
    """Raised when an operation is attempted on a closed account."""

    def __init__(self, account_id: str) -> None:
        self.account_id = account_id
        super().__init__(f"Account {account_id} is closed; operation rejected.")


class InsufficientFundsError(BankError):
    """Raised when a debit exceeds the funds available for it.

    ``available`` is the largest amount the same operation would accept.
    ``hint`` optionally explains why it is smaller than the balance (for
    example that part of the money is locked in a portfolio).
    """

    def __init__(self, requested: Decimal, available: Decimal, hint: str | None = None) -> None:
        self.requested = requested
        self.available = available
        message = f"Insufficient funds: requested {requested}, available {available}."
        if hint:
            message = f"{message} {hint}"
        super().__init__(message)


class LimitExceededError(BankError):
    """Raised when a single operation exceeds the per-operation limit of the account."""

    def __init__(self, requested: Decimal, limit: Decimal) -> None:
        self.requested = requested
        self.limit = limit
        super().__init__(f"Operation limit exceeded: requested {requested}, limit {limit}.")
