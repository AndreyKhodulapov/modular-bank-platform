"""End-to-end scenarios of the bank: several clients, accounts, logins and security rules."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from exceptions import (
    AccountFrozenError,
    AccountNotFoundError,
    AuthenticationError,
    ClientBlockedError,
    InvalidSessionError,
    OperationTimeRestrictedError,
    SessionExpiredError,
)
from models import AccountStatus
from services import ClientPortal, MovementKind, SuspicionReason, TransactionProcessor, TransactionQueue
from tests.helpers import history_gaps


def test_bank_day_from_registration_to_ranking(bank, clock, make_client):
    anna = bank.add_client(make_client("Anna"), "anna-password")
    boris = bank.add_client(make_client("Boris"), "boris-password")

    anna_rub = bank.open_account(anna.client_id, currency="RUB", initial_balance=20_000)
    anna_savings = bank.open_account(
        anna.client_id, "savings", currency="RUB", initial_balance=50_000, min_balance=10_000
    )
    boris_usd = bank.open_account(boris.client_id, "premium", currency="USD", initial_balance=1_000)
    assert anna.account_ids == [anna_rub.account_id, anna_savings.account_id]

    assert bank.resolve_session(bank.authenticate_client(anna.client_id, "anna-password")) is anna
    for _ in range(2):
        with pytest.raises(AuthenticationError):
            bank.authenticate_client(boris.client_id, "not-his-password")
    with pytest.raises(ClientBlockedError):
        bank.authenticate_client(boris.client_id, "not-his-password")
    with pytest.raises(ClientBlockedError):
        bank.withdraw(boris_usd.account_id, 10)
    # someone else may have typed the passwords: money sent to Boris still arrives
    assert bank.deposit(boris_usd.account_id, 10) == Decimal("1010.00")
    bank.unblock_client(boris.client_id)
    bank.authenticate_client(boris.client_id, "boris-password")
    assert bank.withdraw(boris_usd.account_id, 10) < Decimal("1010")

    bank.freeze_account(anna_rub.account_id)
    with pytest.raises(AccountFrozenError):
        bank.withdraw(anna_rub.account_id, 100)
    bank.unfreeze_account(anna_rub.account_id)
    assert bank.withdraw(anna_rub.account_id, 100) == Decimal("19900.00")

    clock.moment = datetime(2026, 9, 25, 3, 0)
    with pytest.raises(OperationTimeRestrictedError):
        bank.deposit(anna_rub.account_id, 100)
    clock.moment = datetime(2026, 9, 25, 9, 0)
    bank.deposit(anna_rub.account_id, 100)

    bank.withdraw(anna_rub.account_id, 20_000)
    bank.close_account(anna_rub.account_id)
    assert bank.search_accounts(status=AccountStatus.CLOSED) == [anna_rub]

    boris_total = boris_usd.balance * 90  # the reference USD rate
    assert bank.get_total_balance() == Decimal("50000.00") + boris_total
    assert [client for client, _ in bank.get_clients_ranking()] == [boris, anna]

    assert [activity.reason for activity in bank.suspicious_activities] == [
        SuspicionReason.FAILED_LOGIN,
        SuspicionReason.FAILED_LOGIN,
        SuspicionReason.FAILED_LOGIN,
        SuspicionReason.CLIENT_BLOCKED,
        SuspicionReason.BLOCKED_CLIENT_ACTIVITY,
        SuspicionReason.INACTIVE_ACCOUNT_OPERATION,
        SuspicionReason.NIGHT_OPERATION,
    ]
    assert history_gaps(bank) == {}
    assert [movement.kind.value for movement in bank.history.movements(anna_rub.account_id)] == [
        "opening",
        "withdrawal",
        "deposit",
        "withdrawal",
    ]  # closing an empty account pays nothing out


def test_account_type_operations_through_the_bank_keep_the_history_whole(bank, client, clock):
    investment = bank.open_account(client.client_id, "investment", currency="EUR", initial_balance=1_000)
    savings = bank.open_account(client.client_id, "savings", currency="RUB", initial_balance=1_000, monthly_rate="0.01")
    bank.invest(investment.account_id, "stocks", 400)
    bank.divest(investment.account_id, "stocks", 100)
    clock.moment += timedelta(days=31)  # interest is due a month after the opening
    bank.apply_monthly_interest(savings.account_id)
    bank.withdraw(investment.account_id, 700)
    assert history_gaps(bank) == {}
    assert bank.history.movements(investment.account_id)[-1].total_value_after == investment.total_value


def test_a_session_ends_with_its_time_a_logout_or_a_block(bank, clock, make_client):
    anna = bank.add_client(make_client("Anna"), "anna-password")
    phone = bank.authenticate_client(anna.client_id, "anna-password")
    clock.moment += timedelta(minutes=20)
    laptop = bank.authenticate_client(anna.client_id, "anna-password")

    clock.moment += timedelta(minutes=10)  # the phone's half an hour is over, the laptop's is not
    with pytest.raises(SessionExpiredError):
        bank.resolve_session(phone)
    assert bank.resolve_session(laptop) is anna

    # someone else types wrong passwords for Anna: the block ends the session she has
    for _ in range(3):
        with pytest.raises((AuthenticationError, ClientBlockedError)):
            bank.authenticate_client(anna.client_id, "guessed-password")
    with pytest.raises(InvalidSessionError):
        bank.resolve_session(laptop)

    bank.unblock_client(anna.client_id)
    with pytest.raises(InvalidSessionError):
        bank.resolve_session(laptop)
    session = bank.authenticate_client(anna.client_id, "anna-password")
    bank.logout(session)
    with pytest.raises(InvalidSessionError):
        bank.resolve_session(session)

    events = [event.event for event in bank.audit_log if event.client_id == anna.client_id]
    assert events == [
        "client_registered",
        "client_logged_in",
        "client_logged_in",
        "expired_session",
        "failed_login",
        "failed_login",
        "failed_login",
        "client_blocked",
        "client_unblocked",
        "client_logged_in",
        "client_logged_out",
    ]


def test_two_clients_act_through_their_sessions_and_the_bank_runs_the_rest(bank, clock, make_client):
    portal = ClientPortal(bank)
    queue = TransactionQueue(clock=bank.now, audit_log=bank.audit_log)
    anna = bank.add_client(make_client("Anna"), "anna-password")
    boris = bank.add_client(make_client("Boris"), "boris-password")
    anna_session = bank.authenticate_client(anna.client_id, "anna-password")
    boris_session = bank.authenticate_client(boris.client_id, "boris-password")

    anna_rub = portal.open_account(anna_session, currency="RUB")["account_id"]
    boris_rub = portal.open_account(boris_session, currency="RUB")["account_id"]
    bank.deposit(anna_rub, 5_000)  # the portal credits nothing: Anna brings the cash to the bank
    # Boris knows Anna's number: he may send money to it, not take money from it
    with pytest.raises(AccountNotFoundError):
        portal.withdraw(boris_session, anna_rub, 1_000)
    with pytest.raises(AccountNotFoundError):
        portal.submit(boris_session, queue, "transfer", 1_000, "RUB", sender_id=anna_rub, recipient_id=boris_rub)
    rent = portal.submit(anna_session, queue, "transfer", 2_000, "RUB", sender_id=anna_rub, recipient_id=boris_rub)
    assert rent.created_at == clock()

    # the processor runs the queue later, when both sessions are over
    clock.moment += timedelta(hours=1)
    TransactionProcessor(bank).process_queue(queue)
    anna_again = bank.authenticate_client(anna.client_id, "anna-password")
    boris_again = bank.authenticate_client(boris.client_id, "boris-password")
    assert [movement.kind for movement in portal.statement(anna_again, anna_rub)] == [
        MovementKind.DEPOSIT,
        MovementKind.WITHDRAWAL,
    ]
    assert portal.transactions(boris_again) == portal.transactions(anna_again) == [rent]
    assert [account["balance"] for account in portal.accounts(boris_again)] == ["2000.00"]
    assert history_gaps(bank) == {}

    # the log says who acted: each client's own session, then the bank for the execution
    opened = bank.audit_log.filter(event="account_opened")
    assert [(event.client_id, event.details["session_id"]) for event in opened] == [
        (anna.client_id, anna_session.session_id),
        (boris.client_id, boris_session.session_id),
    ]
    queued = bank.audit_log.filter(event="transaction_queued", transaction_id=rent.transaction_id)
    assert [event.details.get("session_id") for event in queued] == [anna_session.session_id]
    completed = bank.audit_log.filter(event="transaction_completed", transaction_id=rent.transaction_id)
    assert [event.details.get("session_id") for event in completed] == [None]
