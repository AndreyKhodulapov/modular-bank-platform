from datetime import datetime
from decimal import Decimal

import pytest

from exceptions import InvalidOperationError, SectionNotFoundError
from models import Currency
from reporting import KeyValueSection, Report, ReportKind, TableSection


def make_report(*sections) -> Report:
    return Report(ReportKind.BANK, "Bank report", datetime(2026, 9, 24, 14), Currency.RUB, sections)


def test_key_value_section_reads_as_a_table_without_a_header():
    section = KeyValueSection("summary", "Summary", {"clients": 3, "total": Decimal("10.00")})
    assert section.to_table() == (("key", "value"), (("clients", 3), ("total", Decimal("10.00"))))
    assert section.show_header is False
    assert section.to_data() == {"clients": 3, "total": Decimal("10.00")}


def test_table_section_keeps_rows_under_its_columns():
    section = TableSection("top_clients", "Top clients", ["place", "name"], [[1, "Anna"], (2, "Boris")])
    assert section.columns == ("place", "name")
    assert section.rows == ((1, "Anna"), (2, "Boris"))
    assert section.to_table() == (section.columns, section.rows)
    assert section.show_header is True
    assert section.to_data() == [{"place": 1, "name": "Anna"}, {"place": 2, "name": "Boris"}]


def test_sections_cannot_be_changed():
    summary = KeyValueSection("summary", "Summary", {"clients": 3})
    with pytest.raises(TypeError):
        summary.items["clients"] = 4
    table = TableSection("rows", "Rows", ("value",), [[1]])
    assert isinstance(table.rows, tuple)
    assert isinstance(table.rows[0], tuple)


def test_table_row_must_have_a_value_per_column():
    with pytest.raises(InvalidOperationError, match="2 columns, a row has 1 values"):
        TableSection("rows", "Rows", ("a", "b"), [(1,)])


def test_table_needs_a_column():
    with pytest.raises(InvalidOperationError, match="at least one column"):
        TableSection("rows", "Rows", (), [])


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: KeyValueSection("Top clients", "Top", {}), id="section name"),
        pytest.param(lambda: KeyValueSection("summary", "Summary", {"total balance": 1}), id="item name"),
        pytest.param(lambda: TableSection("rows", "Rows", ("Amount",), []), id="column name"),
        pytest.param(lambda: TableSection("1rows", "Rows", ("a",), []), id="leading digit"),
    ],
)
def test_names_must_be_plain_because_they_go_into_files(build):
    with pytest.raises(InvalidOperationError, match="lowercase letters, digits and underscores"):
        build()


def test_report_finds_a_section_by_name():
    summary = KeyValueSection("summary", "Summary", {})
    report = make_report(summary, TableSection("rows", "Rows", ("a",), []))
    assert report.section("summary") is summary
    with pytest.raises(SectionNotFoundError, match="Section missing not found"):
        report.section("missing")


def test_report_section_names_are_unique():
    with pytest.raises(InvalidOperationError, match="must be unique"):
        make_report(KeyValueSection("summary", "One", {}), KeyValueSection("summary", "Two", {}))
