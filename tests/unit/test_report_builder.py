import csv
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from exceptions import ClientNotFoundError, InvalidOperationError
from models import AccountStatus, ClientStatus, Currency, Transaction, TransactionStatus, TransactionType
from reporting import Report, ReportBuilder, ReportKind
from services import AuditCategory, MovementKind, RiskLevel
from utils import ManualClock

CALL_TIME = datetime(2026, 9, 26, 10, 0, 5)


@pytest.fixture
def builder(bank, tmp_path) -> ReportBuilder:
    return ReportBuilder(bank, tmp_path / "reports", clock=ManualClock(CALL_TIME))


def rows(report: Report, name: str) -> list[dict[str, object]]:
    return report.section(name).to_data()


def test_builder_needs_a_bank(tmp_path):
    with pytest.raises(InvalidOperationError, match="bank must be a Bank"):
        ReportBuilder("bank", tmp_path)


@pytest.mark.usefixtures("processed")
def test_client_report_shows_accounts_transactions_statement_and_risk(clock, parties, builder):
    anna_rub, boris_usd, vera_rub = parties
    report = builder.client_report(anna_rub.owner.client_id)
    assert (report.kind, report.title) == (ReportKind.CLIENT, "Client report: Ivanova Anna")
    assert (report.generated_at, report.currency) == (clock(), Currency.RUB)
    assert [section.name for section in report.sections] == [
        "summary",
        "accounts",
        "transactions",
        "statement",
        "risk_profile",
    ]
    summary = report.section("summary").to_data()
    assert summary["status"] is ClientStatus.ACTIVE
    assert (summary["accounts"], summary["total_value"]) == (1, Decimal("14450.00"))
    assert (summary["period_start"], summary["period_end"]) == (None, None)
    assert (summary["transactions"], summary["completed"], summary["failed"]) == (5, 3, 2)

    (account,) = rows(report, "accounts")
    assert account["account_id"] == anna_rub.account_id
    assert (account["status"], account["balance"], account["value_in_base"]) == (
        AccountStatus.ACTIVE,
        Decimal("14450.00"),
        Decimal("14450.00"),
    )
    # the side each transaction is on, and the account on the other side
    assert [(row["direction"], row["counterparty"], row["status"]) for row in rows(report, "transactions")] == [
        ("incoming", None, TransactionStatus.COMPLETED),  # a deposit has no sender
        ("incoming", boris_usd.account_id, TransactionStatus.COMPLETED),
        ("outgoing", "DE-1", TransactionStatus.COMPLETED),
        ("outgoing", vera_rub.account_id, TransactionStatus.FAILED),
        ("outgoing", boris_usd.account_id, TransactionStatus.FAILED),
    ]
    statement = rows(report, "statement")
    assert [row["kind"] for row in statement] == [
        MovementKind.OPENING,
        MovementKind.DEPOSIT,
        MovementKind.DEPOSIT,
        MovementKind.WITHDRAWAL,
    ]
    assert statement[-1]["balance_after"] == anna_rub.balance
    risk = report.section("risk_profile").to_data()
    assert (risk["level"], risk["blocked"], risk["max_score"]) == (RiskLevel.HIGH, 1, 90)


def test_transfer_between_own_accounts_is_internal(bank, client, builder, processor, queue):
    first = bank.open_account(client.client_id, currency="RUB", initial_balance=500)
    second = bank.open_account(client.client_id, currency="RUB")
    queue.add(Transaction("transfer", 100, "RUB", sender_id=first.account_id, recipient_id=second.account_id))
    processor.process_queue(queue)
    (row,) = rows(builder.client_report(client.client_id), "transactions")
    assert (row["direction"], row["counterparty"]) == ("internal", second.account_id)


def test_client_chart_orders_the_statuses_as_the_bank_does(bank, client, builder, processor, queue):
    account = bank.open_account(client.client_id, currency="RUB", initial_balance=500)
    frozen = bank.open_account(client.client_id, currency="RUB")
    bank.freeze_account(frozen.account_id)
    queue.add(Transaction("transfer", 100, "RUB", sender_id=account.account_id, recipient_id=frozen.account_id))
    processor.process_queue(queue)  # the first transaction fails
    queue.add(Transaction("withdrawal", 100, "RUB", sender_id=account.account_id))
    processor.process_queue(queue)
    statuses = builder.client_report(client.client_id).charts[1]
    assert (statuses.labels, statuses.values) == (("completed", "failed"), (1, 1))


