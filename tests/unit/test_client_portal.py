from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from exceptions import (
    AccountNotFoundError,
    InvalidOperationError,
    InvalidSessionError,
    OperationTimeRestrictedError,
)
from models import AssetType, BankAccount, Transaction
from services import ClientPortal, MovementKind, TransactionProcessor, TransactionQueue


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


def test_open_account_is_the_clients_empty_and_names_the_session(portal, session, client, bank):
    opened = portal.open_account(session, "premium", currency="RUB", overdraft_limit=100)
    assert opened["account_id"] in client.account_ids
    assert (opened["account_type"], opened["balance"]) == ("PremiumAccount", "0.00")
    assert session_ids(bank, "account_opened") == [session.session_id]


@pytest.mark.parametrize(
    ("name", "value"),
    [("actor", "someone-else"), ("client_id", "someone-else"), ("initial_balance", 500)],
)
def test_open_account_refuses_the_names_the_portal_sets(portal, session, client, name, value):
    with pytest.raises(InvalidOperationError, match=name):
        portal.open_account(session, currency="RUB", **{name: value})
    assert len(client.account_ids) == 0


def test_the_portal_does_not_credit_an_account():
    assert not hasattr(ClientPortal, "deposit")


def test_withdraw_moves_the_clients_money(portal, session, own):
    assert portal.withdraw(session, own.account_id, 200) == Decimal("800.00")
    statement = portal.statement(session, own.account_id)
    assert [movement.kind for movement in statement] == [MovementKind.OPENING, MovementKind.WITHDRAWAL]
    assert [movement.session_id for movement in statement] == [None, session.session_id]


def test_large_amount_names_the_session(portal, session, bank, client):
    account = bank.open_account(client.client_id, currency="RUB", initial_balance=600_000)
    portal.withdraw(session, account.account_id, 600_000)
    assert session_ids(bank, "large_operation") == [None, session.session_id]  # the opening, then the withdrawal


def test_invest_and_divest_on_the_clients_investment_account(portal, session, bank, client):
    account = bank.open_account(client.client_id, "investment", currency="RUB", initial_balance=1_000)
    assert portal.invest(session, account.account_id, AssetType.BONDS, 400) == Decimal("600.00")
    assert portal.divest(session, account.account_id, "bonds", 100) == Decimal("700.00")


def test_freeze_and_close_name_the_session(portal, session, own, bank):
    assert portal.freeze_account(session, own.account_id)["status"] == "frozen"
    bank.unfreeze_account(own.account_id)  # lifting a freeze is the bank's decision
    assert portal.close_account(session, own.account_id) == Decimal("1000.00")
    for event in ("account_frozen", "account_closed"):
        assert session_ids(bank, event) == [session.session_id]
    assert session_ids(bank, "account_unfrozen") == [None]


def test_a_client_cannot_lift_a_freeze():
    assert not hasattr(ClientPortal, "unfreeze_account")


def test_operation_refused_by_the_bank_names_the_session(portal, own, bank, client, password, clock):
    clock.moment = datetime(2026, 9, 25, 2, 30)  # a login is allowed at night, moving money is not
    session = bank.authenticate_client(client.client_id, password)
    with pytest.raises(OperationTimeRestrictedError):
        portal.withdraw(session, own.account_id, 100)
    [attempt] = bank.audit_log.filter(event="night_operation")
    assert (attempt.client_id, attempt.details["session_id"]) == (own.owner.client_id, session.session_id)


