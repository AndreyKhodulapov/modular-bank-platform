from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from exceptions import AuthenticationError, ClientBlockedError, InvalidOperationError, InvalidTransactionStateError
from models import Transaction, TransactionStatus
from services import FeePolicy, SuspicionReason, TransactionProcessor
from tests.helpers import reasons

NOW = datetime(2026, 9, 24, 14, 0)
NIGHT = datetime(2026, 9, 25, 2, 30)


@pytest.fixture
def rub(bank, client):
    return bank.open_account(client.client_id, currency="RUB", initial_balance=10_000)


@pytest.fixture
def usd(bank, client):
    return bank.open_account(client.client_id, currency="USD", initial_balance=100)


@pytest.fixture
def premium(bank, client):
    return bank.open_account(
        client.client_id, "premium", currency="RUB", initial_balance=1_000, overdraft_limit=5_000, withdrawal_fee=10
    )


def transfer(sender, recipient, amount, currency="RUB", **params) -> Transaction:
    return Transaction(
        "transfer",
        amount,
        currency,
        sender_id=sender.account_id,
        recipient_id=recipient.account_id,
        created_at=NOW,
        **params,
    )


def external(sender, amount, currency="RUB") -> Transaction:
    return Transaction(
        "external_transfer", amount, currency, sender_id=sender.account_id, recipient_id="KZ-0001", created_at=NOW
    )


def test_deposit_credits_converted_amount(processor, usd):
    transaction = Transaction("deposit", 900, "RUB", recipient_id=usd.account_id, created_at=NOW)
    processor.process(transaction)
    assert transaction.status is TransactionStatus.COMPLETED
    assert (transaction.debited_amount, transaction.credited_amount) == (None, Decimal("10.00"))
    assert usd.balance == Decimal("110.00")


def test_withdrawal_debits_the_account(processor, rub):
    transaction = Transaction("withdrawal", 1_000, "RUB", sender_id=rub.account_id, created_at=NOW)
    processor.process(transaction)
    assert transaction.status is TransactionStatus.COMPLETED
    assert transaction.debited_amount == Decimal("1000.00")
    assert rub.balance == Decimal("9000.00")


def test_transfer_converts_into_each_account_currency(processor, rub, usd):
    transaction = transfer(usd, rub, 10, currency="EUR")  # 10 EUR = 1000 RUB = 11.11 USD
    processor.process(transaction)
    assert transaction.status is TransactionStatus.COMPLETED
    assert (transaction.fee, transaction.debited_amount, transaction.credited_amount) == (
        Decimal("0.00"),
        Decimal("11.11"),
        Decimal("1000.00"),
    )
    assert (usd.balance, rub.balance) == (Decimal("88.89"), Decimal("11000.00"))
    assert (transaction.finished_at, transaction.attempts) == (NOW, 1)


def test_external_transfer_charges_fee_with_the_debit(processor, rub, usd):
    rub_transfer = external(rub, 1_000)
    usd_transfer = external(usd, 50, currency="USD")
    processor.process(rub_transfer)
    processor.process(usd_transfer)
    assert (rub_transfer.fee, rub_transfer.debited_amount, rub_transfer.credited_amount) == (
        Decimal("50.00"),
        Decimal("1050.00"),
        None,
    )
    assert rub.balance == Decimal("8950.00")
    assert (usd_transfer.fee, usd.balance) == (Decimal("0.56"), Decimal("49.44"))
    assert processor.collected_fees == Decimal("50.00") + Decimal("50.40")  # 0.56 USD at 90


def test_premium_account_may_go_negative_and_pays_its_own_fee(processor, premium, rub):
    transaction = transfer(premium, rub, 3_000)
    processor.process(transaction)
    assert transaction.status is TransactionStatus.COMPLETED
    assert transaction.fee == Decimal("0.00")
    assert transaction.debited_amount == Decimal("3010.00")  # the account's own withdrawal fee on top
    assert premium.balance == Decimal("-2010.00")


@pytest.mark.parametrize("make", [lambda s, r: transfer(s, r, 10_001), lambda s, r: external(s, 9_990)])
def test_regular_account_cannot_go_negative(processor, rub, usd, make):
    transaction = make(rub, usd)
    processor.process(transaction)
    assert transaction.status is TransactionStatus.PENDING  # insufficient funds is retried
    assert "Only premium accounts may go below zero" in transaction.failure_reason
    assert (rub.balance, usd.balance) == (Decimal("10000.00"), Decimal("100.00"))


@pytest.mark.parametrize("frozen_side", ["sender", "recipient"])
def test_frozen_account_fails_without_retry(bank, processor, rub, usd, frozen_side):
    frozen = rub if frozen_side == "sender" else usd
    bank.freeze_account(frozen.account_id)
    transaction = transfer(rub, usd, 100)
    processor.process(transaction)
    assert transaction.status is TransactionStatus.FAILED
    assert transaction.failure_reason.startswith("AccountFrozenError")
    assert (rub.balance, usd.balance) == (Decimal("10000.00"), Decimal("100.00"))
    assert reasons(bank) == [SuspicionReason.INACTIVE_ACCOUNT_OPERATION]


