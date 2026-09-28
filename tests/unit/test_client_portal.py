from datetime import datetime
from decimal import Decimal

import pytest

from exceptions import (
    AccountNotFoundError,
    AuthenticationError,
    ClientBlockedError,
    InvalidOperationError,
    InvalidSessionError,
    OperationTimeRestrictedError,
    SessionExpiredError,
)
from models import AssetType, BankAccount, Transaction
from services import ClientPortal, MovementKind, TransactionProcessor, TransactionQueue

NIGHT = datetime(2026, 9, 25, 2, 30)


@pytest.fixture
def portal(bank):
    return ClientPortal(bank)


@pytest.fixture
def session(bank, client, password):
    return bank.authenticate_client(client.client_id, password)


@pytest.fixture
def own(bank, client):
    return bank.open_account(client.client_id, currency="RUB", initial_balance=1_000)


@pytest.fixture
def foreign(bank, make_client):
    boris = bank.add_client(make_client("Boris"), "boris-password")
    return bank.open_account(boris.client_id, "investment", currency="RUB", initial_balance=1_000)


def session_ids(bank, event):
    return [e.details.get("session_id") for e in bank.audit_log.filter(event=event)]


def test_portal_needs_a_bank():
    with pytest.raises(InvalidOperationError):
        ClientPortal(object())


def test_accounts_are_the_clients_own_as_snapshots(portal, session, own, foreign):
    accounts = portal.accounts(session)
    assert [account["account_id"] for account in accounts] == [own.account_id]
    assert accounts[0]["balance"] == "1000.00"
    assert not any(isinstance(account, BankAccount) for account in accounts)


def test_open_account_is_the_clients_and_names_the_session(portal, session, client, bank):
    opened = portal.open_account(session, "savings", currency="RUB", initial_balance=500, min_balance=100)
    assert opened["account_id"] in client.account_ids
    assert opened["account_type"] == "SavingsAccount"
    assert session_ids(bank, "account_opened") == [session.session_id]


@pytest.mark.parametrize("name", ["actor", "client_id"])
def test_open_account_refuses_the_names_the_portal_sets(portal, session, client, name):
    with pytest.raises(InvalidOperationError, match=name):
        portal.open_account(session, currency="RUB", **{name: "someone-else"})
    assert len(client.account_ids) == 0


def test_deposit_and_withdraw_move_the_clients_money(portal, session, own):
    assert portal.deposit(session, own.account_id, 500) == Decimal("1500.00")
    assert portal.withdraw(session, own.account_id, 200) == Decimal("1300.00")
    statement = portal.statement(session, own.account_id)
    assert [movement.kind for movement in statement] == [
        MovementKind.OPENING,
        MovementKind.DEPOSIT,
        MovementKind.WITHDRAWAL,
    ]
    assert [movement.session_id for movement in statement] == [None, session.session_id, session.session_id]


def test_large_amount_names_the_session(portal, session, own, bank):
    portal.deposit(session, own.account_id, 600_000)
    assert session_ids(bank, "large_operation") == [session.session_id]


def test_invest_and_divest_on_the_clients_investment_account(portal, session, bank, client):
    account = bank.open_account(client.client_id, "investment", currency="RUB", initial_balance=1_000)
    assert portal.invest(session, account.account_id, AssetType.BONDS, 400) == Decimal("600.00")
    assert portal.divest(session, account.account_id, "bonds", 100) == Decimal("700.00")


def test_freeze_unfreeze_and_close_name_the_session(portal, session, own, bank):
    assert portal.freeze_account(session, own.account_id)["status"] == "frozen"
    assert portal.unfreeze_account(session, own.account_id)["status"] == "active"
    assert portal.close_account(session, own.account_id) == Decimal("1000.00")
    for event in ("account_frozen", "account_unfrozen", "account_closed"):
        assert session_ids(bank, event) == [session.session_id]


def test_operation_refused_by_the_bank_names_the_session(portal, own, bank, client, password, clock):
    clock.moment = NIGHT  # a login is allowed at night, moving money is not
    session = bank.authenticate_client(client.client_id, password)
    with pytest.raises(OperationTimeRestrictedError):
        portal.withdraw(session, own.account_id, 100)
    [attempt] = bank.audit_log.filter(event="night_operation")
    assert (attempt.client_id, attempt.details["session_id"]) == (own.owner.client_id, session.session_id)


