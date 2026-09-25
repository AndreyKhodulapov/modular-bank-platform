"""Application logging: readable lines in the terminal, JSON Lines in a file.

The services only create loggers under the ``bank`` namespace
(``bank.audit``, ``bank.transactions``, ``bank.queue``) and never configure
them; ``configure_logging()`` is called once by the program that runs them.

Structured fields travel in one ``extra`` key, ``fields``, so they cannot
clash with the attributes of ``logging.LogRecord``::

    logger.debug("attempt started", extra={"fields": {"transaction_id": tid, "attempt": 2}})

Every record carries two moments: ``logged_at`` - when it was written, by
the wall clock - and, when the caller knows it, ``event_time`` - when it
happened by the bank's clock, which the demo and the tests set by hand.
"""

import json
import logging
import sys
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, TextIO

ROOT_LOGGER = "bank"
_HANDLER_NAMES = frozenset({"bank.console", "bank.file"})


def _fields(record: logging.LogRecord) -> dict[str, Any]:
    fields = getattr(record, "fields", None)
    return dict(fields) if isinstance(fields, dict) else {}


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return str(value)  # Decimal and anything else: its text, never a float


class JsonFormatter(logging.Formatter):
    """One JSON object per record: the standard keys first, then the record's fields.

    A field named like a standard key (``level``, ``message``, ...) is
    dropped rather than allowed to overwrite it.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "logged_at": datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update((key, value) for key, value in _fields(record).items() if key not in payload)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=_json_default)


class ConsoleFormatter(logging.Formatter):
    """A line for people: ``time level logger event: message key=value ...``.

    The time is the event's own moment when the record has one. Empty
    fields are skipped, ``details`` are unfolded into their own pairs and
    ids are cut to 8 characters, as in the audit log's ``str()``; the JSON
    file keeps everything in full.
    """

    ID_LENGTH = 8

    def format(self, record: logging.LogRecord) -> str:
        fields = _fields(record)
        moment = fields.pop("event_time", None)
        if not isinstance(moment, datetime):
            moment = datetime.fromtimestamp(record.created)
        event = fields.pop("event", None)
        fields.update(fields.pop("details", None) or {})

        message = record.getMessage()
        text = f"{event}: {message}" if event else message
        pairs = " ".join(
            f"{key}={self._compact(key, value)}" for key, value in fields.items() if value not in (None, "", [], ())
        )
        line = f"{moment:%m-%d %H:%M:%S} {record.levelname:<8} {record.name:<17} {text}"
        if pairs:
            line = f"{line} {pairs}"
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line

    def _compact(self, key: str, value: object) -> str:
        if key.endswith("_id") and isinstance(value, str):
            return value[: self.ID_LENGTH]
        if isinstance(value, list | tuple):
            return ",".join(str(item) for item in value)
        if isinstance(value, datetime):
            return f"{value:%m-%d %H:%M}"
        return str(_json_default(value))


def configure_logging(
    *,
    console_level: int = logging.WARNING,
    file_path: str | Path | None = None,
    file_level: int = logging.DEBUG,
    stream: TextIO | None = None,
) -> None:
    """Send the ``bank`` loggers to the terminal and, when ``file_path`` is given, to a JSON Lines file.

    The console goes to ``stream`` (standard output by default, so log lines
    stay in order with the program's own output). Calling it again replaces
    the handlers it added before instead of adding more.
    """
    logger = logging.getLogger(ROOT_LOGGER)
    for handler in [handler for handler in logger.handlers if handler.name in _HANDLER_NAMES]:
        logger.removeHandler(handler)
        handler.close()

    console = logging.StreamHandler(stream if stream is not None else sys.stdout)
    console.name = "bank.console"
    console.setLevel(console_level)
    console.setFormatter(ConsoleFormatter())
    logger.addHandler(console)
    lowest = console_level

    if file_path is not None:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.name = "bank.file"
        file_handler.setLevel(file_level)
        file_handler.setFormatter(JsonFormatter())
        logger.addHandler(file_handler)
        lowest = min(lowest, file_level)

    # the logger lets through what at least one handler wants; each handler filters the rest
    logger.setLevel(lowest)
