"""Investment account: free cash plus a portfolio of virtual assets."""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from exceptions import InsufficientFundsError
from models.account import BankAccount
from models.client import Client
from models.enums import AccountStatus, AssetType, Currency
from models.portfolio import Portfolio
from utils import to_money


class InvestmentAccount(BankAccount):
    """An account whose money is split between free cash and a portfolio.

    ``balance`` is the free cash only. Money moves into the portfolio with
    ``invest()`` and back with ``divest()``; ``withdraw()`` never touches
    invested money, so a client has to divest first.
    """

    def __init__(
        self,
        owner: Client,
        currency: Currency | str,
        account_id: str | None = None,
        status: AccountStatus | str = AccountStatus.ACTIVE,
        initial_balance: object = 0,
    ) -> None:
        super().__init__(owner, currency, account_id, status, initial_balance)
        self._portfolio = Portfolio()

    @property
    def holdings(self) -> dict[AssetType, Decimal]:
        return self._portfolio.holdings

    @property
    def invested_total(self) -> Decimal:
        return self._portfolio.total

    @property
    def total_value(self) -> Decimal:
        """Free cash plus everything allocated in the portfolio."""
        return self._balance + self._portfolio.total

    def invest(self, asset_type: AssetType | str, amount: object) -> Decimal:
        """Move ``amount`` of free cash into ``asset_type``; return the new cash balance."""
        self.ensure_operational()
        asset = Portfolio.resolve_asset_type(asset_type)
        value = to_money(amount, require="positive")
        if value > self._balance:
            raise InsufficientFundsError(
                requested=value,
                available=self._balance,
                hint="Only free cash can be invested.",
            )
        self._portfolio.add(asset, value)
        self._balance -= value
        return self._balance

    def divest(self, asset_type: AssetType | str, amount: object) -> Decimal:
        """Move ``amount`` from ``asset_type`` back to free cash; return the new cash balance."""
        self.ensure_operational()
        asset = Portfolio.resolve_asset_type(asset_type)
        value = to_money(amount, require="positive")
        self._portfolio.remove(asset, value)
        self._balance += value
        return self._balance

    def project_yearly_growth(self, growth_rates: Mapping[AssetType | str, object]) -> Decimal:
        return self._portfolio.project_yearly_growth(growth_rates)

    def withdraw(self, amount: object) -> Decimal:
        value = self._prepare_withdrawal(amount)
        if value > self._balance:
            hint = f"{self.invested_total} is locked in the portfolio; divest first." if self.invested_total else None
            raise InsufficientFundsError(requested=value, available=self._balance, hint=hint)
        self._balance -= value
        return self._balance

    def get_account_info(self) -> dict[str, Any]:
        info = super().get_account_info()
        info.update(
            {
                "portfolio": self._portfolio.to_dict(),
                "invested_total": str(self.invested_total),
                "total_value": str(self.total_value),
            }
        )
        return info

    def __str__(self) -> str:
        return f"{super().__str__()} | invested {self.invested_total} | total {self.total_value}"