FOREIGN_OPERATIONS = [
    pytest.param(lambda portal, session, account_id: portal.close_account(session, account_id), id="close"),
    pytest.param(lambda portal, session, account_id: portal.freeze_account(session, account_id), id="freeze"),
    pytest.param(lambda portal, session, account_id: portal.unfreeze_account(session, account_id), id="unfreeze"),
    pytest.param(lambda portal, session, account_id: portal.deposit(session, account_id, 10), id="deposit"),
    pytest.param(lambda portal, session, account_id: portal.withdraw(session, account_id, 10), id="withdraw"),
    pytest.param(lambda portal, session, account_id: portal.invest(session, account_id, "bonds", 10), id="invest"),
    pytest.param(lambda portal, session, account_id: portal.divest(session, account_id, "bonds", 10), id="divest"),
    pytest.param(lambda portal, session, account_id: portal.statement(session, account_id), id="statement"),
]


@pytest.mark.parametrize("operation", FOREIGN_OPERATIONS)
def test_someone_elses_account_is_not_found(portal, session, foreign, bank, operation):
    events = len(bank.audit_log)
    with pytest.raises(AccountNotFoundError) as info:
        operation(portal, session, foreign.account_id)
    assert info.value.account_id == foreign.account_id
    assert (foreign.balance, foreign.status.value) == (Decimal("1000.00"), "active")
    assert len(bank.audit_log) == events


@pytest.mark.parametrize("operation", FOREIGN_OPERATIONS)
def test_an_unknown_account_gets_the_same_answer(portal, session, operation):
    with pytest.raises(AccountNotFoundError, match="Account ACC-404 not found"):
        operation(portal, session, "ACC-404")


def test_submit_queues_a_transfer_from_the_clients_account(portal, session, own, foreign, bank, clock):
    queue = TransactionQueue(clock=bank.now, audit_log=bank.audit_log)
    transfer = Transaction(
        "transfer", 300, "RUB", sender_id=own.account_id, recipient_id=foreign.account_id, created_at=clock()
    )
    assert portal.submit(session, transfer, queue) is transfer
    assert session_ids(bank, "transaction_queued") == [session.session_id]

    TransactionProcessor(bank).process_queue(queue)  # later, by the bank itself
    assert (own.balance, foreign.balance) == (Decimal("700.00"), Decimal("1300.00"))
    assert portal.transactions(session) == [transfer]
    assert "session_id" not in bank.audit_log.filter(event="transaction_completed")[0].details


@pytest.mark.parametrize(
    ("kind", "parties"),
    [
        pytest.param("transfer", lambda own, foreign: {"sender_id": foreign, "recipient_id": own}, id="from foreign"),
        pytest.param("deposit", lambda own, foreign: {"recipient_id": foreign}, id="deposit to foreign"),
    ],
)
def test_submit_refuses_a_transaction_of_another_account(portal, session, own, foreign, queue, clock, kind, parties):
    transaction = Transaction(kind, 10, "RUB", created_at=clock(), **parties(own.account_id, foreign.account_id))
    with pytest.raises(AccountNotFoundError):
        portal.submit(session, transaction, queue)
    assert len(queue) == 0


def test_submit_rejects_what_is_not_a_transaction_or_a_queue(portal, session, own, queue, clock):
    with pytest.raises(InvalidOperationError):
        portal.submit(session, "transfer", queue)
    transaction = Transaction("deposit", 10, "RUB", recipient_id=own.account_id, created_at=clock())
    with pytest.raises(InvalidOperationError):
        portal.submit(session, transaction, [])


def test_transactions_show_the_clients_only(portal, session, own, foreign, bank, clock):
    processor = TransactionProcessor(bank)
    mine = Transaction("deposit", 10, "RUB", recipient_id=own.account_id, created_at=clock())
    theirs = Transaction("deposit", 10, "RUB", recipient_id=foreign.account_id, created_at=clock())
    for transaction in (mine, theirs):
        processor.process(transaction)
    assert portal.transactions(session) == [mine]


def test_expired_session_is_refused(portal, session, own, clock):
    clock.moment = session.expires_at
    with pytest.raises(SessionExpiredError):
        portal.withdraw(session, own.account_id, 10)
    assert own.balance == Decimal("1000.00")


def test_logged_out_session_is_refused(portal, session, bank):
    bank.logout(session)
    with pytest.raises(InvalidSessionError):
        portal.accounts(session)


def test_blocked_clients_session_is_refused(portal, session, own, bank, client):
    for _ in range(3):
        with pytest.raises((AuthenticationError, ClientBlockedError)):
            bank.authenticate_client(client.client_id, "wrong-password")
    with pytest.raises(InvalidSessionError):
        portal.withdraw(session, own.account_id, 10)
    assert own.balance == Decimal("1000.00")
