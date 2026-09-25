from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from exceptions import InvalidOperationError, InvalidTransactionStateError
from models import Currency, Transaction, TransactionPriority, TransactionStatus, TransactionType

CREATED = datetime(2026, 9, 24, 10, 0)
LATER = CREATED + timedelta(minutes=1)


def make_transfer(**overrides) -> Transaction:
    params = {"sender_id": "A-1", "recipient_id": "A-2", "created_at": CREATED, **overrides}
    return Transaction("transfer", "100.005", "usd", **params)


def test_new_transaction_is_pending_with_normalised_fields():
    transaction = make_transfer()
    assert transaction.transaction_type is TransactionType.TRANSFER
    assert transaction.amount == Decimal("100.01")
    assert transaction.currency is Currency.USD
    assert transaction.status is TransactionStatus.PENDING
    assert transaction.priority is TransactionPriority.NORMAL
    assert transaction.fee == Decimal("0.00")
    assert (transaction.attempts, transaction.failure_reason, transaction.finished_at) == (0, None, None)
    assert transaction.created_at == transaction.updated_at == CREATED
    assert len(transaction.transaction_id) == 36


@pytest.mark.parametrize(
    ("transaction_type", "sender_id", "recipient_id"),
    [
        ("deposit", None, "A-1"),
        ("withdrawal", "A-1", None),
        ("transfer", "A-1", "A-2"),
        ("external_transfer", "A-1", "DE89-3704-0044"),
    ],
)
def test_each_type_accepts_its_parties(transaction_type, sender_id, recipient_id):
    transaction = Transaction(transaction_type, 10, "RUB", sender_id=sender_id, recipient_id=recipient_id)
    assert (transaction.sender_id, transaction.recipient_id) == (sender_id, recipient_id)


@pytest.mark.parametrize(
    ("transaction_type", "params"),
    [
        ("deposit", {}),  # no recipient
        ("deposit", {"sender_id": "A-1", "recipient_id": "A-2"}),  # a deposit has no sender
        ("withdrawal", {"sender_id": "A-1", "recipient_id": "A-2"}),
        ("transfer", {"sender_id": "A-1"}),
        ("transfer", {"sender_id": "A-1", "recipient_id": "A-1"}),
        ("transfer", {"sender_id": "A-1", "recipient_id": "  "}),
        ("refund", {"recipient_id": "A-1"}),
        ("transfer", {"sender_id": "A-1", "recipient_id": "A-2", "priority": "asap"}),
        ("transfer", {"sender_id": "A-1", "recipient_id": "A-2", "scheduled_at": "tomorrow"}),
    ],
)
def test_rejects_invalid_parameters(transaction_type, params):
    with pytest.raises(InvalidOperationError):
        Transaction(transaction_type, 10, "RUB", **params)


@pytest.mark.parametrize(("amount", "currency"), [(0, "RUB"), (-5, "RUB"), ("ten", "RUB"), (10, "GBP")])
def test_rejects_invalid_amount_or_currency(amount, currency):
    with pytest.raises(InvalidOperationError):
        Transaction("deposit", amount, currency, recipient_id="A-1")


def test_complete_records_amounts_and_finish_time():
    transaction = make_transfer()
    transaction.start(CREATED)
    transaction.complete(LATER, fee=Decimal("1.00"), debited_amount=Decimal("101.01"), credited_amount=None)
    assert transaction.status is TransactionStatus.COMPLETED
    assert (transaction.fee, transaction.debited_amount, transaction.credited_amount) == (
        Decimal("1.00"),
        Decimal("101.01"),
        None,
    )
    assert transaction.attempts == 1
    assert transaction.updated_at == transaction.finished_at == LATER
    assert transaction.is_final


def test_retry_returns_to_pending_and_keeps_the_reason():
    transaction = make_transfer()
    transaction.start(CREATED)
    next_attempt = LATER + timedelta(minutes=5)
    transaction.retry("night window", LATER, next_attempt)
    assert transaction.status is TransactionStatus.PENDING
    assert (transaction.failure_reason, transaction.scheduled_at) == ("night window", next_attempt)
    assert not transaction.is_due(LATER)
    assert transaction.is_due(next_attempt)
    assert transaction.finished_at is None

    transaction.start(next_attempt)
    transaction.fail("frozen", next_attempt)
    assert (transaction.status, transaction.attempts, transaction.failure_reason) == (
        TransactionStatus.FAILED,
        2,
        "frozen",
    )
    assert transaction.finished_at == next_attempt


def test_cancel_only_from_pending():
    transaction = make_transfer()
    transaction.cancel(LATER)
    assert transaction.status is TransactionStatus.CANCELLED
    assert transaction.finished_at == LATER
    with pytest.raises(InvalidTransactionStateError):
        transaction.start(LATER)


@pytest.mark.parametrize(
    "illegal_move",
    [
        lambda t: t.complete(LATER, fee=Decimal(0), debited_amount=None, credited_amount=None),  # not started
        lambda t: t.fail("x", LATER),
        lambda t: t.retry("x", LATER, LATER),
        lambda t: (t.start(LATER), t.cancel(LATER)),  # a running transaction cannot be cancelled
        lambda t: (t.start(LATER), t.start(LATER)),
    ],
)
def test_illegal_transitions_are_rejected(illegal_move):
    transaction = make_transfer()
    with pytest.raises(InvalidTransactionStateError):
        illegal_move(transaction)


def test_invalid_moment_leaves_the_state_untouched():
    transaction = make_transfer()
    with pytest.raises(InvalidOperationError):
        transaction.start("soon")
    assert (transaction.status, transaction.attempts, transaction.updated_at) == (
        TransactionStatus.PENDING,
        0,
        CREATED,
    )
    transaction.start(LATER)
    with pytest.raises(InvalidOperationError):
        transaction.retry("x", LATER, "tomorrow")
    assert (transaction.status, transaction.scheduled_at) == (TransactionStatus.PROCESSING, None)


def test_to_dict_and_str():
    transaction = make_transfer(scheduled_at=LATER, priority="High")
    assert transaction.priority is TransactionPriority.HIGH
    info = transaction.to_dict()
    assert info["type"] == "transfer"
    assert info["amount"] == "100.01"
    assert info["priority"] == "high"
    assert info["scheduled_at"] == LATER.isoformat()
    assert info["finished_at"] is None
    assert "transfer" in str(transaction) and "pending" in str(transaction)
