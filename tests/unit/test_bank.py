from datetime import date, datetime
from decimal import Decimal

import pytest

from exceptions import (
    AccountClosedError,
    AccountFrozenError,
    AccountNotFoundError,
    AuthenticationError,
    ClientBlockedError,
    ClientNotFoundError,
    InvalidOperationError,
    OperationTimeRestrictedError,
)
from models import AccountStatus, BankAccount, Client, InvestmentAccount, SavingsAccount
from services import Bank, CurrencyConverter, SuspicionReason
from tests.conftest import PASSWORD

NIGHT = datetime(2026, 9, 25, 2, 30)


def make_client(first_name: str, last_name: str = "Ivanova") -> Client:
    return Client(
        first_name=first_name,
        last_name=last_name,
        birth_date=date(1990, 1, 1),
        email=f"{first_name.lower()}@example.com",
        phone="+79990002233",
    )


def reasons(bank):
    return [activity.reason for activity in bank.suspicious_activities]


# clients


def test_add_client_registers_and_returns_it(bank, owner):
    assert bank.add_client(owner, PASSWORD) is owner
    assert bank.get_client(owner.client_id) is owner


def test_add_client_rejects_duplicate_id(bank, client):
    with pytest.raises(InvalidOperationError, match="already registered"):
        bank.add_client(client, PASSWORD)


@pytest.mark.parametrize(("candidate", "password"), [("not a client", PASSWORD), (None, PASSWORD)])
def test_add_client_rejects_non_client(bank, candidate, password):
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


def test_authenticate_client_returns_client(bank, client):
    assert bank.authenticate_client(client.client_id, PASSWORD) is client


def test_three_failed_logins_block_the_client(bank, client):
    for _ in range(2):
        with pytest.raises(AuthenticationError):
            bank.authenticate_client(client.client_id, "wrong-password")
    with pytest.raises(ClientBlockedError):
        bank.authenticate_client(client.client_id, "wrong-password")
    with pytest.raises(ClientBlockedError):
        bank.authenticate_client(client.client_id, PASSWORD)
    assert client.is_blocked


def test_login_for_unknown_client_is_flagged(bank):
    with pytest.raises(ClientNotFoundError):
        bank.authenticate_client("ghost", PASSWORD)
    [activity] = bank.suspicious_activities
    assert (activity.reason, activity.client_id) == (SuspicionReason.UNKNOWN_CLIENT_LOGIN, "ghost")


def test_login_is_allowed_at_night(bank, client, clock):
    clock.moment = NIGHT
    assert bank.authenticate_client(client.client_id, PASSWORD) is client


def test_unblock_client_restores_access_and_resets_counter(bank, client, security):
    for _ in range(3):
        with pytest.raises((AuthenticationError, ClientBlockedError)):
            bank.authenticate_client(client.client_id, "wrong-password")
    bank.unblock_client(client.client_id)
    assert not client.is_blocked
    assert security.failed_attempts(client.client_id) == 0
    assert bank.authenticate_client(client.client_id, PASSWORD) is client


def test_unblock_client_is_forbidden_at_night(bank, client, clock):
    client.block()
    clock.moment = NIGHT
    with pytest.raises(OperationTimeRestrictedError):
        bank.unblock_client(client.client_id)
    assert client.is_blocked


# opening accounts


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
        ("basic", {"currency": "GBP"}),
    ],
)
def test_open_account_rejects_invalid_request(bank, client, account_type, params):
    with pytest.raises(InvalidOperationError):
        bank.open_account(client.client_id, account_type, **params)
    assert client.account_ids == []


def test_open_account_rejects_duplicate_account_id(bank, client):
    bank.open_account(client.client_id, currency="RUB", account_id="A-1")
    with pytest.raises(InvalidOperationError, match="already exists"):
        bank.open_account(client.client_id, currency="RUB", account_id="A-1")
    assert client.account_ids == ["A-1"]


def test_open_account_for_unknown_client(bank):
    with pytest.raises(ClientNotFoundError):
        bank.open_account("ghost", currency="RUB")


def test_open_account_for_blocked_client_is_rejected_and_flagged(bank, client):
    client.block()
    with pytest.raises(ClientBlockedError):
        bank.open_account(client.client_id, currency="RUB")
    assert reasons(bank) == [SuspicionReason.BLOCKED_CLIENT_ACTIVITY]


def test_open_account_is_forbidden_at_night(bank, client, clock):
    clock.moment = NIGHT
    with pytest.raises(OperationTimeRestrictedError):
        bank.open_account(client.client_id, currency="RUB")
    assert client.account_ids == []
    assert reasons(bank) == [SuspicionReason.NIGHT_OPERATION]


def test_large_initial_balance_is_flagged(bank, client):
    bank.open_account(client.client_id, currency="USD", initial_balance="5555.56")  # 500_000.40 RUB
    assert reasons(bank) == [SuspicionReason.LARGE_OPERATION]


# status changes


def test_freeze_and_unfreeze_account(bank, client):
    account = bank.open_account(client.client_id, currency="RUB")
    assert bank.freeze_account(account.account_id).status is AccountStatus.FROZEN
    assert bank.unfreeze_account(account.account_id).status is AccountStatus.ACTIVE


