"""Premium account: higher limits, overdraft and a fixed withdrawal fee."""

from decimal import Decimal
from typing import Any

from exceptions import InsufficientFundsError
from models.account import BankAccount
from models.client import Client
from models.enums import AccountStatus, Currency
from utils import to_money


class PremiumAccount(BankAccount):
    """A current account for premium clients.

    Per-operation limits are ten times higher than on a regular account, the
    balance may go negative down to ``-overdraft_limit``, and every successful
    withdrawal is charged a fixed ``withdrawal_fee`` on top of the amount.
    ``MAX_WITHDRAWAL`` caps the requested amount only; the fee is not counted
    against it.
    """

    MAX_DEPOSIT = BankAccount.MAX_DEPOSIT * 10
    MAX_WITHDRAWAL = BankAccount.MAX_WITHDRAWAL * 10

    def __init__(
        self,
        owner: Client,
        currency: Currency | str,
        account_id: str | None = None,
        status: AccountStatus | str = AccountStatus.ACTIVE,
        initial_balance: object = 0,
        *,
        overdraft_limit: object = 0,
        withdrawal_fee: object = 0,
    ) -> None:
        super().__init__(owner, currency, account_id, status, initial_balance)

        self._overdraft_limit = to_money(overdraft_limit, field="overdraft_limit", require="non_negative")
        self._withdrawal_fee = to_money(withdrawal_fee, field="withdrawal_fee", require="non_negative")

    @property
    def overdraft_limit(self) -> Decimal:
        return self._overdraft_limit

    @property
    def withdrawal_fee(self) -> Decimal:
        return self._withdrawal_fee

    @property
    def available_funds(self) -> Decimal:
        """Own money plus the unused part of the overdraft."""
        return self._balance + self._overdraft_limit

    def withdraw(self, amount: object) -> Decimal:
        value = self._prepare_withdrawal(amount)
        total_debit = value + self._withdrawal_fee
        if total_debit > self.available_funds:
            hint = f"The fixed fee {self._withdrawal_fee} is charged on top." if self._withdrawal_fee else None
            requestable = max(self.available_funds - self._withdrawal_fee, Decimal("0.00"))
            raise InsufficientFundsError(requested=value, available=requestable, hint=hint)
        self._balance -= total_debit
        return self._balance

    def get_account_info(self) -> dict[str, Any]:
        info = super().get_account_info()
        info.update(
            {
                "overdraft_limit": str(self._overdraft_limit),
                "withdrawal_fee": str(self._withdrawal_fee),
                "available_funds": str(self.available_funds),
                "max_deposit": str(self.MAX_DEPOSIT),
                "max_withdrawal": str(self.MAX_WITHDRAWAL),
            }
        )
        return info

    def __str__(self) -> str:
        return f"{super().__str__()} | overdraft {self._overdraft_limit} | fee {self._withdrawal_fee}"
