"""Bank account models: the abstract base and its first concrete implementation."""

import uuid
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any

from exceptions import (
    AccountClosedError,
    AccountFrozenError,
    InsufficientFundsError,
    InvalidOperationError,
)
from models.enums import AccountStatus, Currency
from models.owner import Owner
from utils import to_money


class AbstractAccount(ABC):
    """Abstract base for every account type in the platform.

    Holds the state common to all accounts (identifier, owner, protected
    balance, status) and declares the operations each concrete account must
    implement. The balance is deliberately exposed read-only: it may only
    change through ``deposit`` and ``withdraw``.
    """

    def __init__(
        self,
        owner: Owner,
        account_id: str,
        status: AccountStatus = AccountStatus.ACTIVE,
    ) -> None:
        self._owner = owner
        self._account_id = account_id
        self._status = status
        self._balance = Decimal("0.00")

    # --- read-only state -------------------------------------------------

    @property
    def account_id(self) -> str:
        return self._account_id

    @property
    def owner(self) -> Owner:
        return self._owner

    @property
    def status(self) -> AccountStatus:
        return self._status

    @property
    def balance(self) -> Decimal:
        return self._balance

    @property
    def account_type(self) -> str:
        """Human-readable account type derived from the concrete class name."""
        return type(self).__name__

    # --- operations every account must provide ---------------------------

    @abstractmethod
    def deposit(self, amount: object) -> Decimal:
        """Add ``amount`` to the balance and return the new balance."""

    @abstractmethod
    def withdraw(self, amount: object) -> Decimal:
        """Subtract ``amount`` from the balance and return the new balance."""

    @abstractmethod
    def get_account_info(self) -> dict[str, Any]:
        """Return a serializable snapshot of the account state."""

    def __repr__(self) -> str:
        return (
            f"{self.account_type}(account_id={self._account_id!r}, "
            f"owner={self._owner.full_name!r}, status={self._status.value!r}, "
            f"balance={self._balance})"
        )


class BankAccount(AbstractAccount):
    """A regular currency account with validation and status enforcement."""

    def __init__(
        self,
        owner: Owner,
        currency: Currency | str,
        account_id: str | None = None,
        status: AccountStatus | str = AccountStatus.ACTIVE,
        initial_balance: object = 0,
    ) -> None:
        if not isinstance(owner, Owner):
            raise InvalidOperationError("owner must be an Owner instance.")

        super().__init__(
            owner=owner,
            account_id=self._resolve_account_id(account_id),
            status=self._resolve_status(status),
        )
        self._currency = self._resolve_currency(currency)

        balance = to_money(initial_balance, field="initial_balance")
        if balance < 0:
            raise InvalidOperationError("initial_balance cannot be negative.")
        self._balance = balance

    # --- constructor helpers ---------------------------------------------

    @staticmethod
    def _resolve_account_id(account_id: str | None) -> str:
        """Use the provided number or generate a UUID4 when none is given."""
        if account_id is None:
            return str(uuid.uuid4())
        if not isinstance(account_id, str) or not account_id.strip():
            raise InvalidOperationError("account_id must be a non-empty string.")
        return account_id

    @staticmethod
    def _resolve_status(status: AccountStatus | str) -> AccountStatus:
        if isinstance(status, AccountStatus):
            return status
        try:
            return AccountStatus(status)
        except ValueError as exc:
            allowed = ", ".join(item.value for item in AccountStatus)
            raise InvalidOperationError(
                f"Unknown account status {status!r}; allowed: {allowed}."
            ) from exc

    @staticmethod
    def _resolve_currency(currency: Currency | str) -> Currency:
        if isinstance(currency, Currency):
            return currency
        try:
            return Currency(str(currency).upper())
        except ValueError as exc:
            allowed = ", ".join(item.value for item in Currency)
            raise InvalidOperationError(
                f"Unsupported currency {currency!r}; allowed: {allowed}."
            ) from exc

    # --- guards shared by the operations ----------------------------------

    def _ensure_operational(self) -> None:
        """Reject any money movement unless the account is active."""
        if self._status is AccountStatus.FROZEN:
            raise AccountFrozenError(self._account_id)
        if self._status is AccountStatus.CLOSED:
            raise AccountClosedError(self._account_id)

    @staticmethod
    def _validate_amount(amount: object) -> Decimal:
        """Normalize ``amount`` to money and require it to be strictly positive."""
        value = to_money(amount)
        if value <= 0:
            raise InvalidOperationError(
                f"amount must be greater than zero, got {value}."
            )
        return value

    # public API

    @property
    def currency(self) -> Currency:
        return self._currency

    def deposit(self, amount: object) -> Decimal:
        self._ensure_operational()
        value = self._validate_amount(amount)
        self._balance += value
        return self._balance

    def withdraw(self, amount: object) -> Decimal:
        self._ensure_operational()
        value = self._validate_amount(amount)
        if value > self._balance:
            raise InsufficientFundsError(requested=value, available=self._balance)
        self._balance -= value
        return self._balance

    def get_account_info(self) -> dict[str, Any]:
        return {
            "account_id": self._account_id,
            "account_type": self.account_type,
            "owner": self._owner.to_dict(),
            "status": self._status.value,
            "currency": self._currency.value,
            "balance": str(self._balance),
        }

    def __str__(self) -> str:
        masked_id = f"****{self._account_id[-4:]}"
        return (
            f"{self.account_type} | {self._owner.full_name} | {masked_id} | "
            f"{self._status.value} | {self._balance} {self._currency.value}"
        )
