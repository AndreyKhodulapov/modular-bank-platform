import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from exceptions import InvalidOperationError
from models import Currency
from services import AuditCategory, AuditEvent, AuditLevel, AuditLog

NOW = datetime(2026, 9, 24, 14, 0)


@pytest.fixture
def log() -> AuditLog:
    return AuditLog()


@pytest.fixture
def filled(log) -> AuditLog:
    log.record("info", "transaction", "transaction_completed", "ok", timestamp=NOW, client_id="A", account_id="A1")
    log.record(
        AuditLevel.WARNING, AuditCategory.SECURITY, "failed_login", "wrong", timestamp=NOW + timedelta(minutes=1)
    )
    log.record(
        "error", "transaction", "transaction_failed", "no money", timestamp=NOW + timedelta(minutes=2), client_id="B"
    )
    log.record(
        "critical",
        "risk",
        "operation_blocked",
        "high risk",
        timestamp=NOW + timedelta(minutes=3),
        client_id="A",
        transaction_id="T1",
        details={"score": 90, "factors": ["large_amount", "night_operation"]},
    )
    return log


def test_levels_are_ordered_like_logging_levels():
    assert AuditLevel.INFO < AuditLevel.WARNING < AuditLevel.ERROR < AuditLevel.CRITICAL
    assert (AuditLevel.INFO.value, AuditLevel.CRITICAL.value) == (20, 50)


def test_record_returns_and_keeps_the_event(log):
    event = log.record(
        "warning",
        "security",
        "failed_login",
        "wrong password",
        timestamp=NOW,
        client_id="C",
        details={"attempt": 1},
    )
    assert log.events == [event]
    assert (event.level, event.category, event.event) == (AuditLevel.WARNING, AuditCategory.SECURITY, "failed_login")
    assert (event.timestamp, event.client_id, event.details["attempt"]) == (NOW, "C", 1)


def test_events_are_immutable_and_returned_as_a_copy(filled):
    event = filled.events[0]
    with pytest.raises(AttributeError):
        event.level = AuditLevel.CRITICAL
    with pytest.raises(TypeError):
        event.details["x"] = 1
    filled.events.clear()
    assert len(filled) == 4


def test_details_are_stored_as_plain_values(log):
    event = log.record(
        "info",
        "transaction",
        "transaction_completed",
        "ok",
        timestamp=NOW,
        details={"amount": Decimal("10.50"), "currency": Currency.USD, "at": NOW, "factors": ["a", "b"]},
    )
    assert dict(event.details) == {"amount": "10.50", "currency": "USD", "at": NOW.isoformat(), "factors": ("a", "b")}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"level": "debug"},
        {"category": "marketing"},
        {"event": " "},
        {"timestamp": "2026-09-24"},
        {"details": {"obj": object()}},
    ],
)
def test_record_rejects_invalid_input(log, kwargs):
    params = {"level": "info", "category": "risk", "event": "risk_assessed", "timestamp": NOW} | kwargs
    with pytest.raises(InvalidOperationError):
        log.record(params.pop("level"), params.pop("category"), params.pop("event"), "message", **params)


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({}, ["transaction_completed", "failed_login", "transaction_failed", "operation_blocked"]),
        ({"min_level": "warning"}, ["failed_login", "transaction_failed", "operation_blocked"]),
        ({"min_level": AuditLevel.ERROR}, ["transaction_failed", "operation_blocked"]),
        ({"level": "error"}, ["transaction_failed"]),
        ({"category": "transaction"}, ["transaction_completed", "transaction_failed"]),
        ({"event": "failed_login"}, ["failed_login"]),
        ({"client_id": "A"}, ["transaction_completed", "operation_blocked"]),
        ({"account_id": "A1"}, ["transaction_completed"]),
        ({"transaction_id": "T1"}, ["operation_blocked"]),
        ({"since": NOW + timedelta(minutes=1)}, ["failed_login", "transaction_failed", "operation_blocked"]),
        ({"until": NOW + timedelta(minutes=1)}, ["transaction_completed"]),
        ({"client_id": "A", "min_level": "critical"}, ["operation_blocked"]),
        ({"client_id": "nobody"}, []),
    ],
)
def test_filter(filled, filters, expected):
    assert [event.event for event in filled.filter(**filters)] == expected


def test_filter_rejects_unknown_level(filled):
    with pytest.raises(InvalidOperationError):
        filled.filter(min_level="verbose")


def test_file_gets_one_json_line_per_event(tmp_path, filled):
    path = tmp_path / "logs" / "audit.jsonl"  # the folder is created on demand
    log = AuditLog(path)
    for event in filled.events:
        log.record(
            event.level,
            event.category,
            event.event,
            event.message,
            timestamp=event.timestamp,
            client_id=event.client_id,
            account_id=event.account_id,
            transaction_id=event.transaction_id,
            details=event.details,
        )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    assert json.loads(lines[1])["level"] == "WARNING"
    assert AuditLog.load_events(path) == filled.events


def test_file_is_appended_across_logs(tmp_path):
    path = tmp_path / "audit.jsonl"
    AuditLog(path).record("info", "risk", "risk_assessed", "first", timestamp=NOW)
    second = AuditLog(path)
    second.record("info", "risk", "risk_assessed", "second", timestamp=NOW)
    assert len(second) == 1  # memory holds this log's events only
    assert [event.message for event in AuditLog.load_events(path)] == ["first", "second"]


def test_event_round_trips_through_a_dict(filled):
    for event in filled.events:
        assert AuditEvent.from_dict(event.to_dict()) == event


def test_str_shows_level_event_and_message(filled):
    text = str(filled.events[-1])
    assert "CRITICAL" in text and "operation_blocked" in text and "high risk" in text


def test_failed_file_write_keeps_nothing_in_memory(tmp_path):
    log = AuditLog(tmp_path)  # a folder, so appending to it fails
    with pytest.raises(OSError):
        log.record("info", "risk", "risk_assessed", "lost", timestamp=NOW)
    assert log.events == []


def test_events_are_hashable_by_value(filled):
    assert len(set(filled.events + filled.events)) == 4
    assert {AuditEvent.from_dict(event.to_dict()) for event in filled.events} == set(filled.events)


@pytest.mark.parametrize(
    "line", ["not json", '{"level": "INFO"}', '{"timestamp": "2026-09-24T14:00:00", "level": "LOUD"}', "[1, 2]"]
)
def test_load_events_names_the_damaged_line(tmp_path, line):
    path = tmp_path / "audit.jsonl"
    AuditLog(path).record("info", "risk", "risk_assessed", "fine", timestamp=NOW)
    with path.open("a", encoding="utf-8") as file:
        file.write(line + "\n")
    with pytest.raises(InvalidOperationError, match="line 2"):
        AuditLog.load_events(path)
