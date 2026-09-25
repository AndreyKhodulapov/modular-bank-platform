from decimal import Decimal

import pytest

from exceptions import InvalidOperationError
from models import Currency, Transaction, TransactionStatus, TransactionType
from services import BankReport, TransactionProcessor, TransactionQueue


@pytest.fixture
def report(bank) -> BankReport:
    return BankReport(bank)


@pytest.fixture
def parties(bank, make_client):
    """Anna with 10 000 RUB, Boris with 100 USD and Vera with a frozen RUB account."""
    anna, boris, vera = (bank.add_client(make_client(name), "password-1") for name in ("Anna", "Boris", "Vera"))
    anna_rub = bank.open_account(anna.client_id, currency="RUB", initial_balance=10_000)
    boris_usd = bank.open_account(boris.client_id, currency="USD", initial_balance=100)
    vera_rub = bank.open_account(vera.client_id, currency="RUB")
    bank.freeze_account(vera_rub.account_id)
    return anna_rub, boris_usd, vera_rub


@pytest.fixture
def processed(bank, clock, parties):
    """Six transactions: three completed, one failed, one blocked by risk control, one cancelled."""
    anna_rub, boris_usd, vera_rub = parties
    queue = TransactionQueue(clock=bank.now, audit_log=bank.audit_log)
    anna, boris = anna_rub.account_id, boris_usd.account_id
    for kind, amount, currency, params in (
        ("deposit", 1_000, "RUB", {"recipient_id": anna}),
        ("transfer", 50, "USD", {"sender_id": boris, "recipient_id": anna}),  # 4 500 RUB
        ("external_transfer", 1_000, "RUB", {"sender_id": anna, "recipient_id": "DE-1"}),  # the minimal fee, 50 RUB
        ("transfer", 100, "RUB", {"sender_id": anna, "recipient_id": vera_rub.account_id}),  # frozen recipient
        ("transfer", 2_000_000, "RUB", {"sender_id": anna, "recipient_id": boris}),  # high risk
    ):
        queue.add(Transaction(kind, amount, currency, created_at=clock(), **params))
    typo = queue.add(Transaction("withdrawal", 10, "RUB", sender_id=anna, created_at=clock()))
    queue.cancel(typo.transaction_id)
    TransactionProcessor(bank).process_queue(queue)


def test_report_needs_a_bank():
    with pytest.raises(InvalidOperationError):
        BankReport("bank")


def test_statistics_of_an_empty_bank(report):
    statistics = report.transaction_statistics()
    assert statistics.total == 0
    assert statistics.by_type == {}
    assert statistics.volume == Decimal("0.00")
    assert statistics.average_amount == Decimal("0.00")
    assert statistics.largest is None
    assert statistics.failure_rate == Decimal("0.0")
    assert "largest -" in str(statistics)


@pytest.mark.usefixtures("processed")
def test_statistics_count_finished_and_cancelled_transactions(report):
    statistics = report.transaction_statistics()
    assert statistics.currency is Currency.RUB
    assert statistics.by_status == {
        TransactionStatus.COMPLETED: 3,
        TransactionStatus.FAILED: 2,
        TransactionStatus.CANCELLED: 1,
    }
    assert statistics.total == 6
    # the cancelled withdrawal never ran, so only finished transactions have a type
    assert statistics.by_type == {
        TransactionType.DEPOSIT: 1,
        TransactionType.TRANSFER: 3,
        TransactionType.EXTERNAL_TRANSFER: 1,
    }
    assert statistics.blocked_by_risk == 1
    assert (statistics.finished, statistics.failure_rate) == (5, Decimal("40.0"))


@pytest.mark.usefixtures("processed")
def test_statistics_measure_completed_transactions_in_the_base_currency(report):
    statistics = report.transaction_statistics()
    assert statistics.volume == Decimal("6500.00")  # 1 000 + 50 USD * 90 + 1 000
    assert statistics.average_amount == Decimal("2166.67")
    assert (statistics.largest.amount, statistics.largest.currency) == (Decimal("50.00"), Currency.USD)
    assert statistics.tariff_fees == Decimal("50.00")