def test_freeze_is_allowed_at_night_but_unfreeze_is_not(bank, client, clock):
    account = bank.open_account(client.client_id, currency="RUB")
    clock.moment = NIGHT
    bank.freeze_account(account.account_id)
    with pytest.raises(OperationTimeRestrictedError):
        bank.unfreeze_account(account.account_id)
    assert account.status is AccountStatus.FROZEN


def test_blocked_client_cannot_unfreeze_but_account_can_be_frozen(bank, client):
    account = bank.open_account(client.client_id, currency="RUB")
    client.block()
    bank.freeze_account(account.account_id)
    with pytest.raises(ClientBlockedError):
        bank.unfreeze_account(account.account_id)


def test_close_empty_account(bank, client):
    account = bank.open_account(client.client_id, currency="RUB")
    assert bank.close_account(account.account_id).status is AccountStatus.CLOSED
    assert client.account_ids == [account.account_id]  # closed accounts stay in the history


def test_close_account_with_money_is_rejected(bank, client):
    account = bank.open_account(client.client_id, currency="RUB", initial_balance=1)
    with pytest.raises(InvalidOperationError):
        bank.close_account(account.account_id)


def test_close_account_is_forbidden_at_night(bank, client, clock):
    account = bank.open_account(client.client_id, currency="RUB")
    clock.moment = NIGHT
    with pytest.raises(OperationTimeRestrictedError):
        bank.close_account(account.account_id)


# money


def test_deposit_and_withdraw(bank, client):
    account = bank.open_account(client.client_id, currency="RUB", initial_balance=100)
    assert bank.deposit(account.account_id, "50.5") == Decimal("150.50")
    assert bank.withdraw(account.account_id, 30) == Decimal("120.50")
    assert bank.suspicious_activities == []


@pytest.mark.parametrize("operation", ["deposit", "withdraw"])
def test_money_operations_are_forbidden_at_night(bank, client, clock, operation):
    account = bank.open_account(client.client_id, currency="RUB", initial_balance=100)
    clock.moment = NIGHT
    with pytest.raises(OperationTimeRestrictedError):
        getattr(bank, operation)(account.account_id, 10)
    assert account.balance == Decimal("100.00")
    assert reasons(bank) == [SuspicionReason.NIGHT_OPERATION]


def test_blocked_client_cannot_move_money(bank, client):
    account = bank.open_account(client.client_id, currency="RUB", initial_balance=100)
    client.block()
    with pytest.raises(ClientBlockedError):
        bank.withdraw(account.account_id, 10)
    assert account.balance == Decimal("100.00")


@pytest.mark.parametrize(
    ("prepare", "error_type"),
    [("freeze_account", AccountFrozenError), ("close_account", AccountClosedError)],
)
def test_operation_on_inactive_account_is_flagged(bank, client, prepare, error_type):
    account = bank.open_account(client.client_id, currency="RUB")
    getattr(bank, prepare)(account.account_id)
    with pytest.raises(error_type):
        bank.deposit(account.account_id, 10)
    [activity] = bank.suspicious_activities
    assert activity.reason is SuspicionReason.INACTIVE_ACCOUNT_OPERATION
    assert activity.account_id == account.account_id


@pytest.mark.parametrize(
    ("amount", "flagged"),
    [(499_999, False), (500_000, True)],
)
def test_large_amount_is_flagged_in_base_currency(bank, client, amount, flagged):
    account = bank.open_account(client.client_id, "premium", currency="RUB")
    bank.deposit(account.account_id, amount)
    assert reasons(bank) == ([SuspicionReason.LARGE_OPERATION] if flagged else [])


def test_large_amount_is_converted_before_the_check(bank, client):
    account = bank.open_account(client.client_id, currency="EUR")
    bank.deposit(account.account_id, 5_000)  # 500_000 RUB
    assert reasons(bank) == [SuspicionReason.LARGE_OPERATION]


def test_invalid_amount_is_rejected_before_review(bank, client):
    account = bank.open_account(client.client_id, currency="RUB")
    with pytest.raises(InvalidOperationError):
        bank.deposit(account.account_id, -5)
    assert bank.suspicious_activities == []


# queries


@pytest.fixture
def populated(bank, client):
    """Two clients with four accounts in different currencies, types and states."""
    other = bank.add_client(make_client("Olga"), PASSWORD)
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


def test_clients_ranking(bank, populated):
    newcomer = bank.add_client(make_client("Anna", "Belova"), PASSWORD)
    ranking = bank.get_clients_ranking()
    assert ranking == [
        (populated["client"], Decimal("10000.00")),
        (populated["other"], Decimal("5000.00")),
        (newcomer, Decimal("0.00")),
    ]


def test_clients_ranking_breaks_ties_by_full_name(bank):
    zoya = bank.add_client(make_client("Zoya"), PASSWORD)
    anna = bank.add_client(make_client("Anna"), PASSWORD)
    assert [client for client, _ in bank.get_clients_ranking()] == [anna, zoya]


def test_bank_uses_injected_converter(security, owner):
    rates = {"USD": 1, "RUB": "0.01", "EUR": "1.1", "KZT": "0.002", "CNY": "0.14"}
    bank = Bank(security=security, converter=CurrencyConverter(rates, base="USD"))
    bank.add_client(owner, PASSWORD)
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
