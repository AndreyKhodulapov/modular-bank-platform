from decimal import Decimal

import pytest

from exceptions import AccountClosedError, AccountFrozenError, InsufficientFundsError, InvalidOperationError
from models import SavingsAccount


def test_stores_min_balance_and_rate_as_decimal(savings_account):
    assert savings_account.min_balance == Decimal("100.00")
    assert savings_account.monthly_rate == Decimal("0.015")
    assert savings_account.withdrawable == Decimal("900.00")


def test_defaults_to_no_minimum_and_no_interest(owner):
    account = SavingsAccount(owner=owner, currency="RUB")
    assert account.min_balance == Decimal("0.00")
    assert account.monthly_rate == Decimal("0")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_balance": -1},
        {"monthly_rate": "-0.01"},
        {"monthly_rate": True},
        {"initial_balance": 50, "min_balance": 100},
    ],
)
def test_rejects_invalid_constructor_input(owner, kwargs):
    with pytest.raises(InvalidOperationError):
        SavingsAccount(owner=owner, currency="RUB", **kwargs)


def test_withdraw_down_to_min_balance_is_allowed(savings_account):
    assert savings_account.withdraw(900) == Decimal("100.00")


def test_withdraw_below_min_balance_raises(savings_account):
    with pytest.raises(InsufficientFundsError) as info:
        savings_account.withdraw("900.01")
    assert info.value.requested == Decimal("900.01")
    assert info.value.available == Decimal("900.00")
    assert savings_account.balance == Decimal("1000.00")


def test_apply_monthly_interest_credits_and_returns_interest(savings_account):
    interest = savings_account.apply_monthly_interest()
    assert interest == Decimal("15.00")
    assert savings_account.balance == Decimal("1015.00")


def test_interest_is_rounded_half_up(owner):
    account = SavingsAccount(owner=owner, currency="RUB", initial_balance="100.10", monthly_rate="0.015")
    # 100.10 * 0.015 = 1.5015 -> 1.50
    assert account.apply_monthly_interest() == Decimal("1.50")


def test_zero_rate_yields_no_interest(owner):
    account = SavingsAccount(owner=owner, currency="RUB", initial_balance=500)
    assert account.apply_monthly_interest() == Decimal("0.00")
    assert account.balance == Decimal("500.00")


@pytest.mark.parametrize(
    ("status", "error_type"),
    [("frozen", AccountFrozenError), ("closed", AccountClosedError)],
)
def test_inactive_account_earns_no_interest(owner, status, error_type):
    account = SavingsAccount(owner=owner, currency="RUB", status=status, initial_balance=1000, monthly_rate="0.1")
    with pytest.raises(error_type):
        account.apply_monthly_interest()
    assert account.balance == Decimal("1000.00")


def test_get_account_info_extends_base_snapshot(savings_account):
    info = savings_account.get_account_info()
    assert info["account_type"] == "SavingsAccount"
    assert info["balance"] == "1000.00"
    assert info["min_balance"] == "100.00"
    assert info["monthly_rate"] == "0.015"
    assert info["withdrawable"] == "900.00"


def test_str_extends_base_representation(owner):
    account = SavingsAccount(
        owner=owner,
        currency="RUB",
        account_id="SAV-0001",
        initial_balance=1000,
        min_balance=100,
        monthly_rate="0.015",
    )
    assert str(account) == "SavingsAccount | Smirnova Anna | ****0001 | active | 1000.00 RUB | min 100.00 | 1.50%/month"
