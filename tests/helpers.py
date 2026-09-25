"""Small helpers shared by the test modules."""

from services import AuditCategory, Bank, SecurityGuard, SuspicionReason


def reasons(source: Bank | SecurityGuard) -> list[SuspicionReason]:
    """Reasons of the recorded suspicious activities, in order."""
    return [activity.reason for activity in source.suspicious_activities]


def lifecycle(bank: Bank) -> list[str]:
    """Names of the recorded client and account events, in order."""
    return [event.event for event in bank.audit_log if event.category in (AuditCategory.ACCOUNT, AuditCategory.CLIENT)]
