from decimal import Decimal

import pytest

from exceptions import InsufficientFundsError, InvalidOperationError, LimitExceededError
from models import BankAccount, PremiumAccount


def test_stores_overdraft_and_fee_as_decimal(premium_account):
    assert premium_account.overdraft_limit == Decimal("50.00")
    assert premium_account.withdrawal_fee == Decimal("5.00")
    assert premium_account.available_funds == Decimal("150.00")


@pytest.mark.parametrize("kwargs", [{"overdraft_limit": -1}, {"withdrawal_fee": "-0.01"}, {"withdrawal_fee": "x"}])
def test_rejects_invalid_constructor_input(owner, kwargs):
    with pytest.raises(InvalidOperationError):
        PremiumAccount(owner=owner, currency="USD", **kwargs)


def test_withdraw_charges_fixed_fee(premium_account):
    assert premium_account.withdraw(10) == Decimal("85.00")


def test_withdraw_may_use_whole_overdraft(premium_account):
    # 100 balance + 50 overdraft = 150 available, 145 + 5 fee uses all of it
    assert premium_account.withdraw(145) == Decimal("-50.00")


def test_withdraw_beyond_overdraft_raises_and_charges_nothing(premium_account):
    with pytest.raises(InsufficientFundsError) as info:
        premium_account.withdraw("145.01")
    assert info.value.requested == Decimal("145.01")
    assert info.value.available == Decimal("145.00")
    assert "fee 5.00" in str(info.value)
    assert premium_account.balance == Decimal("100.00")


def test_deposit_has_no_fee_and_repays_overdraft(premium_account):
    premium_account.withdraw(145)
    assert premium_account.deposit(60) == Decimal("10.00")


def test_deposit_between_regular_and_premium_limit_is_allowed(premium_account):
    amount = BankAccount.MAX_DEPOSIT + 1
    assert premium_account.deposit(amount) == Decimal("1000101.00")


def test_deposit_above_premium_limit_raises(premium_account):
    with pytest.raises(LimitExceededError) as info:
        premium_account.deposit(PremiumAccount.MAX_DEPOSIT + 1)
    assert info.value.limit == Decimal("10000000.00")


def test_get_account_info_extends_base_snapshot(premium_account):
    info = premium_account.get_account_info()
    assert info["account_type"] == "PremiumAccount"
    assert info["overdraft_limit"] == "50.00"
    assert info["withdrawal_fee"] == "5.00"
    assert info["available_funds"] == "150.00"
    assert info["max_withdrawal"] == "10000000.00"


def test_str_extends_base_representation(owner):
    account = PremiumAccount(
        owner=owner,
        currency="USD",
        account_id="PRM-0007",
        initial_balance=100,
        overdraft_limit=50,
        withdrawal_fee=5,
    )
    assert (
        str(account) == "PremiumAccount | Smirnova Anna | ****0007 | active | 100.00 USD | overdraft 50.00 | fee 5.00"
    )
