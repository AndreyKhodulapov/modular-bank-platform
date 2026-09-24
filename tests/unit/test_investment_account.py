from decimal import Decimal

import pytest

from exceptions import AccountFrozenError, InsufficientFundsError, InvalidOperationError
from models import AssetType, InvestmentAccount


def test_new_account_has_empty_portfolio(investment_account):
    assert investment_account.holdings == {}
    assert investment_account.invested_total == Decimal("0.00")
    assert investment_account.total_value == Decimal("1000.00")


def test_invest_moves_cash_into_portfolio(investment_account):
    assert investment_account.invest("stocks", 300) == Decimal("700.00")
    assert investment_account.invest(AssetType.STOCKS, 100) == Decimal("600.00")
    assert investment_account.holdings == {AssetType.STOCKS: Decimal("400.00")}
    assert investment_account.total_value == Decimal("1000.00")


def test_invest_checks_asset_type_before_funds(investment_account):
    with pytest.raises(InvalidOperationError, match="asset type"):
        investment_account.invest("crypto", 5000)


def test_invest_more_than_cash_raises(investment_account):
    with pytest.raises(InsufficientFundsError) as info:
        investment_account.invest("etf", "1000.01")
    assert info.value.available == Decimal("1000.00")
    assert investment_account.holdings == {}


def test_invest_on_frozen_account_raises(owner):
    frozen = InvestmentAccount(owner=owner, currency="EUR", status="frozen", initial_balance=100)
    with pytest.raises(AccountFrozenError):
        frozen.invest("bonds", 10)


def test_divest_returns_cash(investment_account):
    investment_account.invest("bonds", 300)
    assert investment_account.divest("bonds", 100) == Decimal("800.00")
    assert investment_account.holdings == {AssetType.BONDS: Decimal("200.00")}


def test_divest_more_than_held_raises(investment_account):
    investment_account.invest("bonds", 300)
    with pytest.raises(InsufficientFundsError):
        investment_account.divest("bonds", 301)
    assert investment_account.balance == Decimal("700.00")


def test_withdraw_uses_free_cash_only(investment_account):
    investment_account.invest("stocks", 700)
    assert investment_account.withdraw(300) == Decimal("0.00")
    assert investment_account.invested_total == Decimal("700.00")


def test_withdraw_never_touches_portfolio(investment_account):
    investment_account.invest("stocks", 700)
    with pytest.raises(InsufficientFundsError, match="divest") as info:
        investment_account.withdraw("300.01")
    assert info.value.available == Decimal("300.00")
    assert investment_account.total_value == Decimal("1000.00")


def test_project_yearly_growth_delegates_to_portfolio(investment_account):
    investment_account.invest("stocks", 500)
    investment_account.invest("bonds", 200)
    rates = {"stocks": "0.10", "bonds": "0.04", "etf": "0.07"}
    assert investment_account.project_yearly_growth(rates) == Decimal("58.00")


def test_get_account_info_extends_base_snapshot(investment_account):
    investment_account.invest("etf", 250)
    info = investment_account.get_account_info()
    assert info["account_type"] == "InvestmentAccount"
    assert info["balance"] == "750.00"
    assert info["portfolio"] == {"etf": "250.00"}
    assert info["invested_total"] == "250.00"
    assert info["total_value"] == "1000.00"


def test_str_extends_base_representation(owner):
    account = InvestmentAccount(owner=owner, currency="EUR", account_id="INV-0003", initial_balance=1000)
    account.invest("etf", 250)
    assert (
        str(account)
        == "InvestmentAccount | Smirnova Anna | ****0003 | active | 750.00 EUR | invested 250.00 | total 1000.00"
    )
