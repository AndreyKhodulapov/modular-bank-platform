"""Ten transactions of every kind go through the queue and the processor."""

from datetime import datetime, timedelta
from decimal import Decimal

from models import Transaction, TransactionStatus
from services import SuspicionReason
from tests.helpers import history_gaps, reasons

NOW = datetime(2026, 9, 24, 14, 0)


def test_ten_transactions_through_the_queue(bank, clock, queue, processor, make_client):
    anna = bank.add_client(make_client("Anna"), "anna-password")
    boris = bank.add_client(make_client("Boris"), "boris-password")
    carl = bank.add_client(make_client("Carl"), "carl-password")
    anna_rub = bank.open_account(anna.client_id, currency="RUB", initial_balance=10_000)
    anna_eur = bank.open_account(anna.client_id, currency="EUR", initial_balance=1_000)
    boris_usd = bank.open_account(
        boris.client_id, "premium", currency="USD", initial_balance=500, overdraft_limit=1_000, withdrawal_fee=1
    )
    boris_kzt = bank.open_account(boris.client_id, currency="KZT")
    carl_rub = bank.open_account(carl.client_id, currency="RUB", initial_balance=5_000)
    bank.freeze_account(carl_rub.account_id)

    def make(kind, amount, currency, sender=None, recipient=None, **params):
        return queue.add(
            Transaction(
                kind,
                amount,
                currency,
                sender_id=sender.account_id if sender else None,
                recipient_id=recipient if isinstance(recipient, str) or recipient is None else recipient.account_id,
                created_at=NOW,
                **params,
            )
        )

    salary = make("deposit", 50_000, "RUB", recipient=anna_rub)
    to_boris = make("transfer", 9_000, "RUB", anna_rub, boris_usd, priority="high")
    abroad = make("external_transfer", 100, "USD", boris_usd, "DE89-3704-0044-0532")
    cash = make("withdrawal", 1_000, "RUB", anna_rub, priority="urgent")
    too_big = make("transfer", 2_000, "EUR", anna_eur, boris_kzt)
    overdraft = make("transfer", 1_000, "USD", boris_usd, anna_rub)
    to_frozen = make("transfer", 500, "RUB", anna_rub, carl_rub)
    later = make("deposit", 1_000, "RUB", recipient=boris_kzt, priority="low", scheduled_at=NOW + timedelta(hours=1))
    cancelled = make("transfer", 100, "RUB", anna_rub, boris_kzt, priority="low")
    exchange = make("transfer", 500, "EUR", anna_eur, boris_kzt)
    assert len(queue) == 10

    queue.cancel(cancelled.transaction_id)
    first = processor.process_queue(queue)
    # urgent, then high, then normal ones in the order they were added; the delayed one waits
    assert first.completed == [cash, to_boris, salary, abroad, overdraft, exchange]
    assert first.failed == [to_frozen]
    assert first.rescheduled == [too_big]
    assert queue.pending() == [too_big, later]

    clock.moment = NOW + timedelta(hours=1)
    second = processor.process_queue(queue)
    assert (second.completed, second.rescheduled) == ([later], [too_big])

    clock.moment = too_big.scheduled_at
    third = processor.process_queue(queue)
    assert third.failed == [too_big]
    assert len(queue) == 0

    statuses = {
        TransactionStatus.COMPLETED: [salary, to_boris, abroad, cash, overdraft, later, exchange],
        TransactionStatus.FAILED: [too_big, to_frozen],
        TransactionStatus.CANCELLED: [cancelled],
    }
    for status, transactions in statuses.items():
        assert all(transaction.status is status for transaction in transactions)

    assert anna_rub.balance == Decimal("140000.00")  # 10_000 - 1_000 - 9_000 + 50_000 + 1_000 USD
    assert anna_eur.balance == Decimal("500.00")
    assert boris_usd.balance == Decimal("-503.00")  # premium overdraft: 500 + 100 - 102 - 1_001
    assert boris_kzt.balance == Decimal("283333.34")  # 500 EUR and 1_000 RUB in tenge
    assert carl_rub.balance == Decimal("5000.00")

    assert (abroad.fee, abroad.debited_amount) == (Decimal("1.00"), Decimal("102.00"))
    assert processor.collected_fees == Decimal("90.00")
    assert too_big.attempts == 3
    assert [(record.transaction_id, record.will_retry) for record in processor.errors] == [
        (too_big.transaction_id, True),
        (to_frozen.transaction_id, False),
        (too_big.transaction_id, True),
        (too_big.transaction_id, False),
    ]
    assert reasons(bank) == [SuspicionReason.INACTIVE_ACCOUNT_OPERATION]

    # the history: every finished transaction once, in the order they finished; the cancelled one is not there
    finished = [cash, to_boris, salary, abroad, overdraft, to_frozen, exchange, later, too_big]
    assert bank.history.transactions() == finished
    assert bank.history.transactions(account_ids=[boris_kzt.account_id]) == [exchange, later, too_big]
    # every balance is the sum of its movements, and each transaction's movements name it
    assert history_gaps(bank) == {}
    for transaction in bank.history.transactions(status="completed"):
        moved = [m for m in bank.history.movements() if m.transaction_id == transaction.transaction_id]
        assert len(moved) == (2 if transaction.internal_recipient_id and transaction.sender_id else 1)
