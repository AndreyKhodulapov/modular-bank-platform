from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from exceptions import AuthenticationError, ClientBlockedError, InvalidOperationError, RiskBlockedError
from models import Transaction, TransactionStatus
from services import AuditLevel, Bank, FeePolicy, RiskAnalyzer, SuspicionReason, TransactionProcessor
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
    assert transaction.failure_reason.startswith("InsufficientFundsError")
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


def test_refund_after_a_failed_credit_ignores_bank_limits_and_review(security, owner, password, make_client):
    # risk control would refuse such a large transfer to a new account before the debit; switch it off
    bank = Bank(security=security, risk_analyzer=RiskAnalyzer(rules=[]))
    processor = TransactionProcessor(bank)
    client = bank.add_client(owner, password)
    other = bank.add_client(make_client("Boris"), "boris-password")
    recipient = bank.open_account(other.client_id, currency="RUB")
    for _ in range(3):  # three wrong passwords block the recipient's owner
        with pytest.raises((AuthenticationError, ClientBlockedError)):
            bank.authenticate_client(other.client_id, "wrong-password")
    cap = bank.open_account(
        client.client_id, "premium", currency="RUB", initial_balance=10_000_000, overdraft_limit=100, withdrawal_fee=10
    )
    # the debit plus the premium fee exceeds MAX_DEPOSIT, so a refund through bank.deposit() would be refused
    transaction = transfer(cap, recipient, 10_000_000)
    processor.process(transaction)
    assert transaction.status is TransactionStatus.FAILED
    assert transaction.failure_reason.startswith("ClientBlockedError")
    assert (cap.balance, recipient.balance) == (Decimal("10000000.00"), Decimal("0.00"))
    # the refund is not a client operation: one large withdrawal is reviewed, the refund is not
    assert reasons(bank).count(SuspicionReason.LARGE_OPERATION) == 2  # opening the account, then the withdrawal


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


@pytest.mark.parametrize("make", [lambda s, r: transfer(r, s, "0.01"), lambda s, r: external(s, "0.01")])
def test_amount_that_rounds_to_zero_fails_without_a_fee(processor, usd, rub, make):
    transaction = make(usd, rub)  # 0.01 RUB is 0.00 USD
    processor.process(transaction)
    assert transaction.status is TransactionStatus.FAILED
    assert transaction.failure_reason == "InvalidOperationError: 0.01 RUB is 0.00 in USD."
    assert (transaction.fee, processor.collected_fees) == (Decimal("0.00"), 0)
    assert (rub.balance, usd.balance) == (Decimal("10000.00"), Decimal("100.00"))  # nothing was debited either


def test_process_queue_reports_and_requeues(processor, queue, rub, usd, clock):
    done = queue.add(transfer(rub, usd, 900))
    short = queue.add(transfer(rub, usd, 50_000))
    frozen_target = queue.add(Transaction("deposit", 10, "RUB", recipient_id="missing", created_at=NOW))
    report = processor.process_queue(queue)
    assert (report.completed, report.failed, report.rescheduled) == ([done], [frozen_target], [short])
    assert queue.pending() == [short]
    clock.moment = short.scheduled_at
    assert processor.process_queue(queue).rescheduled == [short]


def test_process_queue_requeues_retries_even_if_an_attempt_raises(processor, queue, rub, usd, monkeypatch):
    short = queue.add(transfer(rub, usd, 50_000, priority="high"))
    untouched = queue.add(transfer(rub, usd, 900))
    original = processor.process

    def process(transaction):
        if transaction is not short:
            raise RuntimeError("boom")
        return original(transaction)

    monkeypatch.setattr(processor, "process", process)
    with pytest.raises(RuntimeError):
        processor.process_queue(queue)
    # the retry comes back, and so does the transaction the failing call never started
    assert queue.pending() == [untouched, short]  # ready ones first, then the delayed retry


def test_unexpected_error_fails_the_transaction_and_is_raised(bank, processor, rub, usd, monkeypatch):
    def withdraw(account_id, amount):
        raise RuntimeError("boom")

    monkeypatch.setattr(bank, "withdraw", withdraw)
    transaction = transfer(rub, usd, 100)
    with pytest.raises(RuntimeError):
        processor.process(transaction)
    assert transaction.status is TransactionStatus.FAILED
    assert transaction.failure_reason == "RuntimeError: boom"
    assert [(record.error_type, record.will_retry) for record in processor.errors] == [("RuntimeError", False)]
    [event] = bank.audit_log.filter(event="transaction_failed")
    assert (event.level, event.details["error_type"]) == (AuditLevel.CRITICAL, "RuntimeError")


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
    [{"max_attempts": 0}, {"retry_delay": timedelta(0)}, {"retry_delay": 5}],
)
def test_rejects_invalid_settings(bank, params):
    with pytest.raises(InvalidOperationError):
        TransactionProcessor(bank, **params)


