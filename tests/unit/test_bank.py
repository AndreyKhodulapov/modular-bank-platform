from datetime import datetime
from decimal import Decimal

import pytest

from exceptions import (
    AccountClosedError,
    AccountFrozenError,
    AccountNotFoundError,
    AuthenticationError,
    ClientBlockedError,
    ClientNotFoundError,
    InsufficientFundsError,
    InvalidOperationError,
    OperationTimeRestrictedError,
)
from models import AccountStatus, BankAccount, InvestmentAccount, SavingsAccount
from services import Bank, CurrencyConverter, SuspicionReason
from tests.helpers import reasons

NIGHT = datetime(2026, 9, 25, 2, 30)

# (prepare, operation) pairs for the operations refused at night and for a blocked client
RESTRICTED_OPERATIONS = [
    pytest.param(
        lambda bank, client, account: None,
        lambda bank, client, account: bank.open_account(client.client_id, currency="RUB"),
        id="open_account",
    ),
    pytest.param(
        lambda bank, client, account: None,
        lambda bank, client, account: bank.close_account(account.account_id),
        id="close_account",
    ),
    pytest.param(
        lambda bank, client, account: bank.freeze_account(account.account_id),
        lambda bank, client, account: bank.unfreeze_account(account.account_id),
        id="unfreeze_account",
    ),
    pytest.param(
        lambda bank, client, account: None,
        lambda bank, client, account: bank.deposit(account.account_id, 10),
        id="deposit",
    ),
    pytest.param(
        lambda bank, client, account: None,
        lambda bank, client, account: bank.withdraw(account.account_id, 10),
        id="withdraw",
    ),
]


def test_add_client_registers_and_returns_it(bank, owner, password):
    assert bank.add_client(owner, password) is owner
    assert bank.get_client(owner.client_id) is owner


def test_add_client_rejects_duplicate_id(bank, client, password):
    with pytest.raises(InvalidOperationError, match="already registered"):
        bank.add_client(client, password)


@pytest.mark.parametrize("candidate", ["not a client", None])
def test_add_client_rejects_non_client(bank, password, candidate):
    with pytest.raises(InvalidOperationError):
        bank.add_client(candidate, password)


def test_client_with_weak_password_is_not_registered(bank, owner):
    with pytest.raises(InvalidOperationError):
        bank.add_client(owner, "123")
    with pytest.raises(ClientNotFoundError):
        bank.get_client(owner.client_id)


def test_unknown_ids_raise_not_found(bank):
    with pytest.raises(ClientNotFoundError):
        bank.get_client("nope")
    with pytest.raises(AccountNotFoundError):
        bank.get_account("nope")


def test_authenticate_client_returns_client(bank, client, password):
    assert bank.authenticate_client(client.client_id, password) is client


def test_login_for_unknown_client_is_flagged(bank, password):
    with pytest.raises(ClientNotFoundError):
        bank.authenticate_client("ghost", password)
    [activity] = bank.suspicious_activities
    assert (activity.reason, activity.client_id) == (SuspicionReason.UNKNOWN_CLIENT_LOGIN, "ghost")


def test_login_is_allowed_at_night(bank, client, clock, password):
    clock.moment = NIGHT
    assert bank.authenticate_client(client.client_id, password) is client


def test_unblock_client_restores_access(bank, client, password):
    for _ in range(3):
        with pytest.raises((AuthenticationError, ClientBlockedError)):
            bank.authenticate_client(client.client_id, "wrong-password")
    bank.unblock_client(client.client_id)
    assert not client.is_blocked
    assert bank.authenticate_client(client.client_id, password) is client


@pytest.mark.parametrize(
    ("account_type", "params", "expected_class"),
    [
        ("basic", {}, BankAccount),
        ("SAVINGS", {"initial_balance": 10, "min_balance": 10}, SavingsAccount),
        ("investment", {}, InvestmentAccount),
    ],
)
def test_open_account_creates_registered_type(bank, client, account_type, params, expected_class):
    account = bank.open_account(client.client_id, account_type, currency="EUR", **params)
    assert type(account) is expected_class
    assert account.owner is client
    assert bank.get_account(account.account_id) is account
    assert client.account_ids == [account.account_id]


