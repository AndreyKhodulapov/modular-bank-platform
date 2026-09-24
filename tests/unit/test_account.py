import uuid
from decimal import Decimal

import pytest

from exceptions import (
    AccountClosedError,
    AccountFrozenError,
    InsufficientFundsError,
    InvalidOperationError,
    LimitExceededError,
)
from models import AbstractAccount, AccountStatus, BankAccount, Currency


def test_abstract_account_cannot_be_instantiated(owner):
    with pytest.raises(TypeError):
        AbstractAccount(owner=owner, account_id="x")


def test_generates_uuid4_when_account_id_missing(owner):
    account = BankAccount(owner=owner, currency=Currency.RUB)
    parsed = uuid.UUID(account.account_id)
    assert parsed.version == 4


def test_keeps_provided_account_id_stripped(owner):
    account = BankAccount(owner=owner, currency="RUB", account_id="  ACC-0001 ")
    assert account.account_id == "ACC-0001"


def test_defaults_to_active_status_and_zero_balance(owner):
    account = BankAccount(owner=owner, currency="RUB")
    assert account.status is AccountStatus.ACTIVE
    assert account.balance == Decimal("0.00")


@pytest.mark.parametrize("currency", [Currency.KZT, "kzt"])
def test_accepts_currency_as_enum_or_string(owner, currency):
    account = BankAccount(owner=owner, currency=currency)
    assert account.currency is Currency.KZT


@pytest.mark.parametrize("status", ["frozen", "FROZEN"])
def test_accepts_status_as_string_in_any_case(owner, status):
    account = BankAccount(owner=owner, currency="RUB", status=status)
    assert account.status is AccountStatus.FROZEN


@pytest.mark.parametrize(
    "kwargs",
    [
        {"currency": "GBP"},
        {"currency": "RUB", "account_id": ""},
        {"currency": "RUB", "account_id": 123},
        {"currency": "RUB", "status": "suspended"},
        {"currency": "RUB", "initial_balance": -1},
    ],
)
def test_rejects_invalid_constructor_input(owner, kwargs):
    with pytest.raises(InvalidOperationError):
        BankAccount(owner=owner, **kwargs)


def test_rejects_non_owner_instance():
    with pytest.raises(InvalidOperationError):
        BankAccount(owner="Ivan Petrov", currency="RUB")


@pytest.mark.parametrize("attribute", ["balance", "status"])
def test_state_is_read_only(active_account, attribute):
    with pytest.raises(AttributeError):
        setattr(active_account, attribute, None)


def test_deposit_increases_balance_and_returns_it(active_account):
    new_balance = active_account.deposit("25.5")
    assert new_balance == Decimal("125.50")
    assert active_account.balance == Decimal("125.50")


def test_withdraw_decreases_balance_and_returns_it(active_account):
    new_balance = active_account.withdraw(40)
    assert new_balance == Decimal("60.00")
    assert active_account.balance == Decimal("60.00")


def test_withdraw_entire_balance_is_allowed(active_account):
    assert active_account.withdraw(100) == Decimal("0.00")


def test_withdraw_more_than_balance_raises(active_account):
    with pytest.raises(InsufficientFundsError) as info:
        active_account.withdraw("100.01")
    assert info.value.requested == Decimal("100.01")
    assert info.value.available == Decimal("100.00")
    assert active_account.balance == Decimal("100.00")


@pytest.mark.parametrize("operation", ["deposit", "withdraw"])
@pytest.mark.parametrize("amount", [0, "-0.01"])
def test_rejects_non_positive_amount(active_account, operation, amount):
    with pytest.raises(InvalidOperationError):
        getattr(active_account, operation)(amount)
    assert active_account.balance == Decimal("100.00")


@pytest.mark.parametrize("operation", ["deposit", "withdraw"])
@pytest.mark.parametrize(
    ("fixture_name", "error_type"),
    [("frozen_account", AccountFrozenError), ("closed_account", AccountClosedError)],
)
def test_inactive_account_rejects_operations(request, fixture_name, error_type, operation):
    account = request.getfixturevalue(fixture_name)
    balance_before = account.balance
    with pytest.raises(error_type) as info:
        getattr(account, operation)(10)
    assert info.value.account_id == account.account_id
    assert account.balance == balance_before


def test_status_is_checked_before_amount(frozen_account):
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
    assert str(account) == "BankAccount | Smirnova Anna | ****0042 | frozen | 250.50 USD"


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


def test_operation_at_limit_is_allowed(active_account):
    assert active_account.deposit(BankAccount.MAX_DEPOSIT) == Decimal("1000100.00")


@pytest.mark.parametrize("operation", ["deposit", "withdraw"])
def test_operation_above_limit_raises_before_funds_check(active_account, operation):
    over_limit = BankAccount.MAX_WITHDRAWAL + Decimal("0.01")
    with pytest.raises(LimitExceededError) as info:
        getattr(active_account, operation)(over_limit)
    assert info.value.requested == over_limit
    assert info.value.limit == Decimal("1000000.00")
    assert active_account.balance == Decimal("100.00")
