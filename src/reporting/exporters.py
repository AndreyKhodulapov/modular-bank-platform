"""Report formats: each exporter turns a ``Report`` into the files of one format.

An exporter only knows the report model, never a particular report, so a
new report needs no change here and a new format needs no change in the
reports (Strategy). Where the files go and how they are named is decided by
``ReportBuilder``.
"""

import csv
import io
import json
import re
from abc import ABC, abstractmethod
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import ClassVar

from reporting.report import Report, Section


def plain(value: object) -> object:
    """A value as JSON can hold it without losing anything.

    ``Decimal`` becomes its exact text (``"150000.00"``), never a binary
    float; dates become ISO 8601; an enum becomes its value, or its name in
    lower case when the value is a number (a risk level, an audit level).
    """
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, Enum):  # before int: IntEnum members are ints too
        return value.value if isinstance(value.value, str) else value.name.lower()
    if isinstance(value, int):
        return value
    if isinstance(value, date):  # datetime is a date too
        return value.isoformat()
    return str(value)


def _plain_tree(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _plain_tree(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_tree(item) for item in value]
    return plain(value)


def _is_number(value: object) -> bool:
    """A number to align to the right, or a missing value; ``bool`` and ``IntEnum`` members are ints but not numbers."""
    return value is None or (isinstance(value, int | Decimal) and not isinstance(value, bool | Enum))


class ReportExporter(ABC):
    """Writes a report in one format."""

    extension: ClassVar[str]

    @abstractmethod
    def render(self, report: Report) -> dict[str, str]:
        """The files of the report: what each adds to the common file name (``""`` for one file) and its text."""


class TextExporter(ReportExporter):
    """A report for people: aligned tables, numbers on the right, identifiers shortened.

    A UUID is cut to its first 8 characters, as everywhere in the program's
    output; the JSON and CSV files keep it whole.
    """

    extension = ".txt"
    UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

    def render(self, report: Report) -> dict[str, str]:
        return {"": self.to_text(report) + "\n"}

    def to_text(self, report: Report) -> str:
        lines = [
            report.title,
            f"Generated {report.generated_at:%Y-%m-%d %H:%M} by the bank's clock, amounts in {report.currency.value}",
        ]
        for section in report.sections:
            lines.extend(["", section.title, *self._table(section)])
        return "\n".join(lines)

    def _cell(self, value: object) -> str:
        if value is None:
            return "-"
        if isinstance(value, datetime):
            return f"{value:%Y-%m-%d %H:%M}"
        text = str(plain(value))
        return text[:8] if self.UUID.fullmatch(text) else text

    def _table(self, section: Section) -> list[str]:
        if not section.rows:
            return ["  (none)"]
        body = [[self._cell(value) for value in row] for row in section.rows]
        # a column of numbers only (a missing value aside) is aligned to the right
        numeric = [all(_is_number(value) for value in column) for column in zip(*section.rows, strict=True)]
        lines = [list(section.columns), *body] if section.show_header else body
        widths = [max(len(line[index]) for line in lines) for index in range(len(section.columns))]
        return [
            "  "
            + "  ".join(
                cell.rjust(width) if right else cell.ljust(width)
                for cell, width, right in zip(line, widths, numeric, strict=True)
            ).rstrip()
            for line in lines
        ]


class JsonExporter(ReportExporter):
    """One JSON document: the report's header and its sections by name.

    A section of named values becomes an object, a table a list of objects,
    one per row.
    """

    extension = ".json"

    def render(self, report: Report) -> dict[str, str]:
        data = {
            "kind": report.kind,
            "title": report.title,
            "generated_at": report.generated_at,
            "currency": report.currency,
            "sections": {section.name: section.to_data() for section in report.sections},
        }
        return {"": json.dumps(_plain_tree(data), ensure_ascii=False, indent=2) + "\n"}


class CsvExporter(ReportExporter):
    """One CSV file per section, named after it: a CSV file holds a single table.

    The first line holds the column names (``key,value`` for a section of
    named values); values are written as ``plain()`` gives them, an empty
    field for a missing one.
    """

    extension = ".csv"

    def render(self, report: Report) -> dict[str, str]:
        files = {}
        for section in report.sections:
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(section.columns)
            writer.writerows([plain(value) for value in row] for row in section.rows)
            files[f"_{section.name}"] = buffer.getvalue()
        return files
