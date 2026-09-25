from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from exceptions import InvalidOperationError
from models import Transaction
from services import AuditLevel, AuditLog, AuditReport, RiskAnalyzer, RiskContext, RiskLevel

NOW = datetime(2026, 9, 24, 14, 0)


@pytest.fixture
def log() -> AuditLog:
    return AuditLog()


@pytest.fixture
def analyzer() -> RiskAnalyzer:
    return RiskAnalyzer()


@pytest.fixture
def report(log, analyzer) -> AuditReport:
    return AuditReport(log, analyzer)


def assess(analyzer, client_id, amount, *, transaction=None, moment=NOW, recipient="R"):
    transaction = transaction or Transaction(
        "transfer", 100, "RUB", sender_id=f"{client_id}-acc", recipient_id=recipient, created_at=NOW
    )
    return analyzer.assess(
        RiskContext(transaction=transaction, client_id=client_id, moment=moment, amount_in_base=Decimal(amount))
    )


def failed(log, client_id, error_type, *, will_retry=False):
    log.record(
        "error",
        "transaction",
        "transaction_failed",
        error_type,
        timestamp=NOW,
        client_id=client_id,
        details={"error_type": error_type, "will_retry": will_retry},
    )


def completed(log, client_id):
    log.record("info", "transaction", "transaction_completed", "ok", timestamp=NOW, client_id=client_id)


def test_suspicious_operations_lists_medium_and_high_and_security_events(report, log, analyzer):
    assess(analyzer, "A", 100)  # new recipient only: low
    medium = assess(analyzer, "A", 500_000)  # 40 + 20
    high = assess(analyzer, "B", 2_000_000)  # 70 + 20
    log.record("warning", "security", "failed_login", "wrong password", timestamp=NOW, client_id="B")
    result = report.suspicious_operations()
    assert result.operations == (medium, high)
    assert [event.event for event in result.security_events] == ["failed_login"]
    assert report.suspicious_operations("high").operations == (high,)
    assert len(report.suspicious_operations(RiskLevel.LOW).operations) == 3


def test_retried_transaction_is_reported_by_its_latest_assessment(report, analyzer):
    transaction = Transaction("transfer", 100, "RUB", sender_id="S", recipient_id="R", created_at=NOW)
    assess(analyzer, "A", 500_000, transaction=transaction)
    last = assess(analyzer, "A", 500_000, transaction=transaction, moment=NOW.replace(hour=23))
    assert report.suspicious_operations().operations == (last,)
    assert last.blocked


def test_client_risk_profile(report, log, analyzer):
    assess(analyzer, "A", 100)  # 20
    assess(analyzer, "A", 500_000)  # 60
    assess(analyzer, "A", 2_000_000)  # 90, blocked
    assess(analyzer, "B", 2_000_000)
    log.record("warning", "security", "night_operation", "night", timestamp=NOW, client_id="A")
    failed(log, "A", "RiskBlockedError")
    profile = report.client_risk_profile("A")
    assert profile.transactions == 3
    assert profile.by_level == {RiskLevel.LOW: 1, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 1}
    assert (profile.blocked, profile.max_score, profile.average_score) == (1, 90, Decimal("56.7"))
    assert profile.top_factors == (("new_recipient", 3), ("large_amount", 2))
    assert (profile.security_events, profile.failed_attempts, profile.level) == (1, 1, RiskLevel.HIGH)
    assert "high risk" in str(profile)


def test_profile_of_a_client_without_transactions_is_low(report):
    profile = report.client_risk_profile("nobody")
    assert (profile.transactions, profile.max_score, profile.average_score, profile.level) == (
        0,
        0,
        Decimal("0.0"),
        RiskLevel.LOW,
    )


def test_error_statistics(report, log):
    completed(log, "A")
    completed(log, "A")
    completed(log, "B")
    failed(log, "A", "InsufficientFundsError", will_retry=True)
    failed(log, "A", "InsufficientFundsError")
    failed(log, "B", "RiskBlockedError")
    failed(log, "B", "AccountFrozenError")
    log.record("critical", "risk", "operation_blocked", "high", timestamp=NOW)
    stats = report.error_statistics()
    assert stats.events_by_level == {
        AuditLevel.INFO: 3,
        AuditLevel.WARNING: 0,
        AuditLevel.ERROR: 4,
        AuditLevel.CRITICAL: 1,
    }
    assert stats.errors_by_type == {"InsufficientFundsError": 2, "RiskBlockedError": 1, "AccountFrozenError": 1}
    assert (stats.failed_attempts, stats.retried, stats.final_failures, stats.completed) == (4, 1, 3, 3)
    assert (stats.blocked_by_risk, stats.failure_rate) == (1, Decimal("50.0"))
    assert "failure rate 50.0%" in str(stats)


def test_error_statistics_of_an_empty_log(report):
    stats = report.error_statistics()
    assert (stats.failed_attempts, stats.completed, stats.failure_rate) == (0, 0, Decimal("0.0"))


def test_reports_read_the_current_state(report, analyzer):
    assert report.suspicious_operations().operations == ()
    assess(analyzer, "A", 2_000_000, moment=NOW + timedelta(minutes=1))
    assert len(report.suspicious_operations().operations) == 1


def test_suspicious_operations_str(report, analyzer):
    assess(analyzer, "A", 2_000_000)
    text = str(report.suspicious_operations())
    assert "blocked" in text and "large_amount, new_recipient" in text


@pytest.mark.parametrize(("log_arg", "analyzer_arg"), [("log", RiskAnalyzer()), (AuditLog(), "analyzer")])
def test_rejects_wrong_sources(log_arg, analyzer_arg):
    with pytest.raises(InvalidOperationError):
        AuditReport(log_arg, analyzer_arg)