def test_open_account_defaults_to_basic(bank, client):
    assert type(bank.open_account(client.client_id, currency="RUB")) is BankAccount


@pytest.mark.parametrize(
    ("account_type", "params"),
    [
        ("crypto", {"currency": "RUB"}),
        ("basic", {"currency": "RUB", "min_balance": 10}),  # unknown argument for this type
        ("basic", {}),  # currency is missing
        ("basic", {"currency": "RUB", "owner": "someone else"}),
        ("basic", {"currency": "RUB", "account_id": "A-1"}),  # the bank issues numbers
        ("basic", {"currency": "RUB", "status": "closed"}),  # a new account is always active
        (None, {"currency": "RUB"}),
    ],
)
def test_open_account_rejects_invalid_request(bank, client, account_type, params):
    with pytest.raises(InvalidOperationError):
        bank.open_account(client.client_id, account_type, **params)
    assert client.account_ids == []


def test_open_account_for_unknown_client(bank):
    with pytest.raises(ClientNotFoundError):
        bank.open_account("ghost", currency="RUB")


def test_large_initial_balance_is_flagged(bank, client):
    bank.open_account(client.client_id, currency="USD", initial_balance="5555.56")  # 500_000.40 RUB
    assert reasons(bank) == [SuspicionReason.LARGE_OPERATION]


def test_freeze_is_allowed_at_night(bank, client, clock):
    account = bank.open_account(client.client_id, currency="RUB")
    clock.moment = NIGHT
    assert bank.freeze_account(account.account_id).status is AccountStatus.FROZEN
    assert bank.suspicious_activities == []


def test_close_account_returns_the_payout_and_keeps_the_history(bank, client):
    account = bank.open_account(client.client_id, currency="RUB", initial_balance=100)
    assert bank.close_account(account.account_id) == Decimal("100.00")
    assert client.account_ids == [account.account_id]


def test_large_payout_on_close_is_flagged(bank, client):
    account = bank.open_account(client.client_id, currency="RUB", initial_balance=400_000)
    bank.deposit(account.account_id, 100_000)
    bank.close_account(account.account_id)
    assert reasons(bank) == [SuspicionReason.LARGE_OPERATION]
    assert "close_account of 500000.00" in bank.suspicious_activities[0].details


@pytest.mark.parametrize(
    ("prepare", "operation"),
    [
        *RESTRICTED_OPERATIONS,
        pytest.param(
            lambda bank, client, account: client.block(),
            lambda bank, client, account: bank.unblock_client(client.client_id),
            id="unblock_client",
        ),
    ],
)
def test_restricted_operations_are_forbidden_at_night(bank, client, clock, prepare, operation):
    account = bank.open_account(client.client_id, currency="RUB", initial_balance=100)
    prepare(bank, client, account)
    status, blocked = account.status, client.is_blocked
    clock.moment = NIGHT
    with pytest.raises(OperationTimeRestrictedError):
        operation(bank, client, account)
    assert reasons(bank) == [SuspicionReason.NIGHT_OPERATION]
    assert (account.status, account.balance, client.is_blocked) == (status, Decimal("100.00"), blocked)
    assert client.account_ids == [account.account_id]


@pytest.mark.parametrize(("prepare", "operation"), RESTRICTED_OPERATIONS)
def test_restricted_operations_are_forbidden_for_blocked_client(bank, client, prepare, operation):
    account = bank.open_account(client.client_id, currency="RUB", initial_balance=100)
    prepare(bank, client, account)
    status = account.status
    client.block()
    with pytest.raises(ClientBlockedError):
        operation(bank, client, account)
    assert reasons(bank) == [SuspicionReason.BLOCKED_CLIENT_ACTIVITY]
    assert (account.status, account.balance) == (status, Decimal("100.00"))
    assert client.account_ids == [account.account_id]


