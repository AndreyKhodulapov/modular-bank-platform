from datetime import datetime, timedelta
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
    RiskBlockedError,
)
from models import AccountStatus, BankAccount, InvestmentAccount, SavingsAccount, Transaction
from services import (
    AuditCategory,
    AuditLevel,
    Bank,
    CurrencyConverter,
    MovementKind,
    RiskAnalyzer,
    RiskLevel,
    SuspicionReason,
    TransactionHistory,
)
from tests.helpers import lifecycle, reasons

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


def test_clients_are_listed_in_registration_order(bank, make_client):
    first, second = (bank.add_client(make_client(name), "password-1") for name in ("Boris", "Anna"))
    assert bank.clients == [first, second]
    bank.clients.clear()  # a copy: the registry stays as it was
    assert bank.clients == [first, second]


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


def test_life_cycle_of_clients_and_accounts_is_recorded(bank, client, clock):
    account = bank.open_account(client.client_id, currency="RUB", initial_balance=100)
    bank.freeze_account(account.account_id)
    bank.unfreeze_account(account.account_id)
    client.block()
    bank.unblock_client(client.client_id)
    clock.moment += timedelta(hours=1)
    bank.close_account(account.account_id)

    events = [event for event in bank.audit_log if event.category in (AuditCategory.ACCOUNT, AuditCategory.CLIENT)]
    cid, aid = client.client_id, account.account_id
    assert [(event.event, event.client_id, event.account_id) for event in events] == [
        ("client_registered", cid, None),
        ("account_opened", cid, aid),
        ("account_frozen", cid, aid),
        ("account_unfrozen", cid, aid),
        ("client_unblocked", cid, None),
        ("account_closed", cid, aid),
    ]
    assert {event.level for event in events} == {AuditLevel.INFO}
    assert events[-1].timestamp == clock.moment
    assert dict(events[1].details) == {"account_type": "basic", "currency": "RUB", "initial_balance": "100.00"}
    assert dict(events[-1].details) == {"payout": "100.00", "currency": "RUB"}


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
    status, blocked, recorded, moved = account.status, client.is_blocked, lifecycle(bank), bank.history.movements()
    clock.moment = NIGHT
    with pytest.raises(OperationTimeRestrictedError):
        operation(bank, client, account)
    assert reasons(bank) == [SuspicionReason.NIGHT_OPERATION]
    assert (lifecycle(bank), bank.history.movements()) == (recorded, moved)
    assert (account.status, account.balance, client.is_blocked) == (status, Decimal("100.00"), blocked)
    assert client.account_ids == [account.account_id]


@pytest.mark.parametrize(("prepare", "operation"), RESTRICTED_OPERATIONS)
def test_restricted_operations_are_forbidden_for_blocked_client(bank, client, prepare, operation):
    account = bank.open_account(client.client_id, currency="RUB", initial_balance=100)
    prepare(bank, client, account)
    status, recorded, moved = account.status, lifecycle(bank), bank.history.movements()
    client.block()
    with pytest.raises(ClientBlockedError):
        operation(bank, client, account)
    assert reasons(bank) == [SuspicionReason.BLOCKED_CLIENT_ACTIVITY]
    assert (lifecycle(bank), bank.history.movements()) == (recorded, moved)
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
    recorded, moved = lifecycle(bank), bank.history.movements()
    arguments = (account.account_id, 10) if operation in ("deposit", "withdraw") else (account.account_id,)
    with pytest.raises(error_type):
        getattr(bank, operation)(*arguments)
    assert (lifecycle(bank), bank.history.movements()) == (recorded, moved)
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
    assert lifecycle(bank) == ["client_registered"]


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


def test_ensure_operational_returns_an_active_account(bank, client):
    account = bank.open_account(client.client_id, currency="RUB")
    assert bank.ensure_operational("transfer", account.account_id) is account
    assert bank.suspicious_activities == []


