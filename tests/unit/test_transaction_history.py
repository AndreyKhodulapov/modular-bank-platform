from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from exceptions import InvalidOperationError
from models import Currency, Transaction, TransactionStatus, TransactionType
from services import BalanceMovement, MovementKind, TransactionHistory

NOW = datetime(2026, 9, 24, 14, 0)


@pytest.fixture
def history() -> TransactionHistory:
    return TransactionHistory()


def finished(kind, *, sender=None, recipient=None, at=NOW, completed=True) -> Transaction:
    transaction = Transaction(kind, 100, "RUB", sender_id=sender, recipient_id=recipient, created_at=NOW)
    transaction.start(at)
    if completed:
        transaction.complete(at, fee=Decimal("0.00"), debited_amount=None, credited_amount=None)
    else:
        transaction.fail("InsufficientFundsError: not enough money", at)
    return transaction


def movement(history, account_id="A", amount="100", *, at=NOW, kind="deposit", transaction_id=None):
    return history.record_movement(
        moment=at,
        account_id=account_id,
        kind=kind,
        amount=amount,
        currency="RUB",
        balance_after=amount,
        transaction_id=transaction_id,
    )


def test_records_completed_and_failed_transactions_in_order(history):
    done = finished("deposit", recipient="A")
    failed = finished("withdrawal", sender="A", completed=False)
    assert history.record_transaction(done) is done
    history.record_transaction(failed)
    assert history.transactions() == [done, failed]


def test_refuses_a_transaction_that_is_not_final(history):
    pending = Transaction("deposit", 100, "RUB", recipient_id="A", created_at=NOW)
    processing = Transaction("deposit", 100, "RUB", recipient_id="A", created_at=NOW)
    processing.start(NOW)
    cancelled = Transaction("deposit", 100, "RUB", recipient_id="A", created_at=NOW)
    cancelled.cancel(NOW)
    for transaction in (pending, processing, cancelled):
        with pytest.raises(InvalidOperationError, match=transaction.status.value):
            history.record_transaction(transaction)
    assert history.transactions() == []


def test_a_transaction_enters_the_history_once(history):
    done = finished("deposit", recipient="A")
    history.record_transaction(done)
    with pytest.raises(InvalidOperationError, match="already"):
        history.record_transaction(done)
    with pytest.raises(InvalidOperationError):
        history.record_transaction("not a transaction")


def test_filters_by_account_on_either_side(history):
    incoming = history.record_transaction(finished("transfer", sender="B", recipient="A"))
    outgoing = history.record_transaction(finished("transfer", sender="A", recipient="C"))
    unrelated = history.record_transaction(finished("transfer", sender="B", recipient="C"))
    assert history.transactions(account_ids=["A"]) == [incoming, outgoing]
    assert history.transactions(account_ids={"A", "C"}) == [incoming, outgoing, unrelated]
    assert history.transactions(account_ids=[]) == []


def test_filters_by_status_type_and_finish_time(history):
    morning = history.record_transaction(finished("deposit", recipient="A", at=NOW - timedelta(hours=4)))
    failed = history.record_transaction(finished("withdrawal", sender="A", completed=False))
    evening = history.record_transaction(finished("withdrawal", sender="A", at=NOW + timedelta(hours=4)))
    assert history.transactions(status="completed") == [morning, evening]
    assert history.transactions(status=TransactionStatus.FAILED) == [failed]
    assert history.transactions(transaction_type=TransactionType.WITHDRAWAL) == [failed, evening]
    assert history.transactions(since=NOW, until=NOW + timedelta(hours=4)) == [failed]  # until is exclusive
    with pytest.raises(InvalidOperationError):
        history.transactions(transaction_type="refund")


def test_records_movements_and_filters_them_by_account_and_time(history):
    first = movement(history, "A", "100", at=NOW - timedelta(hours=1), kind="opening")
    other = movement(history, "B", "50")
    second = movement(history, "A", "-30.5", kind=MovementKind.WITHDRAWAL, transaction_id="T-1")
    assert second == BalanceMovement(
        moment=NOW,
        account_id="A",
        kind=MovementKind.WITHDRAWAL,
        amount=Decimal("-30.50"),
        currency=Currency.RUB,
        balance_after=Decimal("-30.50"),
        transaction_id="T-1",
    )
    assert history.movements() == [first, other, second]
    assert history.movements("A") == [first, second]
    assert history.movements("A", since=NOW) == [second]
    assert history.movements(until=NOW) == [first]


@pytest.mark.parametrize(
    "params",
    [
        {"amount": "0"},
        {"amount": "0.004"},  # rounds to zero
        {"at": "2026-09-24"},
        {"kind": "interest"},
    ],
)
def test_refuses_an_invalid_movement(history, params):
    with pytest.raises(InvalidOperationError):
        movement(history, **params)
    assert history.movements() == []


def test_recorded_history_cannot_be_changed_from_outside(history):
    recorded = movement(history)
    with pytest.raises(FrozenInstanceError):
        recorded.amount = Decimal("1000.00")
    history.movements().clear()
    history.transactions().append(finished("deposit", recipient="A"))
    assert (history.movements(), history.transactions()) == ([recorded], [])


def test_movement_reads_as_one_line(history):
    recorded = movement(history, "account-1234", "-30", kind="withdrawal")
    assert str(recorded) == "09-24 14:00 account- withdrawal       -30.00       -30.00 RUB"
