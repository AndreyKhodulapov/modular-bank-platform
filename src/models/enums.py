"""Enumerations used by the domain models."""

from enum import Enum


class AccountStatus(Enum):
    """Lifecycle state of a bank account."""

    ACTIVE = "active"
    FROZEN = "frozen"
    CLOSED = "closed"


class ClientStatus(Enum):
    """Access state of a bank client."""

    ACTIVE = "active"
    BLOCKED = "blocked"


class Currency(Enum):
    """Currencies supported by the platform (ISO 4217 codes)."""

    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"
    KZT = "KZT"
    CNY = "CNY"


class AssetType(Enum):
    """Virtual asset classes an investment portfolio can hold."""

    STOCKS = "stocks"
    BONDS = "bonds"
    ETF = "etf"


class TransactionType(Enum):
    """What a transaction does with money.

    ``EXTERNAL_TRANSFER`` sends money to an account in another bank: only the
    sender's side is booked here and a fee is charged.
    """

    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    TRANSFER = "transfer"
    EXTERNAL_TRANSFER = "external_transfer"


class TransactionStatus(Enum):
    """Lifecycle state of a transaction."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TransactionPriority(Enum):
    """Execution priority in the queue; a larger value runs first."""

    LOW = 0
    NORMAL = 1
    HIGH = 2
    URGENT = 3
