"""Small helpers shared by the test modules."""

from services import Bank, SecurityGuard, SuspicionReason


def reasons(source: Bank | SecurityGuard) -> list[SuspicionReason]:
    """Reasons of the recorded suspicious activities, in order."""
    return [activity.reason for activity in source.suspicious_activities]
