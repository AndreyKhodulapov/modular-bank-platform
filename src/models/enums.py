"""Enumerations used by the account models."""

from enum import Enum


class AccountStatus(Enum):
    """Lifecycle state of a bank account."""

    ACTIVE = "active"
    FROZEN = "frozen"
    CLOSED = "closed"


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