def test_fees_are_converted_from_the_sender_currency(bank, clock, parties, report):
    _, boris_usd, _ = parties
    queue = TransactionQueue(clock=bank.now)
    queue.add(
        Transaction(
            "external_transfer", 10, "USD", sender_id=boris_usd.account_id, recipient_id="DE-1", created_at=clock()
        )
    )
    TransactionProcessor(bank).process_queue(queue)
    # the minimal fee is 50 RUB, charged as 0.56 USD, which is 50.40 RUB back in the base currency
    assert report.transaction_statistics().tariff_fees == Decimal("50.40")


@pytest.mark.usefixtures("processed")
def test_statistics_text_names_the_share_of_finished_transactions(report):
    text = str(report.transaction_statistics())
    assert text.startswith("Transactions: 6 (completed 3, failed 2, cancelled 1)\n")
    assert "failure rate 40.0% of 5 finished, blocked by risk control 1" in text


def test_tariff_fees_leave_out_the_premium_account_own_fee(bank, clock, client, report):
    premium = bank.open_account(client.client_id, "premium", currency="RUB", initial_balance=10_000, withdrawal_fee=30)
    queue = TransactionQueue(clock=bank.now)
    queue.add(
        Transaction(
            "external_transfer", 1_000, "RUB", sender_id=premium.account_id, recipient_id="DE-1", created_at=clock()
        )
    )
    (transaction,) = TransactionProcessor(bank).process_queue(queue).completed
    assert transaction.debited_amount == Decimal("1080.00")  # the amount, the tariff fee and the account's own fee
    assert report.transaction_statistics().tariff_fees == Decimal("50.00")


@pytest.mark.usefixtures("processed")
def test_reports_cannot_be_changed(report):
    statistics, summary = report.transaction_statistics(), report.total_balance()
    with pytest.raises(TypeError):
        statistics.by_status[TransactionStatus.FAILED] = 0
    with pytest.raises(TypeError):
        statistics.by_type[TransactionType.DEPOSIT] = 0
    with pytest.raises(TypeError):
        summary.by_currency[Currency.RUB] = Decimal(0)


def test_top_clients_keeps_the_first_of_the_bank_ranking(bank, make_client, report):
    for name, amount in (("Anna", 100), ("Boris", 300), ("Vera", 200), ("Gleb", 50)):
        client = bank.add_client(make_client(name), "password-1")
        bank.open_account(client.client_id, currency="RUB", initial_balance=amount)
    ranking = report.top_clients()
    assert ranking.clients == tuple(bank.get_clients_ranking()[:3])
    assert len(report.top_clients(limit=10).clients) == 4
    assert str(ranking).splitlines()[:2] == ["Top 3 clients", f"  1. {'Ivanova Boris':<30} {'300.00':>14} RUB"]


def test_top_clients_needs_a_positive_integer_limit(report):
    with pytest.raises(InvalidOperationError, match="limit must be a positive integer"):
        report.top_clients(limit=0)


def test_total_balance_by_currency_and_in_the_base_currency(bank, make_client, report):
    client = bank.add_client(make_client("Anna"), "password-1")
    bank.open_account(client.client_id, currency="USD", initial_balance=10)
    bank.open_account(client.client_id, currency="EUR", initial_balance=20)
    bank.open_account(client.client_id, currency="RUB", initial_balance=5)
    bank.open_account(client.client_id, currency="RUB", initial_balance=30)
    summary = report.total_balance()
    assert summary.by_currency == {
        Currency.EUR: Decimal("20.00"),
        Currency.RUB: Decimal("35.00"),
        Currency.USD: Decimal("10.00"),
    }
    assert summary.total == Decimal("2935.00")  # 20 * 100 + 35 + 10 * 90
    assert summary.accounts == 4
    assert str(summary).splitlines()[0] == "Total balance: 2935.00 RUB on 4 accounts"


def test_total_balance_leaves_out_closed_accounts(bank, client, report):
    bank.open_account(client.client_id, currency="RUB", initial_balance=100)
    closed = bank.open_account(client.client_id, currency="CNY", initial_balance=10)
    frozen = bank.open_account(client.client_id, currency="EUR", initial_balance=1)
    bank.close_account(closed.account_id)
    bank.freeze_account(frozen.account_id)
    summary = report.total_balance()
    assert summary.by_currency == {Currency.EUR: Decimal("1.00"), Currency.RUB: Decimal("100.00")}
    assert (summary.accounts, summary.total) == (2, Decimal("200.00"))
