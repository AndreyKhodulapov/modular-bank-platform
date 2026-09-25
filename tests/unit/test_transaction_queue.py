import logging
from datetime import datetime, timedelta

import pytest

from exceptions import InvalidOperationError, InvalidTransactionStateError, TransactionNotFoundError
from models import Transaction, TransactionPriority, TransactionStatus
from services import AuditLevel, AuditLog, TransactionQueue

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


def test_a_delayed_transaction_that_becomes_due_is_traced(queue, clock, caplog):
    caplog.set_level(logging.DEBUG, logger="bank.queue")
    later = NOW + timedelta(hours=1)
    transaction = queue.add(deposit("later", scheduled_at=later))
    queue.next_ready()
    clock.moment = later
    queue.next_ready()
    [record] = [record for record in caplog.records if record.name == "bank.queue"]
    assert (record.levelno, record.getMessage()) == (logging.DEBUG, "delayed transaction is due")
    assert record.fields == {"event_time": later, "transaction_id": transaction.transaction_id, "scheduled_at": later}


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


@pytest.fixture
def audit_log() -> AuditLog:
    return AuditLog()


@pytest.fixture
def journaled(clock, audit_log) -> TransactionQueue:
    return TransactionQueue(clock=clock, audit_log=audit_log)


def test_adding_and_cancelling_are_recorded(journaled, audit_log):
    later = NOW + timedelta(hours=1)
    transfer = journaled.add(
        Transaction("transfer", 10, "RUB", sender_id="S", recipient_id="R", created_at=NOW, scheduled_at=later)
    )
    top_up = journaled.add(deposit("R", priority="urgent"))
    journaled.cancel(transfer.transaction_id)

    events = audit_log.events
    assert [(event.event, event.transaction_id, event.account_id) for event in events] == [
        ("transaction_queued", transfer.transaction_id, "S"),
        ("transaction_queued", top_up.transaction_id, "R"),
        ("transaction_cancelled", transfer.transaction_id, "S"),
    ]
    assert [event.message for event in events] == [
        "transfer of 10.00 RUB queued for 09-24 15:00",
        "deposit of 10.00 RUB queued",
        "transfer of 10.00 RUB cancelled",
    ]
    assert {(event.level, event.client_id, event.timestamp) for event in events} == {(AuditLevel.INFO, None, NOW)}
    assert dict(events[0].details) == {
        "type": "transfer",
        "amount": "10.00",
        "currency": "RUB",
        "sender_id": "S",
        "recipient_id": "R",
        "priority": "normal",
        "scheduled_at": later.isoformat(),
        "attempts": 0,
    }


def test_a_retry_is_recorded_as_queued_again(journaled, audit_log):
    transaction = journaled.add(deposit("a"))
    journaled.next_ready().start(NOW)
    transaction.retry("night window", NOW, NOW + timedelta(minutes=5))
    journaled.add(transaction)
    assert [event.details["attempts"] for event in audit_log.filter(event="transaction_queued")] == [0, 1]


def test_failed_audit_write_leaves_nothing_queued(clock, tmp_path):
    queue = TransactionQueue(clock=clock, audit_log=AuditLog(tmp_path))  # a folder, so the write fails
    transaction = deposit("a")
    with pytest.raises(OSError):
        queue.add(transaction)
    assert len(queue) == 0
    with pytest.raises(TransactionNotFoundError):
        queue.get(transaction.transaction_id)


def test_audit_log_must_be_an_audit_log(clock):
    with pytest.raises(InvalidOperationError):
        TransactionQueue(clock=clock, audit_log="audit.jsonl")