def test_ensure_operational_flags_a_frozen_account(bank, client):
    account = bank.open_account(client.client_id, currency="RUB")
    bank.freeze_account(account.account_id)
    with pytest.raises(AccountFrozenError):
        bank.ensure_operational("transfer", account.account_id)
    assert reasons(bank) == [SuspicionReason.INACTIVE_ACCOUNT_OPERATION]


def test_now_and_converter_come_from_the_collaborators(bank, clock):
    assert bank.now() == clock.moment
    assert bank.converter.base.value == "RUB"


# risk screening


@pytest.fixture
def pair(bank, client, clock):
    """Two RUB accounts of the client opened a month ago, so neither counts as new."""
    opened = clock.moment
    clock.moment = opened - timedelta(days=30)
    sender = bank.open_account(client.client_id, currency="RUB", initial_balance=900_000)
    recipient = bank.open_account(client.client_id, currency="RUB")
    clock.moment = opened
    return sender, recipient


def transfer(sender, recipient, amount, kind="transfer") -> Transaction:
    return Transaction(kind, amount, "RUB", sender_id=sender.account_id, recipient_id=recipient, created_at=NIGHT)


def test_bank_remembers_when_an_account_was_opened(bank, client, clock):
    account = bank.open_account(client.client_id, currency="RUB")
    assert bank.account_opened_at(account.account_id) == clock.moment
    with pytest.raises(AccountNotFoundError):
        bank.account_opened_at("missing")


def test_screen_low_risk_is_logged_as_info(bank, client, pair):
    sender, recipient = pair
    assessment = bank.screen(transfer(sender, recipient.account_id, 100))
    assert (assessment.level, assessment.rules, assessment.client_id) == (
        RiskLevel.LOW,
        ("new_recipient",),
        client.client_id,
    )
    [event] = bank.audit_log.filter(category="risk")
    assert (event.level, event.event, event.account_id) == (AuditLevel.INFO, "risk_assessed", sender.account_id)
    assert dict(event.details) == {
        "score": 20,
        "risk_level": "low",
        "factors": ("new_recipient",),
        "amount_in_base": "100.00",
    }


def test_screen_medium_risk_is_a_warning_and_goes_on(bank, pair):
    sender, recipient = pair
    assessment = bank.screen(transfer(sender, recipient.account_id, 500_000))
    assert assessment.level is RiskLevel.MEDIUM
    assert bank.audit_log.filter(category="risk")[-1].level is AuditLevel.WARNING


def test_screen_refuses_high_risk(bank, pair, clock):
    sender, recipient = pair
    clock.moment = clock.moment.replace(hour=23)
    transaction = transfer(sender, recipient.account_id, 500_000)
    with pytest.raises(RiskBlockedError) as info:
        bank.screen(transaction)
    assert (info.value.score, info.value.factors) == (80, ("large_amount", "new_recipient", "night_operation"))
    [event] = bank.audit_log.filter(event="operation_blocked")
    assert (event.level, event.transaction_id) == (AuditLevel.CRITICAL, transaction.transaction_id)
    assert sender.balance == Decimal("900000.00")  # screening never moves money


def test_screen_applies_the_hard_rules_before_scoring(bank, client, pair, clock):
    sender, recipient = pair
    clock.moment = NIGHT
    with pytest.raises(OperationTimeRestrictedError):
        bank.screen(transfer(sender, recipient.account_id, 100))
    client.block()
    clock.moment = NIGHT.replace(hour=12)
    with pytest.raises(ClientBlockedError):
        bank.screen(transfer(sender, recipient.account_id, 100))
    assert bank.risk_analyzer.assessments == []


def test_screen_deposit_is_assessed_for_the_recipient_owner(bank, client, pair):
    _, recipient = pair
    deposit = Transaction("deposit", 100, "RUB", recipient_id=recipient.account_id, created_at=NIGHT)
    assessment = bank.screen(deposit)
    assert (assessment.client_id, assessment.level) == (client.client_id, RiskLevel.LOW)


def test_screen_external_transfer_needs_only_the_sender(bank, pair):
    sender, _ = pair
    assessment = bank.screen(transfer(sender, "DE-0001", 100, kind="external_transfer"))
    assert assessment.rules == ("new_recipient",)


