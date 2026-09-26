"""The report model: what a report says, independent of the format it is written in.

A ``Report`` is a titled list of sections. A section is either a set of
named values (``KeyValueSection``) or a table (``TableSection``); both can
be seen as a table - ``columns`` and ``rows`` - which is all a tabular
format needs. Values keep their domain types (``Decimal``, ``datetime``,
enums) until an exporter turns them into text.
"""

import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import ClassVar

from exceptions import InvalidOperationError
from models import Currency


class ReportKind(Enum):
    """What a report is about; also the type part of its file names."""

    CLIENT = "client"
    BANK = "bank"
    RISK = "risk"


def _check_name(name: object, field: str) -> None:
    # names become JSON keys and parts of file names, so they stay plain
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise InvalidOperationError(f"{field} must be lowercase letters, digits and underscores; got {name!r}.")


@dataclass(frozen=True)
class Section(ABC):
    """A named part of a report; ``name`` is its key in JSON and in file names, ``title`` is for readers.

    Every section can be read as a table: ``columns`` and ``rows``, one
    value per column in each row. ``show_header`` tells a reader whether the
    column names are worth printing.
    """

    name: str
    title: str

    show_header: ClassVar[bool] = True

    def __post_init__(self) -> None:
        _check_name(self.name, "section name")

    @abstractmethod
    def to_data(self) -> object:
        """The section as plain containers: a dict of values or a list of rows as dicts."""


@dataclass(frozen=True)
class KeyValueSection(Section):
    """Named values, such as the totals of a report; as a table, one ``key``-``value`` row per item."""

    items: Mapping[str, object]

    show_header: ClassVar[bool] = False

    def __post_init__(self) -> None:
        super().__post_init__()
        for key in self.items:
            _check_name(key, "item name")
        object.__setattr__(self, "items", MappingProxyType(dict(self.items)))

    @property
    def columns(self) -> tuple[str, ...]:
        return ("key", "value")

    @property
    def rows(self) -> tuple[tuple[object, ...], ...]:
        return tuple(self.items.items())

    def to_data(self) -> dict[str, object]:
        return dict(self.items)


@dataclass(frozen=True)
class TableSection(Section):
    """Rows of values under named columns."""

    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "columns", tuple(self.columns))
        object.__setattr__(self, "rows", tuple(tuple(row) for row in self.rows))
        if not self.columns:
            raise InvalidOperationError(f"Table {self.name!r} needs at least one column.")
        for column in self.columns:
            _check_name(column, "column name")
        for row in self.rows:
            if len(row) != len(self.columns):
                raise InvalidOperationError(
                    f"Table {self.name!r} has {len(self.columns)} columns, a row has {len(row)} values: {row!r}."
                )

    def to_data(self) -> list[dict[str, object]]:
        return [dict(zip(self.columns, row, strict=True)) for row in self.rows]


@dataclass(frozen=True)
class Report:
    """A finished report: what it is about, when the data was taken and its sections, in order.

    ``generated_at`` is the bank's time the data was read at; ``currency``
    is the currency of every converted amount (``*_in_base`` values and
    totals).
    """

    kind: ReportKind
    title: str
    generated_at: datetime
    currency: Currency
    sections: tuple[Section, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "sections", tuple(self.sections))
        names = [section.name for section in self.sections]
        if len(names) != len(set(names)):
            raise InvalidOperationError(f"Section names of a report must be unique; got {names}.")

    def section(self, name: str) -> Section:
        for section in self.sections:
            if section.name == name:
                return section
        raise KeyError(name)
