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


class ClientNotFoundError(BankError):
    """Raised when the bank has no client with the given id."""

    def __init__(self, client_id: str) -> None:
        self.client_id = client_id
        super().__init__(f"Client {client_id} not found.")


class AccountNotFoundError(BankError):
    """Raised when the bank has no account with the given id."""

    def __init__(self, account_id: str) -> None:
        self.account_id = account_id
        super().__init__(f"Account {account_id} not found.")


class AuthenticationError(BankError):
    """Raised when a client presents a wrong password.

    ``attempts_left`` tells how many more failures the client may make before
    being blocked.
    """

    def __init__(self, client_id: str, attempts_left: int) -> None:
        self.client_id = client_id
        self.attempts_left = attempts_left
        super().__init__(f"Wrong password for client {client_id}; {attempts_left} attempt(s) left before blocking.")


class ClientBlockedError(BankError):
    """Raised when a blocked client tries to log in or to operate."""

    def __init__(self, client_id: str) -> None:
        self.client_id = client_id
        super().__init__(f"Client {client_id} is blocked; operation rejected.")


class OperationTimeRestrictedError(BankError):
    """Raised when an operation is attempted inside the restricted night window."""

    def __init__(self, action: str, window: str) -> None:
        self.action = action
        self.window = window
        super().__init__(f"Operation {action!r} is not allowed between {window}.")


class TransactionNotFoundError(BankError):
    """Raised when the queue has no transaction with the given id."""

    def __init__(self, transaction_id: str) -> None:
        self.transaction_id = transaction_id
        super().__init__(f"Transaction {transaction_id} not found.")


class InvalidTransactionStateError(InvalidOperationError):
    """Raised when a transaction cannot move from its current status to the requested one."""

    def __init__(self, transaction_id: str, current: str, target: str) -> None:
        self.transaction_id = transaction_id
        self.current = current
        self.target = target
        super().__init__(f"Transaction {transaction_id} cannot move from {current} to {target}.")
