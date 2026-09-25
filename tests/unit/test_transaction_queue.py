from datetime import datetime, timedelta

import pytest

from exceptions import InvalidOperationError, InvalidTransactionStateError, TransactionNotFoundError
from models import Transaction, TransactionPriority, TransactionStatus
from services import TransactionQueue

NOW = datetime(2026, 9, 24, 14, 0)


def deposit(label: str, **params) -> Transaction:
    # the recipient id doubles as a readable label in assertions
    return Transaction("deposit", 10, "RUB", recipient_id=label, created_at=NOW, **params)


def drain(queue: TransactionQueue) -> list[str]:
    labels = []
    while (transaction := queue.next_ready()) is not None:
        labels.append(transaction.recipient_id)
    return labels


def test_higher_priority_first_and_fifo_within_a_priority(queue):
    for label, priority in [("a", "normal"), ("b", "low"), ("c", "urgent"), ("d", "normal"), ("e", "high")]:
        queue.add(deposit(label, priority=priority))
    assert len(queue) == 5
    assert [t.recipient_id for t in queue.pending()] == ["c", "e", "a", "d", "b"]
    assert drain(queue) == ["c", "e", "a", "d", "b"]
    assert len(queue) == 0


def test_delayed_transaction_waits_and_does_not_block_ready_ones(queue, clock):
    queue.add(deposit("later", priority="urgent", scheduled_at=NOW + timedelta(hours=1)))
    queue.add(deposit("now", priority="low"))
    assert [t.recipient_id for t in queue.pending()] == ["now", "later"]
    assert drain(queue) == ["now"]
    assert len(queue) == 1

    clock.moment = NOW + timedelta(hours=1)
    assert drain(queue) == ["later"]


def test_due_delayed_transaction_competes_by_priority(queue, clock):
    queue.add(deposit("delayed-high", priority="high", scheduled_at=NOW + timedelta(minutes=5)))
    queue.add(deposit("normal"))
    clock.moment = NOW + timedelta(minutes=5)
    assert drain(queue) == ["delayed-high", "normal"]


def test_cancel_removes_a_waiting_transaction(queue, clock):
    first = queue.add(deposit("a"))
    queue.add(deposit("b"))
    clock.moment = NOW + timedelta(seconds=30)
    assert queue.cancel(first.transaction_id) is first
    assert first.status is TransactionStatus.CANCELLED
    assert first.finished_at == clock.moment
    assert len(queue) == 1
    assert drain(queue) == ["b"]
    assert queue.get(first.transaction_id) is first


def test_cancel_of_a_delayed_transaction(queue, clock):
    delayed = queue.add(deposit("a", scheduled_at=NOW + timedelta(minutes=1)))
    queue.cancel(delayed.transaction_id)
    clock.moment = NOW + timedelta(minutes=1)
    assert queue.next_ready() is None


def test_cannot_cancel_a_transaction_already_handed_out(queue):
    transaction = queue.add(deposit("a"))
    assert queue.next_ready() is transaction
    with pytest.raises(InvalidOperationError, match="no longer waiting"):
        queue.cancel(transaction.transaction_id)
    assert transaction.status is TransactionStatus.PENDING


def test_unknown_id_raises_not_found(queue):
    with pytest.raises(TransactionNotFoundError):
        queue.cancel("nope")
    with pytest.raises(TransactionNotFoundError):
        queue.get("nope")


def test_rejects_duplicates_non_pending_and_non_transactions(queue):
    transaction = queue.add(deposit("a"))
    with pytest.raises(InvalidOperationError, match="already queued"):
        queue.add(transaction)
    queue.cancel(transaction.transaction_id)
    with pytest.raises(InvalidOperationError, match="Only pending"):
        queue.add(transaction)
    with pytest.raises(InvalidOperationError):
        queue.add("not a transaction")


def test_a_retried_transaction_can_be_queued_again(queue, clock):
    transaction = queue.add(deposit("a", priority=TransactionPriority.HIGH))
    queue.next_ready().start(NOW)
    transaction.retry("night window", NOW, NOW + timedelta(minutes=5))
    queue.add(transaction)
    assert queue.next_ready() is None
    clock.moment = NOW + timedelta(minutes=5)
    assert queue.next_ready() is transaction


def test_transaction_cancelled_outside_the_queue_is_dropped(queue):
    cancelled = queue.add(deposit("a"))
    queue.add(deposit("b"))
    cancelled.cancel(NOW)
    assert drain(queue) == ["b"]
    assert len(queue) == 0


def test_cancel_forgets_a_transaction_that_changed_status_elsewhere(queue):
    transaction = queue.add(deposit("a"))
    transaction.cancel(NOW)
    with pytest.raises(InvalidTransactionStateError):
        queue.cancel(transaction.transaction_id)
    assert len(queue) == 0