def test_requires_a_bank():
    with pytest.raises(InvalidOperationError):
        TransactionProcessor("bank")


def test_high_risk_transaction_fails_at_once_without_moving_money(bank, processor, client, clock):
    rich = bank.open_account(client.client_id, currency="RUB", initial_balance=900_000)
    fresh = bank.open_account(client.client_id, currency="RUB")
    clock.moment = NOW.replace(hour=23)
    transaction = transfer(rich, fresh, 600_000)  # large 40 + new account 20 + late evening 20
    processor.process(transaction)
    assert transaction.status is TransactionStatus.FAILED
    assert transaction.failure_reason.startswith(RiskBlockedError.__name__)
    assert (rich.balance, fresh.balance) == (Decimal("900000.00"), Decimal("0.00"))
    assert [(record.error_type, record.will_retry) for record in processor.errors] == [("RiskBlockedError", False)]
    assert [(event.event, event.level) for event in bank.audit_log.filter(min_level="error")] == [
        ("operation_blocked", AuditLevel.CRITICAL),
        ("transaction_failed", AuditLevel.ERROR),
    ]


def test_medium_risk_transaction_goes_through(bank, processor, client):
    rich = bank.open_account(client.client_id, currency="RUB", initial_balance=900_000)
    fresh = bank.open_account(client.client_id, currency="RUB")
    transaction = transfer(rich, fresh, 600_000)  # large 40 + new account 20: medium, not blocked
    processor.process(transaction)
    assert transaction.status is TransactionStatus.COMPLETED
    assert (rich.balance, fresh.balance) == (Decimal("300000.00"), Decimal("600000.00"))


def test_outcomes_are_written_to_the_audit_log(bank, processor, client, rub, usd):
    done = transfer(rub, usd, 900)
    short = transfer(rub, usd, 50_000)
    processor.process(done)
    processor.process(short)
    events = bank.audit_log.filter(category="transaction")
    assert [(event.event, event.level, event.transaction_id) for event in events] == [
        ("transaction_completed", AuditLevel.INFO, done.transaction_id),
        ("transaction_failed", AuditLevel.ERROR, short.transaction_id),
    ]
    assert all((event.client_id, event.account_id) == (client.client_id, rub.account_id) for event in events)
    assert dict(events[0].details) == {
        "type": "transfer",
        "amount": "900.00",
        "currency": "RUB",
        "fee": "0.00",
        "attempt": 1,
    }
    assert dict(events[1].details) == {"error_type": "InsufficientFundsError", "attempt": 1, "will_retry": True}


def test_failure_for_an_unknown_account_is_logged_without_a_client(bank, processor, rub):
    transaction = Transaction("deposit", 100, "RUB", recipient_id="missing", created_at=NOW)
    processor.process(transaction)
    [event] = bank.audit_log.filter(event="transaction_failed")
    assert (event.client_id, event.account_id) == (None, "missing")


def test_completed_transfer_makes_the_recipient_known(bank, processor, client, clock):
    clock.moment = NOW - timedelta(days=30)
    sender = bank.open_account(client.client_id, currency="RUB", initial_balance=10_000)
    recipient = bank.open_account(client.client_id, currency="RUB")
    clock.moment = NOW
    processor.process(transfer(sender, recipient, 100))
    processor.process(transfer(sender, recipient, 100))
    first, second = bank.risk_analyzer.assessments
    assert (first.rules, second.rules) == (("new_recipient",), ())


@pytest.fixture
def failing_audit_log(bank, monkeypatch):
    """The bank's audit log refuses to record a failed attempt, as if the disk were full."""
    record = bank.audit_log.record

    def broken(level, category, event, *args, **kwargs):
        if event == "transaction_failed":
            raise OSError("disk full")
        return record(level, category, event, *args, **kwargs)

    monkeypatch.setattr(bank.audit_log, "record", broken)


def test_failed_attempt_is_finished_even_if_the_audit_write_fails(processor, rub, usd, failing_audit_log):
    short = transfer(rub, usd, 50_000)
    with pytest.raises(OSError):
        processor.process(short)
    assert short.status is TransactionStatus.PENDING
    assert short.scheduled_at == NOW + timedelta(minutes=5)


def test_process_queue_keeps_a_retry_when_its_audit_write_fails(processor, queue, rub, usd, failing_audit_log):
    short = queue.add(transfer(rub, usd, 50_000))
    with pytest.raises(OSError):
        processor.process_queue(queue)
    assert short.status is TransactionStatus.PENDING
    assert queue.pending() == [short]