def test_closed_or_unknown_account_fails(bank, processor, rub, usd):
    bank.withdraw(usd.account_id, 100)
    bank.close_account(usd.account_id)
    to_closed = transfer(rub, usd, 100)
    to_nowhere = Transaction("deposit", 10, "RUB", recipient_id="no-such-account", created_at=NOW)
    for transaction in (to_closed, to_nowhere):
        processor.process(transaction)
        assert transaction.status is TransactionStatus.FAILED
    assert to_closed.failure_reason.startswith("AccountClosedError")
    assert to_nowhere.failure_reason.startswith("AccountNotFoundError")


def test_failed_credit_returns_the_debit(bank, processor, premium, client, make_client):
    other = bank.add_client(make_client("Boris"), "boris-password")
    recipient = bank.open_account(other.client_id, currency="RUB")
    for _ in range(3):  # three wrong passwords block the recipient's owner
        with pytest.raises((AuthenticationError, ClientBlockedError)):
            bank.authenticate_client(other.client_id, "wrong-password")
    transaction = transfer(premium, recipient, 500)
    processor.process(transaction)
    assert transaction.status is TransactionStatus.FAILED
    assert transaction.failure_reason.startswith("ClientBlockedError")
    assert premium.balance == Decimal("1000.00")  # debit of 510 returned
    assert recipient.balance == Decimal("0.00")


def test_night_window_is_retried_with_exponential_delay(processor, rub, usd, clock):
    clock.moment = NIGHT
    transaction = transfer(rub, usd, 900)
    processor.process(transaction)
    assert transaction.status is TransactionStatus.PENDING
    assert transaction.scheduled_at == NIGHT + timedelta(minutes=5)

    clock.moment = transaction.scheduled_at
    processor.process(transaction)
    assert transaction.scheduled_at == NIGHT + timedelta(minutes=15)  # 5 more, then 10

    clock.moment = transaction.scheduled_at
    processor.process(transaction)
    assert (transaction.status, transaction.attempts) == (TransactionStatus.FAILED, 3)
    assert transaction.failure_reason.startswith("OperationTimeRestrictedError")
    assert [(record.attempt, record.will_retry) for record in processor.errors] == [(1, True), (2, True), (3, False)]
    assert {record.error_type for record in processor.errors} == {"OperationTimeRestrictedError"}


def test_retry_succeeds_once_money_arrives(bank, processor, rub, usd, clock):
    transaction = transfer(rub, usd, 12_000)
    processor.process(transaction)
    assert transaction.status is TransactionStatus.PENDING
    bank.deposit(rub.account_id, 5_000)
    clock.moment = transaction.scheduled_at
    processor.process(transaction)
    assert transaction.status is TransactionStatus.COMPLETED
    assert transaction.failure_reason is None
    assert rub.balance == Decimal("3000.00")


def test_invalid_amount_after_conversion_fails_at_once(processor, rub, usd):
    transaction = transfer(rub, usd, "0.01")  # 0.01 RUB is 0.00 USD
    processor.process(transaction)
    assert transaction.status is TransactionStatus.FAILED
    assert transaction.failure_reason.startswith("InvalidOperationError")


def test_process_queue_reports_and_requeues(processor, queue, rub, usd, clock):
    done = queue.add(transfer(rub, usd, 900))
    short = queue.add(transfer(rub, usd, 50_000))
    frozen_target = queue.add(Transaction("deposit", 10, "RUB", recipient_id="missing", created_at=NOW))
    report = processor.process_queue(queue)
    assert (report.completed, report.failed, report.rescheduled) == ([done], [frozen_target], [short])
    assert queue.pending() == [short]
    clock.moment = short.scheduled_at
    assert processor.process_queue(queue).rescheduled == [short]


def test_cannot_process_a_finished_transaction(processor, rub, usd):
    transaction = transfer(rub, usd, 900)
    processor.process(transaction)
    with pytest.raises(InvalidTransactionStateError):
        processor.process(transaction)


def test_custom_fee_policy_and_single_attempt(bank, rub):
    processor = TransactionProcessor(bank, fee_policy=FeePolicy(rate=0, minimum=0, maximum=0), max_attempts=1)
    free = external(rub, 1_000)
    too_big = external(rub, 20_000)
    processor.process(free)
    processor.process(too_big)
    assert (free.fee, rub.balance) == (Decimal("0.00"), Decimal("9000.00"))
    assert too_big.status is TransactionStatus.FAILED


@pytest.mark.parametrize(
    "params",
    [{"max_attempts": 0}, {"max_attempts": True}, {"retry_delay": timedelta(0)}, {"retry_delay": 5}],
)
def test_rejects_invalid_settings(bank, params):
    with pytest.raises(InvalidOperationError):
        TransactionProcessor(bank, **params)


def test_requires_a_bank():
    with pytest.raises(InvalidOperationError):
        TransactionProcessor("bank")