def test_blocked_client_account_can_still_be_frozen(bank, client):
    account = bank.open_account(client.client_id, currency="RUB")
    client.block()
    assert bank.freeze_account(account.account_id).status is AccountStatus.FROZEN
    assert bank.suspicious_activities == []


def test_deposit_and_withdraw(bank, client):
    account = bank.open_account(client.client_id, currency="RUB", initial_balance=100)
    assert bank.deposit(account.account_id, "50.5") == Decimal("150.50")
    assert bank.withdraw(account.account_id, 30) == Decimal("120.50")
    assert bank.suspicious_activities == []


@pytest.mark.parametrize(
    ("prepare", "operation", "error_type"),
    [
        ("freeze_account", "deposit", AccountFrozenError),
        ("freeze_account", "withdraw", AccountFrozenError),
        ("freeze_account", "close_account", AccountFrozenError),
        ("close_account", "deposit", AccountClosedError),
        ("close_account", "withdraw", AccountClosedError),
        ("close_account", "close_account", AccountClosedError),
        ("close_account", "freeze_account", AccountClosedError),
        ("close_account", "unfreeze_account", AccountClosedError),
    ],
)
def test_operation_on_inactive_account_is_flagged(bank, client, prepare, operation, error_type):
    account = bank.open_account(client.client_id, currency="RUB", initial_balance=10)
    getattr(bank, prepare)(account.account_id)
    arguments = (account.account_id, 10) if operation in ("deposit", "withdraw") else (account.account_id,)
    with pytest.raises(error_type):
        getattr(bank, operation)(*arguments)
    [activity] = bank.suspicious_activities
    assert activity.reason is SuspicionReason.INACTIVE_ACCOUNT_OPERATION
    assert activity.account_id == account.account_id


def test_large_amount_is_converted_before_the_check(bank, client):
    account = bank.open_account(client.client_id, currency="EUR")
    bank.deposit(account.account_id, 5_000)  # 500_000 RUB
    assert reasons(bank) == [SuspicionReason.LARGE_OPERATION]


def test_large_amount_includes_the_withdrawal_fee(bank, client):
    account = bank.open_account(client.client_id, "premium", currency="RUB", initial_balance=600_000, withdrawal_fee=10)
    bank.withdraw(account.account_id, 499_995)  # 500_005 leaves the account
    assert "withdraw of 500005.00" in bank.suspicious_activities[-1].details


def test_rejected_large_operation_is_not_flagged(bank, client):
    account = bank.open_account(client.client_id, currency="RUB")
    with pytest.raises(InsufficientFundsError):
        bank.withdraw(account.account_id, 600_000)
    assert bank.suspicious_activities == []


def test_invalid_amount_is_rejected_before_any_check(bank, client, clock):
    account = bank.open_account(client.client_id, currency="RUB")
    clock.moment = NIGHT
    with pytest.raises(InvalidOperationError):
        bank.deposit(account.account_id, -5)
    assert bank.suspicious_activities == []


def test_invalid_account_parameters_are_rejected_before_any_check(bank, client, clock):
    clock.moment = NIGHT
    with pytest.raises(InvalidOperationError):
        bank.open_account(client.client_id, currency="RUB", min_balance=10)
    assert bank.suspicious_activities == []


def test_non_string_password_for_unknown_id_is_not_a_login_attempt(bank):
    with pytest.raises(InvalidOperationError):
        bank.authenticate_client("ghost", None)
    assert bank.suspicious_activities == []


def test_unblocking_an_active_client_is_not_a_night_operation(bank, client, clock):
    clock.moment = NIGHT
    with pytest.raises(InvalidOperationError, match="not blocked"):
        bank.unblock_client(client.client_id)
    assert bank.suspicious_activities == []