def test_balance_chart_sums_the_smallest_accounts_past_the_line_limit(bank, client, builder):
    accounts = [
        bank.open_account(client.client_id, currency="RUB", initial_balance=100 * number) for number in range(1, 10)
    ]
    report = builder.client_report(client.client_id)
    balance = report.charts[2]
    # the seven largest keep their lines in the order they were opened; the two smallest are summed
    assert list(balance.series) == [
        *(f"BankAccount RUB {account.account_id[:8]}" for account in accounts[2:]),
        "Other 2 accounts",
    ]
    assert balance.series["Other 2 accounts"][-1][1] == Decimal("300.00")
    paths = builder.save_charts(report)
    assert "2026-09-26_10-00-05_client_balance.png" in [path.name for path in paths]


@pytest.mark.usefixtures("processed")
def test_client_report_period_limits_transactions_and_statement(bank, clock, parties, builder):
    anna_rub, _, _ = parties
    clock.moment = datetime(2026, 9, 24, 15, 0)
    bank.withdraw(anna_rub.account_id, 450)
    later = builder.client_report(anna_rub.owner.client_id, since=clock())
    assert later.section("summary").to_data()["period_start"] == clock()
    assert rows(later, "transactions") == []  # a back-office withdrawal is not a transaction
    assert [row["amount"] for row in rows(later, "statement")] == [Decimal("-450.00")]
    earlier = builder.client_report(anna_rub.owner.client_id, until=clock())
    assert len(rows(earlier, "transactions")) == 5
    assert len(rows(earlier, "statement")) == 4
    # the accounts are shown as they are now, whatever the period
    assert rows(earlier, "accounts")[0]["balance"] == Decimal("14000.00")


@pytest.mark.parametrize(
    ("period", "message"),
    [
        ({"since": datetime(2026, 9, 25), "until": datetime(2026, 9, 24)}, "since must be earlier than until"),
        ({"since": datetime(2026, 9, 24), "until": datetime(2026, 9, 24)}, "since must be earlier than until"),
        ({"since": "2026-09-24"}, "since must be a datetime"),
    ],
)
def test_client_report_refuses_a_wrong_period(client, builder, period, message):
    with pytest.raises(InvalidOperationError, match=message):
        builder.client_report(client.client_id, **period)


def test_client_report_of_an_unknown_client(builder):
    with pytest.raises(ClientNotFoundError):
        builder.client_report("ghost")


@pytest.mark.usefixtures("processed")
def test_bank_report_sums_up_the_bank(parties, builder):
    anna_rub, boris_usd, vera_rub = parties
    report = builder.bank_report()
    assert (report.kind, report.title) == (ReportKind.BANK, "Bank report")
    summary = report.section("summary").to_data()
    assert (summary["clients"], summary["open_accounts"], summary["total_balance"]) == (3, 3, Decimal("18950.00"))
    assert (summary["transactions"], summary["finished"], summary["failure_rate_percent"]) == (6, 5, Decimal("40.0"))
    assert (summary["blocked_by_risk"], summary["tariff_fees"]) == (1, Decimal("50.00"))
    assert (summary["largest_amount"], summary["largest_currency"]) == (Decimal("50.00"), Currency.USD)
    assert rows(report, "balance_by_currency") == [
        {"currency": Currency.RUB, "amount": Decimal("14450.00"), "amount_in_base": Decimal("14450.00")},
        {"currency": Currency.USD, "amount": Decimal("50.00"), "amount_in_base": Decimal("4500.00")},
    ]
    assert rows(report, "accounts_by_type") == [
        {"account_type": "BankAccount", "active": 2, "frozen": 1, "closed": 0, "value_in_base": Decimal("18950.00")}
    ]
    assert rows(report, "transactions_by_status") == [
        {"status": TransactionStatus.COMPLETED, "count": 3},
        {"status": TransactionStatus.FAILED, "count": 2},
        {"status": TransactionStatus.CANCELLED, "count": 1},
    ]
    assert {row["transaction_type"]: row["count"] for row in rows(report, "transactions_by_type")} == {
        TransactionType.DEPOSIT: 1,
        TransactionType.TRANSFER: 3,
        TransactionType.EXTERNAL_TRANSFER: 1,
    }
    assert [(row["place"], row["client_id"]) for row in rows(report, "top_clients")] == [
        (1, anna_rub.owner.client_id),
        (2, boris_usd.owner.client_id),
        (3, vera_rub.owner.client_id),
    ]


@pytest.mark.usefixtures("processed")
def test_bank_report_top_size_is_a_parameter(builder):
    report = builder.bank_report(top=1)
    assert report.section("top_clients").title == "Top 1 clients"
    assert len(rows(report, "top_clients")) == 1


def test_bank_report_of_an_empty_bank(builder):
    report = builder.bank_report()
    summary = report.section("summary").to_data()
    assert (summary["clients"], summary["total_balance"], summary["largest_amount"]) == (0, Decimal("0.00"), None)
    assert rows(report, "accounts_by_type") == []
    assert rows(report, "top_clients") == []


