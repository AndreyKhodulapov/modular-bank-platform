"""Audit log: one append-only journal of security, transaction and risk events."""

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum, IntEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from exceptions import InvalidOperationError
from utils import to_enum

type DetailValue = str | int | float | bool | None | tuple[DetailValue, ...]


class AuditLevel(IntEnum):
    """Severity of an audit event; the values match the standard ``logging`` levels.

    An ``IntEnum`` compares by value, so ``level >= AuditLevel.WARNING``
    selects everything from warnings up.
    """

    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50


class AuditCategory(Enum):
    """Which part of the bank produced an audit event."""

    SECURITY = "security"
    TRANSACTION = "transaction"
    RISK = "risk"


def _plain(value: object) -> DetailValue:
    """Turn a detail value into a JSON-friendly one, so the file keeps exactly what memory keeps."""
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return _plain(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return tuple(_plain(item) for item in value)
    raise InvalidOperationError(f"Unsupported audit detail value of type {type(value).__name__}.")


@dataclass(frozen=True)
class AuditEvent:
    """One entry of the audit log; immutable once recorded.

    ``event`` names what happened (``failed_login``, ``transaction_failed``,
    ``operation_blocked``, ...); ``details`` holds structured extras such as
    an amount, an error type or a risk score.
    """

    timestamp: datetime
    level: AuditLevel
    category: AuditCategory
    event: str
    message: str
    client_id: str | None = None
    account_id: str | None = None
    transaction_id: str | None = None
    details: Mapping[str, DetailValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # a read-only view keeps the frozen event really unchangeable
        plain = {str(key): _plain(value) for key, value in self.details.items()}
        object.__setattr__(self, "details", MappingProxyType(plain))

    def __hash__(self) -> int:
        # the generated hash would fail on the mapping; its sorted items are hashable and equal events stay equal
        return hash(
            (
                self.timestamp,
                self.level,
                self.category,
                self.event,
                self.message,
                self.client_id,
                self.account_id,
                self.transaction_id,
                tuple(sorted(self.details.items())),
            )
        )

    def to_dict(self) -> dict[str, Any]:
        def jsonable(value: DetailValue) -> Any:
            return [jsonable(item) for item in value] if isinstance(value, tuple) else value

        return {
            "timestamp": self.timestamp.isoformat(),
            "level": self.level.name,
            "category": self.category.value,
            "event": self.event,
            "message": self.message,
            "client_id": self.client_id,
            "account_id": self.account_id,
            "transaction_id": self.transaction_id,
            "details": {key: jsonable(value) for key, value in self.details.items()},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AuditEvent":
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            level=AuditLevel[data["level"]],
            category=AuditCategory(data["category"]),
            event=data["event"],
            message=data["message"],
            client_id=data.get("client_id"),
            account_id=data.get("account_id"),
            transaction_id=data.get("transaction_id"),
            details=data.get("details", {}),
        )

    def __str__(self) -> str:
        subject = (self.client_id or "-")[:8]
        return (
            f"{self.timestamp:%m-%d %H:%M} {self.level.name:<8} {self.category.value:<11} "
            f"{self.event:<26} {subject:<8} {self.message}"
        )


class AuditLog:
    """Keeps audit events in memory and, optionally, appends them to a file.

    The file is in JSON Lines format: one JSON object per line. Each event
    is appended as soon as it is recorded, so nothing is lost if the
    process stops, and the file is never rewritten. Memory holds the events
    of this run; ``load()`` reads a whole file back. A failed file write is
    not hidden: the error reaches the caller and the event is not kept.
    """

    def __init__(self, file_path: str | Path | None = None) -> None:
        self._events: list[AuditEvent] = []
        self._file_path = Path(file_path) if file_path is not None else None
        if self._file_path is not None:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def file_path(self) -> Path | None:
        return self._file_path

    @property
    def events(self) -> list[AuditEvent]:
        """A copy of the events in the order they were recorded."""
        return list(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self) -> Iterator[AuditEvent]:
        return iter(self.events)

    def record(
        self,
        level: AuditLevel | str,
        category: AuditCategory | str,
        event: str,
        message: str,
        *,
        timestamp: datetime,
        client_id: str | None = None,
        account_id: str | None = None,
        transaction_id: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> AuditEvent:
        if not isinstance(timestamp, datetime):
            raise InvalidOperationError("timestamp must be a datetime.")
        if not isinstance(event, str) or not event.strip():
            raise InvalidOperationError("event must be a non-empty string.")
        entry = AuditEvent(
            timestamp=timestamp,
            level=to_enum(AuditLevel, level, field="audit level"),
            category=to_enum(AuditCategory, category, field="audit category"),
            event=event.strip(),
            message=str(message),
            client_id=client_id,
            account_id=account_id,
            transaction_id=transaction_id,
            details=details or {},
        )
        # the file first: if the write fails, memory does not get an event the file lacks
        if self._file_path is not None:
            with self._file_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        self._events.append(entry)
        return entry

    def filter(
        self,
        *,
        min_level: AuditLevel | str | None = None,
        level: AuditLevel | str | None = None,
        category: AuditCategory | str | None = None,
        event: str | None = None,
        client_id: str | None = None,
        account_id: str | None = None,
        transaction_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[AuditEvent]:
        """Return the events matching every given condition, in order.

        ``level`` matches one level exactly, ``min_level`` that level and
        above. ``since`` is inclusive, ``until`` exclusive.
        """
        lowest = to_enum(AuditLevel, min_level, field="audit level") if min_level is not None else None
        exact = to_enum(AuditLevel, level, field="audit level") if level is not None else None
        kind = to_enum(AuditCategory, category, field="audit category") if category is not None else None
        return [
            entry
            for entry in self._events
            if (lowest is None or entry.level >= lowest)
            and (exact is None or entry.level is exact)
            and (kind is None or entry.category is kind)
            and (event is None or entry.event == event)
            and (client_id is None or entry.client_id == client_id)
            and (account_id is None or entry.account_id == account_id)
            and (transaction_id is None or entry.transaction_id == transaction_id)
            and (since is None or entry.timestamp >= since)
            and (until is None or entry.timestamp < until)
        ]

    @staticmethod
    def load(file_path: str | Path) -> list[AuditEvent]:
        """Read every event from a JSON Lines audit file, skipping blank lines."""
        with Path(file_path).open(encoding="utf-8") as file:
            return [AuditEvent.from_dict(json.loads(line)) for line in file if line.strip()]