def test_screen_unknown_account_and_wrong_input(bank, pair):
    sender, _ = pair
    with pytest.raises(AccountNotFoundError):
        bank.screen(transfer(sender, "missing", 100))
    with pytest.raises(InvalidOperationError):
        bank.screen("transaction")


def test_bank_uses_injected_risk_analyzer_and_shares_the_audit_log(security):
    analyzer = RiskAnalyzer(rules=[])
    bank = Bank(security=security, risk_analyzer=analyzer)
    assert bank.risk_analyzer is analyzer
    assert bank.audit_log is security.audit_log
    with pytest.raises(InvalidOperationError):
        Bank(risk_analyzer="strict")


# transaction history


def test_every_balance_change_through_the_bank_is_a_movement(bank, client, clock):
    account = bank.open_account(
        client.client_id, "premium", currency="RUB", initial_balance=1_000, overdraft_limit=500, withdrawal_fee=10
    )
    clock.moment += timedelta(minutes=1)
    bank.deposit(account.account_id, 200, transaction_id="T-1")
    clock.moment += timedelta(minutes=1)
    bank.withdraw(account.account_id, 1_400)  # the account's own fee on top, into the overdraft
    clock.moment += timedelta(minutes=1)
    bank.deposit(account.account_id, 210)
    bank.close_account(account.account_id)

    movements = bank.history.movements(account.account_id)
    assert [(m.kind, m.amount, m.balance_after, m.transaction_id) for m in movements] == [
        (MovementKind.OPENING, Decimal("1000.00"), Decimal("1000.00"), None),
        (MovementKind.DEPOSIT, Decimal("200.00"), Decimal("1200.00"), "T-1"),
        (MovementKind.WITHDRAWAL, Decimal("-1410.00"), Decimal("-210.00"), None),
        (MovementKind.DEPOSIT, Decimal("210.00"), Decimal("0.00"), None),
    ]  # nothing is paid out on closing an empty account
    assert movements[0].moment == bank.account_opened_at(account.account_id)
    assert movements[-1].moment == clock.moment
    assert {movement.currency for movement in movements} == {account.currency}


def test_opening_and_closing_move_only_cash(bank, client):
    empty = bank.open_account(client.client_id, currency="USD")
    investment = bank.open_account(client.client_id, "investment", currency="EUR", initial_balance=300)
    bank.close_account(investment.account_id)
    assert bank.history.movements(empty.account_id) == []
    assert [(m.kind, m.amount, m.balance_after) for m in bank.history.movements(investment.account_id)] == [
        (MovementKind.OPENING, Decimal("300.00"), Decimal("300.00")),
        (MovementKind.PAYOUT, Decimal("-300.00"), Decimal("0.00")),
    ]


def test_refused_money_movement_is_not_recorded(bank, client):
    account = bank.open_account(client.client_id, currency="RUB", initial_balance=100)
    moved = bank.history.movements()
    with pytest.raises(InsufficientFundsError):
        bank.withdraw(account.account_id, 500)
    with pytest.raises(InvalidOperationError):
        bank.deposit(account.account_id, 0)
    assert bank.history.movements() == moved


def test_refund_ignores_the_bank_rules_but_is_recorded(bank, client, clock):
    account = bank.open_account(client.client_id, currency="RUB")
    bank.freeze_account(account.account_id)
    client.block()
    clock.moment = NIGHT
    assert bank.refund(account.account_id, 600_000, transaction_id="T-1") == Decimal("600000.00")
    [movement] = bank.history.movements(account.account_id)
    assert (movement.kind, movement.amount, movement.transaction_id, movement.moment) == (
        MovementKind.REFUND,
        Decimal("600000.00"),
        "T-1",
        NIGHT,
    )
    assert bank.suspicious_activities == []  # neither the night, the status nor the amount is reviewed


def test_bank_uses_injected_history(security):
    history = TransactionHistory()
    bank = Bank(security=security, history=history)
    assert bank.history is history
    assert Bank(security=security).history is not history
    with pytest.raises(InvalidOperationError):
        Bank(history=[])
