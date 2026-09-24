"""Bank account models: the abstract base and its first concrete implementation."""

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any

from exceptions import (
    AccountClosedError,
    AccountFrozenError,
    InsufficientFundsError,
    InvalidOperationError,
    LimitExceededError,
)
from models.client import Client
from models.enums import AccountStatus, Currency
from utils import resolve_identifier, to_enum, to_money


class AbstractAccount(ABC):
    """Abstract base for every account type in the platform.

    Holds the state common to all accounts (identifier, owner, protected
    balance, status) and declares the operations each concrete account must
    implement. The balance is deliberately exposed read-only: it may only
    change through ``deposit`` and ``withdraw``.
    """

    def __init__(
        self,
        owner: Client,
        account_id: str,
        status: AccountStatus = AccountStatus.ACTIVE,
    ) -> None:
        self._owner = owner
        self._account_id = account_id
        self._status = status
        self._balance = Decimal("0.00")

    @property
    def account_id(self) -> str:
        return self._account_id

    @property
    def owner(self) -> Client:
        return self._owner

    @property
    def status(self) -> AccountStatus:
        return self._status

    @property
    def balance(self) -> Decimal:
        return self._balance

    @property
    def account_type(self) -> str:
        return type(self).__name__

    @property
    def total_value(self) -> Decimal:
        """Everything the account is worth; subclasses holding more than cash override it."""
        return self._balance

    # status transitions: ACTIVE <-> FROZEN, and either of them -> CLOSED (final)

    def _ensure_not_closed(self) -> None:
        if self._status is AccountStatus.CLOSED:
            raise AccountClosedError(self._account_id)

    def freeze(self) -> None:
        self._ensure_not_closed()
        if self._status is AccountStatus.FROZEN:
            raise InvalidOperationError(f"Account {self._account_id} is already frozen.")
        self._status = AccountStatus.FROZEN

    def unfreeze(self) -> None:
        self._ensure_not_closed()
        if self._status is AccountStatus.ACTIVE:
            raise InvalidOperationError(f"Account {self._account_id} is not frozen.")
        self._status = AccountStatus.ACTIVE

    def close(self) -> Decimal:
        """Close the account for good, pay out its cash and return the paid-out amount.

        Closing is a settlement, so ``min_balance`` does not hold the cash
        back. It is refused while the account is frozen with anything on it
        (a freeze must not be bypassed by closing), owes money or holds
        anything besides cash (``total_value`` above the balance).
        """
        self._ensure_not_closed()
        if self._status is AccountStatus.FROZEN and self.total_value != 0:
            raise AccountFrozenError(self._account_id)
        if self._balance < 0:
            raise InvalidOperationError(f"Account {self._account_id} cannot be closed while it owes {-self._balance}.")
        if self.total_value != self._balance:
            raise InvalidOperationError(
                f"Account {self._account_id} cannot be closed while it holds "
                f"{self.total_value - self._balance} besides cash."
            )
        payout = self._balance
        self._balance = Decimal("0.00")
        self._status = AccountStatus.CLOSED
        return payout

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
    """A regular currency account with validation and status enforcement.

    ``MAX_DEPOSIT`` and ``MAX_WITHDRAWAL`` cap a single operation; subclasses
    override them to offer higher limits.
    """

    MAX_DEPOSIT = Decimal("1000000.00")
    MAX_WITHDRAWAL = Decimal("1000000.00")

    def __init__(
        self,
        owner: Client,
        currency: Currency | str,
        account_id: str | None = None,
        status: AccountStatus | str = AccountStatus.ACTIVE,
        initial_balance: object = 0,
    ) -> None:
        if not isinstance(owner, Client):
            raise InvalidOperationError("owner must be a Client instance.")

        super().__init__(
            owner=owner,
            account_id=resolve_identifier(account_id, field="account_id"),
            status=to_enum(AccountStatus, status, field="account status"),
        )
        self._currency = to_enum(Currency, currency, field="currency")

        self._balance = to_money(initial_balance, field="initial_balance", require="non_negative")

    def _ensure_operational(self) -> None:
        if self._status is AccountStatus.FROZEN:
            raise AccountFrozenError(self._account_id)
        if self._status is AccountStatus.CLOSED:
            raise AccountClosedError(self._account_id)

    @staticmethod
    def _check_limit(value: Decimal, limit: Decimal) -> None:
        if value > limit:
            raise LimitExceededError(requested=value, limit=limit)

    def _prepare_withdrawal(self, amount: object) -> Decimal:
        """Run the checks shared by every withdrawal: status, amount, limit.

        Subclasses call this first and then apply their own rule for how much
        money is actually available (minimum balance, overdraft, portfolio).
        """
        self._ensure_operational()
        value = to_money(amount, require="positive")
        self._check_limit(value, self.MAX_WITHDRAWAL)
        return value

    # public API

    @property
    def currency(self) -> Currency:
        return self._currency

    def deposit(self, amount: object) -> Decimal:
        self._ensure_operational()
        value = to_money(amount, require="positive")
        self._check_limit(value, self.MAX_DEPOSIT)
        self._balance += value
        return self._balance

    def withdraw(self, amount: object) -> Decimal:
        value = self._prepare_withdrawal(amount)
        if value > self._balance:
            raise InsufficientFundsError(requested=value, available=self._balance)
        self._balance -= value
        return self._balance

    def get_account_info(self) -> dict[str, Any]:
        return {
            "account_id": self._account_id,
            "account_type": self.account_type,
            "owner": self._owner.to_summary_dict(),
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
