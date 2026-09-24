import uuid
from decimal import Decimal

import pytest

from exceptions import (
    AccountClosedError,
    AccountFrozenError,
    InsufficientFundsError,
    InvalidOperationError,
)
from models import AbstractAccount, AccountStatus, BankAccount, Currency


def test_abstract_account_cannot_be_instantiated(owner):
    with pytest.raises(TypeError):
        AbstractAccount(owner=owner, account_id="x")


def test_generates_uuid4_when_account_id_missing(owner):
    account = BankAccount(owner=owner, currency=Currency.RUB)
    parsed = uuid.UUID(account.account_id)
    assert parsed.version == 4


def test_generated_ids_are_unique(owner):
    ids = {BankAccount(owner=owner, currency="RUB").account_id for _ in range(50)}
    assert len(ids) == 50


def test_keeps_provided_account_id_stripped(owner):
    account = BankAccount(owner=owner, currency="RUB", account_id="  ACC-0001 ")
    assert account.account_id == "ACC-0001"


def test_defaults_to_active_status_and_zero_balance(owner):
    account = BankAccount(owner=owner, currency="RUB")
    assert account.status is AccountStatus.ACTIVE
    assert account.balance == Decimal("0.00")


@pytest.mark.parametrize("code", ["RUB", "USD", "EUR", "KZT", "CNY", "usd"])
def test_accepts_supported_currency_codes(owner, code):
    account = BankAccount(owner=owner, currency=code)
    assert account.currency is Currency(code.upper())


def test_accepts_status_as_string(owner):
    account = BankAccount(owner=owner, currency="RUB", status="frozen")
    assert account.status is AccountStatus.FROZEN


def test_initial_balance_is_normalized_to_money(owner):
    account = BankAccount(owner=owner, currency="RUB", initial_balance=10.005)
    assert account.balance == Decimal("10.01")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"currency": "GBP"},
        {"currency": "RUB", "account_id": ""},
        {"currency": "RUB", "account_id": 123},
        {"currency": "RUB", "status": "suspended"},
        {"currency": "RUB", "initial_balance": -1},
        {"currency": "RUB", "initial_balance": "abc"},
        {"currency": "RUB", "initial_balance": True},
    ],
)
def test_rejects_invalid_constructor_input(owner, kwargs):
    with pytest.raises(InvalidOperationError):
        BankAccount(owner=owner, **kwargs)


def test_rejects_non_owner_instance():
    with pytest.raises(InvalidOperationError):
        BankAccount(owner="Ivan Petrov", currency="RUB")


def test_balance_is_read_only(active_account):
    with pytest.raises(AttributeError):
        active_account.balance = Decimal("1000000")


def test_status_is_read_only(active_account):
    with pytest.raises(AttributeError):
        active_account.status = AccountStatus.CLOSED


def test_deposit_increases_balance_and_returns_it(active_account):
    new_balance = active_account.deposit("25.5")
    assert new_balance == Decimal("125.50")
    assert active_account.balance == Decimal("125.50")


@pytest.mark.parametrize("amount", [0, -1, "-0.01", "abc", None, True])
def test_deposit_rejects_invalid_amount(active_account, amount):
    with pytest.raises(InvalidOperationError):
        active_account.deposit(amount)
    assert active_account.balance == Decimal("100.00")


def test_deposit_on_frozen_account_raises(frozen_account):
    with pytest.raises(AccountFrozenError):
        frozen_account.deposit(10)
    assert frozen_account.balance == Decimal("100.00")


def test_deposit_on_closed_account_raises(closed_account):
    with pytest.raises(AccountClosedError):
        closed_account.deposit(10)


def test_withdraw_decreases_balance_and_returns_it(active_account):
    new_balance = active_account.withdraw(40)
    assert new_balance == Decimal("60.00")


def test_withdraw_entire_balance_is_allowed(active_account):
    assert active_account.withdraw(100) == Decimal("0.00")


def test_withdraw_more_than_balance_raises(active_account):
    with pytest.raises(InsufficientFundsError) as info:
        active_account.withdraw("100.01")
    assert info.value.requested == Decimal("100.01")
    assert info.value.available == Decimal("100.00")
    assert active_account.balance == Decimal("100.00")


@pytest.mark.parametrize("amount", [0, -5, "x"])
def test_withdraw_rejects_invalid_amount(active_account, amount):
    with pytest.raises(InvalidOperationError):
        active_account.withdraw(amount)


def test_withdraw_on_frozen_account_raises(frozen_account):
    with pytest.raises(AccountFrozenError):
        frozen_account.withdraw(10)


def test_withdraw_on_closed_account_raises(closed_account):
    with pytest.raises(AccountClosedError):
        closed_account.withdraw(10)


def test_status_is_checked_before_amount(frozen_account):
    # A frozen account rejects the operation even if the amount is invalid.
    with pytest.raises(AccountFrozenError):
        frozen_account.deposit(-1)


def test_str_contains_required_fields(owner):
    account = BankAccount(
        owner=owner,
        currency="USD",
        account_id="ACC-2024-0042",
        status="frozen",
        initial_balance="250.5",
    )
    assert str(account) == ("BankAccount | Smirnova Anna | ****0042 | frozen | 250.50 USD")


def test_repr_is_informative(active_account):
    text = repr(active_account)
    assert text.startswith("BankAccount(")
    assert active_account.account_id in text


def test_get_account_info_returns_serializable_snapshot(owner):
    account = BankAccount(owner=owner, currency="KZT", account_id="A-1")
    assert account.get_account_info() == {
        "account_id": "A-1",
        "account_type": "BankAccount",
        "owner": owner.to_dict(),
        "status": "active",
        "currency": "KZT",
        "balance": "0.00",
    }