FOREIGN_OPERATIONS = [
    pytest.param(lambda portal, session, account_id: portal.close_account(session, account_id), id="close"),
    pytest.param(lambda portal, session, account_id: portal.freeze_account(session, account_id), id="freeze"),
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


def test_submit_builds_the_transfer_from_the_clients_account_and_queues_it(portal, session, own, foreign, bank, clock):
    queue = TransactionQueue(clock=bank.now, audit_log=bank.audit_log)
    clock.moment += timedelta(minutes=10)  # the bank's clock now, whatever the client says
    own_id, foreign_id = own.account_id, foreign.account_id
    transfer = portal.submit(session, queue, "transfer", 300, "RUB", sender_id=own_id, recipient_id=foreign_id)
    assert (transfer.sender_id, transfer.recipient_id, transfer.amount) == (own_id, foreign_id, 300)
    assert (transfer.created_at, transfer.requested_at, transfer.priority.name) == (clock(), clock(), "NORMAL")
    assert queue.get(transfer.transaction_id) is transfer
    assert session_ids(bank, "transaction_queued") == [session.session_id]

    TransactionProcessor(bank).process_queue(queue)  # later, by the bank itself
    assert (own.balance, foreign.balance) == (Decimal("700.00"), Decimal("1300.00"))
    assert portal.transactions(session) == [transfer]
    assert "session_id" not in bank.audit_log.filter(event="transaction_completed")[0].details


def test_submit_keeps_the_clients_priority_and_schedule(portal, session, own, queue, clock):
    tomorrow = clock() + timedelta(days=1)
    withdrawal = portal.submit(
        session, queue, "withdrawal", 10, "RUB", sender_id=own.account_id, priority="urgent", scheduled_at=tomorrow
    )
    assert (withdrawal.priority.name, withdrawal.scheduled_at) == ("URGENT", tomorrow)
    assert withdrawal.requested_at == tomorrow
    assert queue.pending() == [withdrawal]


def test_submit_cannot_move_the_request_into_the_past(portal, session, own, queue, clock):
    # the night factor is scored by the schedule when there is one: a schedule in the past would dodge it
    earlier = clock() - timedelta(minutes=1)
    with pytest.raises(InvalidOperationError, match="scheduled_at"):
        portal.submit(session, queue, "withdrawal", 10, "RUB", sender_id=own.account_id, scheduled_at=earlier)
    assert len(queue) == 0


def test_submit_refuses_a_transfer_from_another_account(portal, session, own, foreign, queue):
    with pytest.raises(AccountNotFoundError):
        portal.submit(session, queue, "transfer", 10, "RUB", sender_id=foreign.account_id, recipient_id=own.account_id)
    assert len(queue) == 0


def test_submit_refuses_a_deposit_even_to_the_clients_own_account(portal, session, own, queue):
    with pytest.raises(InvalidOperationError, match="the bank makes it"):
        portal.submit(session, queue, "deposit", 10, "RUB", sender_id=own.account_id)
    assert len(queue) == 0


def test_submit_rejects_bad_input_before_the_queue_sees_it(portal, session, own, queue):
    with pytest.raises(InvalidOperationError, match="queue"):
        portal.submit(session, [], "withdrawal", 10, "RUB", sender_id=own.account_id)
    with pytest.raises(InvalidOperationError, match="transaction type"):
        portal.submit(session, queue, "refund", 10, "RUB", sender_id=own.account_id)
    with pytest.raises(InvalidOperationError):
        portal.submit(session, queue, "withdrawal", -10, "RUB", sender_id=own.account_id)
    assert len(queue) == 0


def test_transactions_show_the_clients_only(portal, session, own, foreign, bank, clock):
    processor = TransactionProcessor(bank)
    mine = Transaction("deposit", 10, "RUB", recipient_id=own.account_id, created_at=clock())
    theirs = Transaction("deposit", 10, "RUB", recipient_id=foreign.account_id, created_at=clock())
    for transaction in (mine, theirs):
        processor.process(transaction)
    assert portal.transactions(session) == [mine]


def test_an_invalid_session_is_refused_before_anything_happens(portal, session, own, bank):
    bank.logout(session)
    events = len(bank.audit_log)
    with pytest.raises(InvalidSessionError):
        portal.withdraw(session, own.account_id, 10)
    assert (own.balance, len(bank.audit_log)) == (Decimal("1000.00"), events)
