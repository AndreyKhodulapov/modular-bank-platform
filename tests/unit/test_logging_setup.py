import io
import json
import logging
import sys
from datetime import datetime

import pytest

from logging_setup import ROOT_LOGGER, ConsoleFormatter, JsonFormatter, configure_logging

EVENT_TIME = datetime(2026, 9, 25, 2, 30)


def make_record(message: str = "attempt started", level: int = logging.INFO, fields: dict | None = None):
    record = logging.LogRecord("bank.test", level, __file__, 1, message, None, None)
    record.fields = fields or {}
    return record


@pytest.fixture
def bank_logger():
    """The ``bank`` logger, with whatever ``configure_logging()`` added removed afterwards."""
    logger = logging.getLogger(ROOT_LOGGER)
    handlers, level, propagate = list(logger.handlers), logger.level, logger.propagate
    yield logger
    for handler in [handler for handler in logger.handlers if handler not in handlers]:
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(level)
    logger.propagate = propagate


def test_json_line_has_the_standard_keys_and_the_fields():
    fields = {"event_time": EVENT_TIME, "transaction_id": "T-1", "details": {"attempt": 2}, "level": "ignored"}
    record = make_record(fields=fields)
    payload = json.loads(JsonFormatter().format(record))
    assert list(payload)[:4] == ["logged_at", "level", "logger", "message"]
    assert datetime.fromisoformat(payload["logged_at"]).tzinfo is not None
    # the field named "level" is dropped: a field cannot overwrite a standard key
    assert (payload["level"], payload["logger"], payload["message"]) == ("INFO", "bank.test", "attempt started")
    assert payload["event_time"] == "2026-09-25T02:30:00"
    assert (payload["transaction_id"], payload["details"]) == ("T-1", {"attempt": 2})


def test_json_line_carries_the_exception():
    try:
        raise OSError("disk full")
    except OSError:
        record = logging.LogRecord("bank.test", logging.ERROR, __file__, 1, "write failed", None, sys.exc_info())
    payload = json.loads(JsonFormatter().format(record))
    assert "OSError: disk full" in payload["exception"]


def test_console_line_uses_the_event_time_and_compacts_fields():
    record = make_record(
        "failed login attempt 1 of 3",
        logging.WARNING,
        {
            "event_time": EVENT_TIME,
            "event": "failed_login",
            "client_id": "0123456789abcdef",
            "account_id": None,
            "details": {"factors": ["large_amount", "night_operation"], "will_retry": False},
        },
    )
    line = ConsoleFormatter().format(record)
    assert line.startswith("09-25 02:30:00 WARNING  bank.test")
    assert "failed_login: failed login attempt 1 of 3" in line
    assert "client_id=01234567 " in line
    assert "account_id" not in line
    assert line.endswith("factors=large_amount,night_operation will_retry=False")


def test_console_line_keeps_details_that_are_not_a_mapping():
    record = make_record(fields={"details": "free text"})
    assert ConsoleFormatter().format(record).endswith("attempt started details=free text")


def test_console_line_without_fields_is_just_the_message():
    record = make_record("delayed transaction is due", logging.DEBUG)
    assert ConsoleFormatter().format(record).endswith("DEBUG    bank.test         delayed transaction is due")


def test_console_and_file_get_their_own_levels(tmp_path, bank_logger):
    console = io.StringIO()
    path = tmp_path / "logs" / "app.jsonl"  # the folder is created on demand
    configure_logging(console_level=logging.WARNING, file_path=path, stream=console)
    logger = logging.getLogger("bank.test")
    logger.debug("trace", extra={"fields": {"attempt": 1}})
    logger.warning("unusual")

    assert console.getvalue().count("\n") == 1 and "unusual" in console.getvalue()
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [(line["level"], line["message"]) for line in lines] == [("DEBUG", "trace"), ("WARNING", "unusual")]
    assert lines[0]["attempt"] == 1


def test_configuring_again_replaces_the_handlers(tmp_path, bank_logger):
    first, second = io.StringIO(), io.StringIO()
    configure_logging(console_level=logging.INFO, stream=first)
    configure_logging(console_level=logging.INFO, stream=second)
    logging.getLogger("bank.test").info("once")
    assert (first.getvalue(), second.getvalue().count("once")) == ("", 1)
    assert not [handler for handler in bank_logger.handlers if isinstance(handler, logging.FileHandler)]


def test_records_do_not_reach_the_root_logger_twice(bank_logger):
    root_output = io.StringIO()
    root_handler = logging.StreamHandler(root_output)
    logging.getLogger().addHandler(root_handler)  # as if the host program called logging.basicConfig()
    try:
        console = io.StringIO()
        configure_logging(console_level=logging.INFO, stream=console)
        logging.getLogger("bank.test").warning("once")
    finally:
        logging.getLogger().removeHandler(root_handler)
    assert (console.getvalue().count("once"), root_output.getvalue()) == (1, "")
