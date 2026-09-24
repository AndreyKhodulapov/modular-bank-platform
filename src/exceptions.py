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
    """Raised when a withdrawal exceeds the available balance."""

    def __init__(self, requested: Decimal, available: Decimal) -> None:
        self.requested = requested
        self.available = available
        super().__init__(f"Insufficient funds: requested {requested}, available {available}.")
