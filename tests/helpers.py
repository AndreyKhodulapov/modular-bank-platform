"""Small helpers shared by the test modules."""

from collections.abc import Iterable
from decimal import Decimal

from services import AuditCategory, Bank, SecurityGuard, SuspicionReason


def reasons(source: Bank | SecurityGuard) -> list[SuspicionReason]:
    """Reasons of the recorded suspicious activities, in order."""
    return [activity.reason for activity in source.suspicious_activities]


def lifecycle(bank: Bank) -> list[str]:
    """Names of the recorded client and account events, in order."""
    return [event.event for event in bank.audit_log if event.category in (AuditCategory.ACCOUNT, AuditCategory.CLIENT)]


def history_gaps(bank: Bank, *, bypassed: Iterable[str] = ()) -> dict[str, Decimal]:
    """Accounts whose balance the history does not explain, with the unexplained amount.

    The movements of an account must add up to its balance, and the last one
    must end on it. ``bypassed`` accounts are skipped: money moved on them past
    the bank (``invest()``, ``apply_monthly_interest()``).
    """
    skipped = set(bypassed)
    gaps = {}
    for account in bank.search_accounts():
        if account.account_id in skipped:
            continue
        movements = bank.history.movements(account.account_id)
        total = sum((movement.amount for movement in movements), Decimal("0.00"))
        last = movements[-1].balance_after if movements else Decimal("0.00")
        if total != account.balance or last != account.balance:
            gaps[account.account_id] = account.balance - total
    return gaps
