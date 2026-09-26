"""The report model: what a report says, independent of the format it is written in.

A ``Report`` is a titled list of sections. A section is either a set of
named values (``KeyValueSection``) or a table (``TableSection``); both can
be seen as a table - ``columns`` and ``rows`` - which is all a tabular
format needs. Values keep their domain types (``Decimal``, ``datetime``,
enums) until an exporter turns them into text.

A report also describes its charts as data (``PieChart``, ``BarChart``,
``LineChart``): what to draw, not how. Drawing is the job of
``ChartRenderer``, so the charts can be checked without drawing them.
"""

import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
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
class Chart(ABC):
    """A chart as data; ``name`` is its part of the file name, ``unit`` what its values are in."""

    name: str
    title: str
    unit: str

    def __post_init__(self) -> None:
        _check_name(self.name, "chart name")

    @property
    @abstractmethod
    def is_empty(self) -> bool:
        """Nothing to draw: an empty chart is not saved."""


@dataclass(frozen=True)
class CategoryChart(Chart):
    """One value per labelled category."""

    labels: tuple[str, ...]
    values: tuple[Decimal | int, ...]

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "labels", tuple(self.labels))
        object.__setattr__(self, "values", tuple(self.values))
        if len(self.labels) != len(self.values):
            raise InvalidOperationError(
                f"Chart {self.name!r} has {len(self.labels)} labels and {len(self.values)} values."
            )

    @property
    def is_empty(self) -> bool:
        return not self.labels


@dataclass(frozen=True)
class BarChart(CategoryChart):
    """Values side by side, in the given order."""


@dataclass(frozen=True)
class PieChart(CategoryChart):
    """Parts of a whole, largest first.

    Only a positive value is a part of a whole: a zero has no slice and a
    negative value (an overdraft) cannot be one, so both are left out; the
    negative ones are listed in ``left_out`` for the reader. Past
    ``MAX_SLICES`` the smallest parts fold into one ``Other`` slice, so
    every slice keeps a colour of its own.
    """

    MAX_SLICES: ClassVar[int] = 8

    @property
    def slices(self) -> tuple[tuple[str, Decimal | int], ...]:
        parts = sorted(
            ((label, value) for label, value in zip(self.labels, self.values, strict=True) if value > 0),
            key=lambda part: -part[1],
        )
        if len(parts) <= self.MAX_SLICES:
            return tuple(parts)
        kept = parts[: self.MAX_SLICES - 1]
        return (*kept, ("Other", sum(value for _, value in parts[self.MAX_SLICES - 1 :])))

    @property
    def left_out(self) -> tuple[tuple[str, Decimal | int], ...]:
        return tuple((label, value) for label, value in zip(self.labels, self.values, strict=True) if value < 0)

    @property
    def is_empty(self) -> bool:
        return not self.slices


@dataclass(frozen=True)
class LineChart(Chart):
    """Values over time, one line per series.

    Each point is ``(moment, value)`` in time order; a value holds until the
    next point (a balance does not change between operations), so the line
    is drawn in steps. At most ``MAX_SERIES`` lines, so every line keeps a
    colour of its own; a report with more folds the rest into one line.
    """

    MAX_SERIES: ClassVar[int] = 8

    series: Mapping[str, tuple[tuple[datetime, Decimal], ...]]

    def __post_init__(self) -> None:
        super().__post_init__()
        series = {label: tuple(points) for label, points in self.series.items()}
        if len(series) > self.MAX_SERIES:
            raise InvalidOperationError(f"Chart {self.name!r} has {len(series)} series, at most {self.MAX_SERIES}.")
        for label, points in series.items():
            moments = [moment for moment, _ in points]
            if moments != sorted(moments):
                raise InvalidOperationError(f"Points of {label!r} in chart {self.name!r} must be in time order.")
        object.__setattr__(self, "series", MappingProxyType(series))

    @property
    def is_empty(self) -> bool:
        return not any(self.series.values())


@dataclass(frozen=True)
class Report:
    """A finished report: what it is about, when the data was taken and its sections, in order.

    ``generated_at`` is the bank's time the data was read at; ``currency``
    is the currency of every converted amount (``*_in_base`` values and
    totals). ``charts`` draw the report's data; their names are unique too.
    """

    kind: ReportKind
    title: str
    generated_at: datetime
    currency: Currency
    sections: tuple[Section, ...]
    charts: tuple[Chart, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "sections", tuple(self.sections))
        object.__setattr__(self, "charts", tuple(self.charts))
        for kind, items in (("Section", self.sections), ("Chart", self.charts)):
            names = [item.name for item in items]
            if len(names) != len(set(names)):
                raise InvalidOperationError(f"{kind} names of a report must be unique; got {names}.")

    def section(self, name: str) -> Section:
        for section in self.sections:
            if section.name == name:
                return section
        raise KeyError(name)
