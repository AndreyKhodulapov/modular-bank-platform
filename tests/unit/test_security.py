from datetime import datetime, time
from decimal import Decimal

import pytest

from exceptions import AuthenticationError, ClientBlockedError, InvalidOperationError, OperationTimeRestrictedError
from services import SecurityGuard, SuspicionReason


@pytest.fixture
def guard(security, owner, password):
    security.register_password(owner.client_id, password)
    return security


def test_password_is_stored_as_salted_hash(security, password):
    security.register_password("A", password)
    security.register_password("B", password)
    stored_a, stored_b = security._passwords["A"], security._passwords["B"]
    assert password.encode() not in stored_a.digest
    assert stored_a.salt != stored_b.salt
    assert stored_a.digest != stored_b.digest


@pytest.mark.parametrize("candidate", ["short", "", None, 12345678])
def test_rejects_weak_or_non_string_password(security, candidate):
    with pytest.raises(InvalidOperationError):
        security.register_password("A", candidate)


def test_right_password_passes_and_logs_nothing(guard, owner, password):
    guard.authenticate(owner, password)
    assert guard.suspicious_activities == []


@pytest.mark.parametrize("candidate", ["wrong-password", None, b"correct-horse-1"])
def test_wrong_password_counts_and_reports_attempts_left(guard, owner, reasons, candidate):
    with pytest.raises(AuthenticationError) as info:
        guard.authenticate(owner, candidate)
    assert info.value.attempts_left == 2
    assert guard.failed_attempts(owner.client_id) == 1
    assert reasons(guard) == [SuspicionReason.FAILED_LOGIN]


def test_third_failure_blocks_the_client(guard, owner, reasons):
    for _ in range(2):
        with pytest.raises(AuthenticationError):
            guard.authenticate(owner, "wrong-password")
    with pytest.raises(ClientBlockedError):
        guard.authenticate(owner, "wrong-password")
    assert owner.is_blocked
    assert reasons(guard) == [SuspicionReason.FAILED_LOGIN] * 3 + [SuspicionReason.CLIENT_BLOCKED]


def test_blocked_client_is_rejected_even_with_right_password(guard, owner, password, reasons):
    owner.block()
    with pytest.raises(ClientBlockedError):
        guard.authenticate(owner, password)
    assert reasons(guard) == [SuspicionReason.BLOCKED_CLIENT_ACTIVITY]


def test_success_resets_the_failure_counter(guard, owner, password):
    for _ in range(2):
        with pytest.raises(AuthenticationError):
            guard.authenticate(owner, "wrong-password")
    guard.authenticate(owner, password)
    assert guard.failed_attempts(owner.client_id) == 0
    with pytest.raises(AuthenticationError):
        guard.authenticate(owner, "wrong-password")
    assert not owner.is_blocked


def test_unknown_credentials_count_as_wrong_password(security, owner, password):
    with pytest.raises(AuthenticationError):
        security.authenticate(owner, password)


@pytest.mark.parametrize(
    ("moment", "night"),
    [
        (time(23, 59, 59), False),
        (time(0, 0), True),
        (time(2, 30), True),
        (time(4, 59, 59), True),
        (time(5, 0), False),
    ],
)
def test_night_window_boundaries(security, clock, moment, night):
    clock.moment = datetime.combine(clock.moment.date(), moment)
    assert security.is_night() is night


def test_ensure_daytime_rejects_and_logs_night_action(security, clock):
    clock.moment = datetime(2026, 9, 25, 1, 15)
    with pytest.raises(OperationTimeRestrictedError, match="between 00:00 and 05:00"):
        security.ensure_daytime("withdraw", client_id="C", account_id="A")
    [activity] = security.suspicious_activities
    assert activity.reason is SuspicionReason.NIGHT_OPERATION
    assert (activity.timestamp, activity.client_id, activity.account_id) == (clock.moment, "C", "A")


def test_ensure_daytime_allows_daytime_action(security):
    security.ensure_daytime("withdraw")
    assert security.suspicious_activities == []


@pytest.mark.parametrize(
    ("amount", "flagged"),
    [(Decimal("499999.99"), False), (Decimal("500000.00"), True)],
)
def test_review_amount_flags_from_threshold(security, reasons, amount, flagged):
    assert security.review_amount(amount, "deposit", client_id="C", account_id="A") is flagged
    assert reasons(security) == ([SuspicionReason.LARGE_OPERATION] if flagged else [])


def test_log_is_returned_as_a_copy(security):
    security.flag(SuspicionReason.LARGE_OPERATION, "test")
    security.suspicious_activities.clear()
    assert len(security.suspicious_activities) == 1


def test_default_clock_is_system_time():
    before = datetime.now()
    assert before <= SecurityGuard().now() <= datetime.now()