@pytest.mark.usefixtures("processed")
def test_risk_report_shows_levels_suspicious_operations_and_clients(bank, parties, builder):
    anna_rub, boris_usd, vera_rub = parties
    report = builder.risk_report()
    assert (report.kind, report.title) == (ReportKind.RISK, "Risk report")
    summary = report.section("summary").to_data()
    assert summary["min_level"] is RiskLevel.MEDIUM
    assert (summary["assessed"], summary["suspicious"], summary["blocked_by_risk"]) == (4, 1, 1)
    assert (summary["completed"], summary["final_failures"]) == (3, 2)
    assert summary["security_events"] == len(bank.audit_log.filter(category=AuditCategory.SECURITY))
    assert rows(report, "assessments_by_level") == [
        {"level": RiskLevel.LOW, "count": 3},
        {"level": RiskLevel.MEDIUM, "count": 0},
        {"level": RiskLevel.HIGH, "count": 1},
    ]
    (operation,) = rows(report, "suspicious_operations")
    assert (operation["client_id"], operation["action"], operation["score"]) == (
        anna_rub.owner.client_id,
        "blocked",
        90,
    )
    assert operation["factors"] == "large_amount, new_recipient"
    # the riskiest client first
    assert [(row["client_id"], row["level"]) for row in rows(report, "client_risk")] == [
        (anna_rub.owner.client_id, RiskLevel.HIGH),
        (boris_usd.owner.client_id, RiskLevel.LOW),
        (vera_rub.owner.client_id, RiskLevel.LOW),
    ]
    assert {row["error_type"]: row["count"] for row in rows(report, "errors_by_type")} == {
        "AccountFrozenError": 1,
        "RiskBlockedError": 1,
    }


@pytest.mark.usefixtures("processed")
def test_risk_report_minimum_level_chooses_the_suspicious_operations(builder):
    report = builder.risk_report(min_level="low")
    assert report.section("summary").to_data()["suspicious"] == 4
    assert report.section("suspicious_operations").title == "Suspicious operations (low risk and above)"


@pytest.mark.usefixtures("processed")
def test_report_charts_draw_the_report_data(parties, builder):
    anna_rub, boris_usd, vera_rub = parties
    client = builder.client_report(anna_rub.owner.client_id)
    assets, statuses, balance = client.charts
    anna = f"BankAccount RUB {anna_rub.account_id[:8]}"
    assert (assets.name, assets.labels, assets.values) == ("assets", (anna,), (Decimal("14450.00"),))
    assert (statuses.name, statuses.labels, statuses.values) == (
        "transactions_by_status",
        ("completed", "failed"),
        (3, 2),
    )
    assert (balance.name, dict(balance.series)) == (
        "balance",
        {anna: ((datetime(2026, 9, 24, 14), Decimal("14450.00")),)},
    )

    bank = builder.bank_report()
    assert [chart.name for chart in bank.charts] == [
        "balance_by_currency",
        "transactions_by_type",
        "top_clients",
        "total_balance",
    ]
    by_currency, _, top_clients, _ = bank.charts
    assert (by_currency.labels, by_currency.values) == (("RUB", "USD"), (Decimal("14450.00"), Decimal("4500.00")))
    assert top_clients.labels == tuple(client.full_name for client in (anna_rub.owner, boris_usd.owner, vera_rub.owner))

    risk = builder.risk_report()
    assert [chart.name for chart in risk.charts] == ["assessments_by_level", "risk_factors", "errors_by_type"]
    assert rows(risk, "risk_factors") == [
        {"factor": "new_recipient", "count": 3},
        {"factor": "large_amount", "count": 1},
    ]


@pytest.mark.usefixtures("processed")
def test_balance_history_starts_at_the_period_and_reaches_its_end(bank, clock, parties, builder):
    anna_rub, _, _ = parties
    clock.moment = datetime(2026, 9, 24, 15)
    bank.withdraw(anna_rub.account_id, 450)
    clock.moment = datetime(2026, 9, 24, 16)
    since = datetime(2026, 9, 24, 14, 30)

    (balance,) = builder.client_report(anna_rub.owner.client_id, since=since).charts[2].series.values()
    # the balance at the start of the period, every change in it, and the last balance held until now
    assert balance == (
        (since, Decimal("14450.00")),
        (datetime(2026, 9, 24, 15), Decimal("14000.00")),
        (datetime(2026, 9, 24, 16), Decimal("14000.00")),
    )
    history = [tuple(row.values()) for row in rows(builder.bank_report(since=since), "balance_history")]
    assert history == [
        (since, Decimal("18950.00")),  # Anna's 14 450 RUB and Boris's 50 USD
        (datetime(2026, 9, 24, 15), Decimal("18500.00")),
        (datetime(2026, 9, 24, 16), Decimal("18500.00")),
    ]
    # the movements of one moment make one point; the period ends at until, not now
    report = builder.bank_report(until=datetime(2026, 9, 24, 15))
    assert [tuple(row.values()) for row in rows(report, "balance_history")] == [
        (datetime(2026, 9, 24, 14), Decimal("18950.00")),
        (datetime(2026, 9, 24, 15), Decimal("18950.00")),
    ]
    assert report.charts[-1].series == {"Total": tuple(tuple(row.values()) for row in rows(report, "balance_history"))}


