"""Audit reports built from the audit log and the risk analyzer: read-only views, no state of their own."""

from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from exceptions import InvalidOperationError, RiskBlockedError
from services.audit_log import AuditCategory, AuditEvent, AuditLevel, AuditLog, TransactionEvent
from services.risk import RiskAnalyzer, RiskAssessment, RiskLevel
from utils import to_enum


@dataclass(frozen=True)
class SuspiciousOperationsReport:
    """Transactions whose latest assessment reached ``min_level``, and the security events."""

    min_level: RiskLevel
    operations: tuple[RiskAssessment, ...]
    security_events: tuple[AuditEvent, ...]

    def __str__(self) -> str:
        lines = [f"Risky transactions ({self.min_level.name.lower()} and above): {len(self.operations)}"]
        for assessment in self.operations:
            action = "blocked" if assessment.blocked else "allowed"
            lines.append(
                f"  {assessment.moment:%m-%d %H:%M} {assessment.transaction_id[:8]} {action:<7} "
                f"{assessment.amount_in_base:>12} | {assessment}"
            )
        lines.append(f"Security events: {len(self.security_events)}")
        lines.extend(f"  {event}" for event in self.security_events)
        return "\n".join(lines)


@dataclass(frozen=True)
class ClientRiskProfile:
    """How risky a client looks, from the latest assessment of each of their transactions.

    ``level`` is the highest level among those assessments (``LOW`` when
    there are none).
    """

    client_id: str
    transactions: int
    by_level: dict[RiskLevel, int]
    blocked: int
    max_score: int
    average_score: Decimal
    top_factors: tuple[tuple[str, int], ...]
    security_events: int
    failed_attempts: int
    level: RiskLevel

    def __str__(self) -> str:
        levels = ", ".join(f"{level.name.lower()} {count}" for level, count in self.by_level.items())
        factors = ", ".join(f"{rule} x{count}" for rule, count in self.top_factors) or "-"
        return "\n".join(
            [
                f"Client {self.client_id[:8]}: {self.level.name.lower()} risk",
                f"  transactions {self.transactions} ({levels}), blocked {self.blocked}",
                f"  score max {self.max_score}, average {self.average_score}",
                f"  factors: {factors}",
                f"  security events {self.security_events}, failed attempts {self.failed_attempts}",
            ]
        )


@dataclass(frozen=True)
class ErrorStatistics:
    """Counts of the audit log by level and of failed transaction attempts by error type.

    ``failure_rate`` is the share of finished transactions that failed, in
    percent: final failures / (completed + final failures).
    """

    events_by_level: dict[AuditLevel, int]
    errors_by_type: dict[str, int]
    failed_attempts: int
    retried: int
    final_failures: int
    completed: int
    blocked_by_risk: int
    failure_rate: Decimal

    def __str__(self) -> str:
        levels = ", ".join(f"{level.name} {count}" for level, count in self.events_by_level.items())
        lines = [
            f"Events by level: {levels}",
            f"Transactions: completed {self.completed}, failed {self.final_failures} "
            f"(failure rate {self.failure_rate}%), blocked by risk control {self.blocked_by_risk}",
            f"Failed attempts: {self.failed_attempts}, of them retried {self.retried}",
        ]
        lines.extend(f"  {error_type:<30} {count}" for error_type, count in self.errors_by_type.items())
        return "\n".join(lines)


class AuditReport:
    """Builds the audit reports; every call reads the current state of its sources."""

    def __init__(self, audit_log: AuditLog, risk_analyzer: RiskAnalyzer) -> None:
        if not isinstance(audit_log, AuditLog):
            raise InvalidOperationError("audit_log must be an AuditLog instance.")
        if not isinstance(risk_analyzer, RiskAnalyzer):
            raise InvalidOperationError("risk_analyzer must be a RiskAnalyzer instance.")
        self._log = audit_log
        self._risk = risk_analyzer

    def _latest_assessments(self, client_id: str | None = None) -> list[RiskAssessment]:
        """The last assessment of each transaction: a retried transaction counts once."""
        latest: dict[str, RiskAssessment] = {}
        for assessment in self._risk.assessments:
            if client_id is None or assessment.client_id == client_id:
                latest.pop(assessment.transaction_id, None)  # keep the order of the last attempt
                latest[assessment.transaction_id] = assessment
        return list(latest.values())

    def suspicious_operations(self, min_level: RiskLevel | str = RiskLevel.MEDIUM) -> SuspiciousOperationsReport:
        lowest = to_enum(RiskLevel, min_level, field="risk level")
        return SuspiciousOperationsReport(
            min_level=lowest,
            operations=tuple(item for item in self._latest_assessments() if item.level >= lowest),
            security_events=tuple(self._log.filter(category=AuditCategory.SECURITY)),
        )

    def client_risk_profile(self, client_id: str) -> ClientRiskProfile:
        assessments = self._latest_assessments(client_id)
        scores = [item.score for item in assessments]
        average = Decimal(sum(scores)) / len(scores) if scores else Decimal(0)
        factors = Counter(rule for item in assessments for rule in item.rules)
        return ClientRiskProfile(
            client_id=client_id,
            transactions=len(assessments),
            by_level={level: sum(1 for item in assessments if item.level is level) for level in RiskLevel},
            blocked=sum(1 for item in assessments if item.blocked),
            max_score=max(scores, default=0),
            average_score=average.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP),
            top_factors=tuple(factors.most_common()),
            security_events=len(self._log.filter(category=AuditCategory.SECURITY, client_id=client_id)),
            failed_attempts=len(self._log.filter(event=TransactionEvent.FAILED.value, client_id=client_id)),
            level=max((item.level for item in assessments), default=RiskLevel.LOW),
        )

    def error_statistics(self) -> ErrorStatistics:
        events = self._log.events
        failures = [event for event in events if event.event == TransactionEvent.FAILED.value]
        errors_by_type = Counter(str(event.details.get("error_type")) for event in failures)
        retried = sum(1 for event in failures if event.details.get("will_retry"))
        final_failures = len(failures) - retried
        completed = sum(1 for event in events if event.event == TransactionEvent.COMPLETED.value)
        finished = completed + final_failures
        rate = Decimal(100 * final_failures) / finished if finished else Decimal(0)
        return ErrorStatistics(
            events_by_level={level: sum(1 for event in events if event.level is level) for level in AuditLevel},
            errors_by_type=dict(errors_by_type.most_common()),
            failed_attempts=len(failures),
            retried=retried,
            final_failures=final_failures,
            completed=completed,
            blocked_by_risk=errors_by_type.get(RiskBlockedError.__name__, 0),
            failure_rate=rate.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP),
        )
