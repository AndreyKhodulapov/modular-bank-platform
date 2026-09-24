"""Savings account: a protected minimum balance and monthly interest."""

from decimal import Decimal
from typing import Any

from exceptions import InsufficientFundsError, InvalidOperationError
from models.account import BankAccount
from models.client import Client
from models.enums import AccountStatus, Currency
from utils import to_money, to_rate


class SavingsAccount(BankAccount):
    """A deposit account that keeps ``min_balance`` locked and earns interest.

    ``monthly_rate`` is a fraction (``0.01`` means 1% per month). Interest is
    credited explicitly through ``apply_monthly_interest()``; the platform has
    no clock, so the caller decides when a month has passed.
    """

    def __init__(
        self,
        owner: Client,
        currency: Currency | str,
        account_id: str | None = None,
        status: AccountStatus | str = AccountStatus.ACTIVE,
        initial_balance: object = 0,
        *,
        min_balance: object = 0,
        monthly_rate: object = 0,
    ) -> None:
        super().__init__(owner, currency, account_id, status, initial_balance)

        self._min_balance = to_money(min_balance, field="min_balance", require="non_negative")
        self._monthly_rate = to_rate(monthly_rate, field="monthly_rate")

        if self._balance < self._min_balance:
            raise InvalidOperationError(
                f"initial_balance {self._balance} is below the minimum balance {self._min_balance}."
            )

    @property
    def min_balance(self) -> Decimal:
        return self._min_balance

    @property
    def monthly_rate(self) -> Decimal:
        return self._monthly_rate

    @property
    def withdrawable(self) -> Decimal:
        """Amount that can leave the account without breaking ``min_balance``."""
        return self._balance - self._min_balance

    def apply_monthly_interest(self) -> Decimal:
        """Credit one month of interest and return the credited amount."""
        self._ensure_operational()
        interest = to_money(self._balance * self._monthly_rate, field="interest")
        self._balance += interest
        return interest

    def withdraw(self, amount: object) -> Decimal:
        value = self._prepare_withdrawal(amount)
        if value > self.withdrawable:
            raise InsufficientFundsError(
                requested=value,
                available=self.withdrawable,
                hint=f"Minimum balance {self._min_balance} must stay on the account.",
            )
        self._balance -= value
        return self._balance

    def get_account_info(self) -> dict[str, Any]:
        info = super().get_account_info()
        info.update(
            {
                "min_balance": str(self._min_balance),
                "monthly_rate": str(self._monthly_rate),
                "withdrawable": str(self.withdrawable),
            }
        )
        return info

    def __str__(self) -> str:
        return f"{super().__str__()} | min {self._min_balance} | {self._monthly_rate:.2%}/month"