def test_risk_report_refuses_an_unknown_level(builder):
    with pytest.raises(InvalidOperationError, match="Unsupported risk level"):
        builder.risk_report(min_level="extreme")


@pytest.mark.usefixtures("processed")
def test_to_text_prints_the_report(builder):
    text = builder.to_text(builder.bank_report())
    assert text.startswith("Bank report\nGenerated 2026-09-24 14:00 by the bank's clock, amounts in RUB\n")
    assert "Top 3 clients" in text


@pytest.mark.usefixtures("processed")
def test_exports_are_named_by_the_call_time_and_the_report_kind(builder):
    report = builder.bank_report()
    json_path = builder.export_to_json(report)
    csv_paths = builder.export_to_csv(report)
    text_path = builder.export_to_text(report)

    assert builder.output_dir.is_dir()  # created on the first save
    assert json_path == builder.output_dir / "2026-09-26_10-00-05_bank.json"
    assert text_path == builder.output_dir / "2026-09-26_10-00-05_bank.txt"
    # one CSV file per section, in the order of the sections, under the same name as the JSON file
    assert [path.name for path in csv_paths] == [
        f"2026-09-26_10-00-05_bank_{section.name}.csv" for section in report.sections
    ]
    assert json.loads(json_path.read_text(encoding="utf-8"))["sections"]["summary"]["total_balance"] == "18950.00"
    top_clients = builder.output_dir / "2026-09-26_10-00-05_bank_top_clients.csv"
    with top_clients.open(encoding="utf-8", newline="") as file:
        assert [row["place"] for row in csv.DictReader(file)] == ["1", "2", "3"]
    assert text_path.read_text(encoding="utf-8") == builder.to_text(report) + "\n"


def test_a_second_export_within_the_same_second_does_not_overwrite(builder):
    report = builder.bank_report()
    first, second = builder.export_to_json(report), builder.export_to_json(report)
    assert (first.name, second.name) == ("2026-09-26_10-00-05_bank.json", "2026-09-26_10-00-05_bank-2.json")
    csv_second = builder.export_to_csv(report)[0]
    assert csv_second.name == "2026-09-26_10-00-05_bank_summary.csv"  # the CSV files are not taken yet
    assert builder.export_to_csv(report)[0].name == "2026-09-26_10-00-05_bank-2_summary.csv"


def test_a_name_taken_while_writing_moves_the_whole_call_to_the_next_name(builder, monkeypatch):
    report = builder.bank_report()
    builder.output_dir.mkdir(parents=True)
    # another process takes the second file after the names were checked
    taken = builder.output_dir / "2026-09-26_10-00-05_bank_balance_by_currency.csv"
    taken.write_text("theirs", encoding="utf-8")
    monkeypatch.setattr(Path, "exists", lambda path: False)
    paths = builder.export_to_csv(report)
    monkeypatch.undo()
    assert [path.name for path in paths] == [
        f"2026-09-26_10-00-05_bank-2_{section.name}.csv" for section in report.sections
    ]
    # the first file of the failed attempt is removed, the other process's file is untouched
    assert sorted(path.name for path in builder.output_dir.iterdir()) == sorted([taken.name, *(p.name for p in paths)])
    assert taken.read_text(encoding="utf-8") == "theirs"


def test_export_needs_a_report(builder):
    with pytest.raises(InvalidOperationError, match="report must be a Report"):
        builder.export_to_json({"kind": "bank"})


@pytest.mark.usefixtures("processed")
def test_save_charts_writes_a_png_per_chart(builder):
    report = builder.bank_report()
    paths = builder.save_charts(report)
    assert [path.name for path in paths] == [f"2026-09-26_10-00-05_bank_{chart.name}.png" for chart in report.charts]
    assert all(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for path in paths)


def test_save_charts_skips_a_chart_with_nothing_to_draw(bank, client, builder):
    bank.open_account(client.client_id, currency="RUB", initial_balance=500)
    paths = builder.save_charts(builder.client_report(client.client_id))
    # no transactions yet: the chart of their statuses is not saved
    assert [path.name for path in paths] == [
        "2026-09-26_10-00-05_client_assets.png",
        "2026-09-26_10-00-05_client_balance.png",
    ]


def test_save_charts_of_an_empty_bank_writes_nothing(builder):
    assert builder.save_charts(builder.bank_report()) == []
    assert not builder.output_dir.exists()
