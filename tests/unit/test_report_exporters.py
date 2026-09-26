import csv
import io
import json
from datetime import date, datetime
from decimal import Decimal

import pytest

from models import Currency
from reporting import CsvExporter, JsonExporter, KeyValueSection, Report, ReportKind, TableSection, TextExporter, plain
from services import AuditLevel, RiskLevel

CLIENT_ID = "0805d0c3-9f1e-4b7a-8a53-2f1c5d7e9b10"
ACCOUNT_ID = "bc28e87f-1d2c-4e3f-9a8b-7c6d5e4f3a21"


@pytest.fixture
def report() -> Report:
    return Report(
        ReportKind.CLIENT,
        "Client report: Sokolov Oleg",
        datetime(2026, 9, 25, 8, 0),
        Currency.RUB,
        (
            KeyValueSection(
                "summary",
                "Summary",
                {"client_id": CLIENT_ID, "total_value": Decimal("1464010.00"), "period_end": None},
            ),
            TableSection(
                "accounts",
                "Accounts",
                ("account_id", "currency", "balance", "level", "opened_at"),
                [
                    (ACCOUNT_ID, Currency.USD, Decimal("-1511.00"), RiskLevel.HIGH, datetime(2026, 9, 1, 10)),
                    ("DE89-3704", Currency.EUR, Decimal("16000.00"), RiskLevel.LOW, datetime(2026, 9, 24, 9)),
                ],
            ),
            TableSection("errors", "Errors", ("error_type", "count"), []),
        ),
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("150000.00"), "150000.00"),
        (Decimal("0.10"), "0.10"),
        (datetime(2026, 9, 24, 9, 30), "2026-09-24T09:30:00"),
        (date(2026, 9, 24), "2026-09-24"),
        (Currency.RUB, "RUB"),
        (RiskLevel.HIGH, "high"),
        (AuditLevel.WARNING, "warning"),
        (7, 7),
        (True, True),
        (None, None),
        ("text", "text"),
    ],
)
def test_plain_keeps_values_exact_and_readable(value, expected):
    result = plain(value)
    assert result == expected
    assert type(result) is type(expected)  # an IntEnum member is not left as an int


def test_text_lays_out_the_report_for_people(report):
    text = TextExporter().to_text(report)
    assert text.splitlines() == [
        "Client report: Sokolov Oleg",
        "Generated 2026-09-25 08:00 by the bank's clock, amounts in RUB",
        "",
        "Summary",
        "  client_id    0805d0c3",
        "  total_value  1464010.00",
        "  period_end   -",
        "",
        "Accounts",
        "  account_id  currency   balance  level  opened_at",
        "  bc28e87f    USD       -1511.00  high   2026-09-01 10:00",
        "  DE89-3704   EUR       16000.00  low    2026-09-24 09:00",
        "",
        "Errors",
        "  (none)",
    ]


def test_json_holds_every_section_by_name(report):
    files = JsonExporter().render(report)
    assert list(files) == [""]
    data = json.loads(files[""])
    assert data == {
        "kind": "client",
        "title": "Client report: Sokolov Oleg",
        "generated_at": "2026-09-25T08:00:00",
        "currency": "RUB",
        "sections": {
            "summary": {"client_id": CLIENT_ID, "total_value": "1464010.00", "period_end": None},
            "accounts": [
                {
                    "account_id": ACCOUNT_ID,
                    "currency": "USD",
                    "balance": "-1511.00",
                    "level": "high",
                    "opened_at": "2026-09-01T10:00:00",
                },
                {
                    "account_id": "DE89-3704",
                    "currency": "EUR",
                    "balance": "16000.00",
                    "level": "low",
                    "opened_at": "2026-09-24T09:00:00",
                },
            ],
            "errors": [],
        },
    }


def test_csv_gives_one_table_per_section(report):
    files = CsvExporter().render(report)
    assert list(files) == ["_summary", "_accounts", "_errors"]
    summary = list(csv.reader(io.StringIO(files["_summary"])))
    assert summary == [["key", "value"], ["client_id", CLIENT_ID], ["total_value", "1464010.00"], ["period_end", ""]]
    accounts = list(csv.DictReader(io.StringIO(files["_accounts"])))
    assert accounts[0] == {
        "account_id": ACCOUNT_ID,
        "currency": "USD",
        "balance": "-1511.00",
        "level": "high",
        "opened_at": "2026-09-01T10:00:00",
    }
    assert files["_errors"] == "error_type,count\r\n"  # an empty table keeps its header


def test_csv_keeps_text_that_looks_like_a_formula_as_text():
    table = TableSection(
        "clients",
        "Clients",
        ("full_name", "reason", "balance"),
        [
            ('=HYPERLINK("http://example.com","x")', "@SUM(A1)", Decimal("-1511.00")),
            ("+7 999", "-", Decimal("10.00")),
            ("Volkova Maria", "\tcmd", None),
        ],
    )
    report = Report(ReportKind.BANK, "Bank report", datetime(2026, 9, 25, 8, 0), Currency.RUB, (table,))
    rows = list(csv.reader(io.StringIO(CsvExporter().render(report)["_clients"])))
    assert rows[1:] == [
        ['\'=HYPERLINK("http://example.com","x")', "'@SUM(A1)", "-1511.00"],  # an overdraft stays a number
        ["'+7 999", "'-", "10.00"],
        ["Volkova Maria", "'\tcmd", ""],
    ]
