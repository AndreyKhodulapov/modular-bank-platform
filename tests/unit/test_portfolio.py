from decimal import Decimal

import pytest

from exceptions import InsufficientFundsError, InvalidOperationError
from models import AssetType, Portfolio


@pytest.fixture
def portfolio() -> Portfolio:
    filled = Portfolio()
    filled.add(AssetType.STOCKS, 500)
    filled.add("bonds", 200)
    return filled


def test_empty_portfolio():
    empty = Portfolio()
    assert empty.total == Decimal("0.00")
    assert empty.holdings == {}
    assert empty.to_dict() == {}
    assert empty.project_yearly_growth({"stocks": "0.10"}) == Decimal("0.00")


@pytest.mark.parametrize("asset", [AssetType.ETF, "etf", "ETF"])
def test_add_accepts_enum_or_string_and_accumulates(portfolio, asset):
    assert portfolio.add(asset, 100) == Decimal("100.00")
    assert portfolio.add(asset, "50.5") == Decimal("150.50")
    assert portfolio.total == Decimal("850.50")


@pytest.mark.parametrize(("asset", "amount"), [("crypto", 10), ("stocks", 0), ("stocks", -5)])
def test_add_rejects_unknown_asset_or_non_positive_amount(portfolio, asset, amount):
    with pytest.raises(InvalidOperationError):
        portfolio.add(asset, amount)
    assert portfolio.total == Decimal("700.00")


def test_holdings_is_a_copy(portfolio):
    portfolio.holdings[AssetType.STOCKS] = Decimal("0")
    assert portfolio.get("stocks") == Decimal("500.00")


def test_remove_partial_and_full_position(portfolio):
    assert portfolio.remove("stocks", 100) == Decimal("400.00")
    assert portfolio.remove("bonds", 200) == Decimal("0.00")
    assert portfolio.to_dict() == {"stocks": "400.00"}


def test_remove_more_than_held_raises(portfolio):
    with pytest.raises(InsufficientFundsError) as info:
        portfolio.remove("etf", 1)
    assert info.value.available == Decimal("0.00")
    assert portfolio.total == Decimal("700.00")


def test_project_yearly_growth_sums_weighted_rates(portfolio):
    # 500 * 0.10 + 200 * 0.04 = 58.00; the etf rate is unused but allowed
    assert portfolio.project_yearly_growth({"stocks": "0.10", "bonds": "0.04", "etf": "0.07"}) == Decimal("58.00")


def test_project_yearly_growth_accepts_enum_keys_and_negative_rates(portfolio):
    growth = portfolio.project_yearly_growth({AssetType.STOCKS: "-0.10", AssetType.BONDS: 0})
    assert growth == Decimal("-50.00")


@pytest.mark.parametrize(
    "rates",
    [
        {"stocks": "0.10"},  # bonds missing
        {"stocks": "0.10", "bonds": "0.04", "crypto": "1"},  # unknown asset
        {"stocks": "0.10", "bonds": "-1.5"},  # loss of more than 100%
        {"stocks": "1.5", "bonds": "0.04"},  # gain of more than 100%
        {"stocks": "0.10", AssetType.STOCKS: "0.50", "bonds": "0.04"},  # same asset twice
        {"stocks": "0.10", "bonds": "four"},
        [("stocks", "0.10")],  # not a mapping
    ],
)
def test_project_yearly_growth_rejects_bad_rates(portfolio, rates):
    with pytest.raises(InvalidOperationError):
        portfolio.project_yearly_growth(rates)