@pytest.fixture
def populated(bank, client, make_client, password):
    """Two clients with four accounts in different currencies, types and states."""
    other = bank.add_client(make_client("Olga"), password)
    rub = bank.open_account(client.client_id, currency="RUB", initial_balance=1_000)
    usd = bank.open_account(client.client_id, "savings", currency="USD", initial_balance=100)
    eur = bank.open_account(other.client_id, "investment", currency="EUR", initial_balance=50)
    eur.invest("bonds", 30)
    empty = bank.open_account(other.client_id, currency="RUB")
    bank.close_account(empty.account_id)
    return {"client": client, "other": other, "rub": rub, "usd": usd, "eur": eur, "empty": empty}


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({}, ["rub", "usd", "eur", "empty"]),
        ({"currency": "rub"}, ["rub", "empty"]),
        ({"status": "closed"}, ["empty"]),
        ({"status": AccountStatus.ACTIVE, "currency": "RUB"}, ["rub"]),
        ({"account_type": "basic"}, ["rub", "empty"]),  # exact type: savings is not "basic"
        ({"account_type": "savings"}, ["usd"]),
        ({"min_balance": 100}, ["rub", "usd"]),
        ({"max_balance": 20}, ["eur", "empty"]),  # 20 of free cash, 30 invested
        ({"min_balance": 100, "max_balance": 100}, ["usd"]),
    ],
)
def test_search_accounts(bank, populated, filters, expected):
    found = bank.search_accounts(**filters)
    assert found == [populated[name] for name in expected]


def test_search_accounts_by_client(bank, populated):
    assert bank.search_accounts(client_id=populated["other"].client_id) == [populated["eur"], populated["empty"]]
    assert bank.search_accounts(client_id="ghost") == []


@pytest.mark.parametrize(
    "filters",
    [{"status": "lost"}, {"currency": "GBP"}, {"account_type": "crypto"}, {"min_balance": "many"}],
)
def test_search_accounts_rejects_invalid_filter(bank, filters):
    with pytest.raises(InvalidOperationError):
        bank.search_accounts(**filters)


def test_total_balance_is_in_roubles_and_includes_portfolio(bank, populated):
    # 1_000 RUB + 100 USD * 90 + (20 + 30) EUR * 100 + 0 RUB
    assert bank.get_total_balance() == Decimal("15000.00")


def test_total_balance_counts_overdraft_as_negative(bank, client):
    premium = bank.open_account(client.client_id, "premium", currency="RUB", overdraft_limit=100)
    bank.withdraw(premium.account_id, 60)
    assert bank.get_total_balance() == Decimal("-60.00")


def test_total_balance_of_empty_bank_is_zero(bank):
    assert bank.get_total_balance() == Decimal("0.00")


def test_clients_ranking(bank, populated, make_client, password):
    newcomer = bank.add_client(make_client("Anna", "Belova"), password)
    ranking = bank.get_clients_ranking()
    assert ranking == [
        (populated["client"], Decimal("10000.00")),
        (populated["other"], Decimal("5000.00")),
        (newcomer, Decimal("0.00")),
    ]


def test_clients_ranking_breaks_ties_by_full_name(bank, make_client, password):
    zoya = bank.add_client(make_client("Zoya"), password)
    anna = bank.add_client(make_client("Anna"), password)
    assert [client for client, _ in bank.get_clients_ranking()] == [anna, zoya]


def test_bank_uses_injected_converter(security, owner, password):
    rates = {"USD": 1, "RUB": "0.01", "EUR": "1.1", "KZT": "0.002", "CNY": "0.14"}
    bank = Bank(security=security, converter=CurrencyConverter(rates, base="USD"))
    bank.add_client(owner, password)
    bank.open_account(owner.client_id, currency="RUB", initial_balance=1_000)
    assert bank.base_currency.value == "USD"
    assert bank.get_total_balance() == Decimal("10.00")


def test_account_type_registry_is_open_for_extension(bank, client, monkeypatch):
    class StudentAccount(BankAccount):
        MAX_WITHDRAWAL = Decimal("100.00")

    monkeypatch.setitem(Bank.ACCOUNT_TYPES, "student", StudentAccount)
    account = bank.open_account(client.client_id, "student", currency="RUB")
    assert type(account) is StudentAccount
    assert bank.search_accounts(account_type="student") == [account]
