from datetime import datetime, time
from decimal import Decimal

import pytest

from exceptions import AuthenticationError, ClientBlockedError, InvalidOperationError, OperationTimeRestrictedError
from services import SecurityGuard, SuspicionReason

PASSWORD = "correct-horse-1"


@pytest.fixture
def guard(security, owner):
    security.register_password(owner.client_id, PASSWORD)
    return security


def reasons(guard):
    return [activity.reason for activity in guard.suspicious_activities]


def test_password_is_stored_as_salted_hash(security):
    security.register_password("A", PASSWORD)
    security.register_password("B", PASSWORD)
    stored_a, stored_b = security._passwords["A"], security._passwords["B"]
    assert PASSWORD.encode() not in stored_a.digest
    assert stored_a.salt != stored_b.salt
    assert stored_a.digest != stored_b.digest


@pytest.mark.parametrize("password", ["short", "", None, 12345678])
def test_rejects_weak_or_non_string_password(security, password):
    with pytest.raises(InvalidOperationError):
        security.register_password("A", password)


def test_right_password_passes_and_logs_nothing(guard, owner):
    guard.authenticate(owner, PASSWORD)
    assert guard.suspicious_activities == []


@pytest.mark.parametrize("password", ["wrong-password", None, b"correct-horse-1"])
def test_wrong_password_counts_and_reports_attempts_left(guard, owner, password):
    with pytest.raises(AuthenticationError) as info:
        guard.authenticate(owner, password)
    assert info.value.attempts_left == 2
    assert guard.failed_attempts(owner.client_id) == 1
    assert reasons(guard) == [SuspicionReason.FAILED_LOGIN]


def test_third_failure_blocks_the_client(guard, owner):
    for _ in range(2):
        with pytest.raises(AuthenticationError):
            guard.authenticate(owner, "wrong-password")
    with pytest.raises(ClientBlockedError):
        guard.authenticate(owner, "wrong-password")
    assert owner.is_blocked
    assert reasons(guard) == [SuspicionReason.FAILED_LOGIN] * 3 + [SuspicionReason.CLIENT_BLOCKED]


def test_blocked_client_is_rejected_even_with_right_password(guard, owner):
    owner.block()
    with pytest.raises(ClientBlockedError):
        guard.authenticate(owner, PASSWORD)
    assert reasons(guard) == [SuspicionReason.BLOCKED_CLIENT_ACTIVITY]


def test_success_resets_the_failure_counter(guard, owner):
    for _ in range(2):
        with pytest.raises(AuthenticationError):
            guard.authenticate(owner, "wrong-password")
    guard.authenticate(owner, PASSWORD)
    assert guard.failed_attempts(owner.client_id) == 0
    with pytest.raises(AuthenticationError):
        guard.authenticate(owner, "wrong-password")
    assert not owner.is_blocked


def test_unknown_credentials_count_as_wrong_password(security, owner):
    with pytest.raises(AuthenticationError):
        security.authenticate(owner, PASSWORD)


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
def test_review_amount_flags_from_threshold(security, amount, flagged):
    assert security.review_amount(amount, "deposit", client_id="C", account_id="A") is flagged
    assert reasons(security) == ([SuspicionReason.LARGE_OPERATION] if flagged else [])


def test_log_is_returned_as_a_copy(security):
    security.flag(SuspicionReason.LARGE_OPERATION, "test")
    security.suspicious_activities.clear()
    assert len(security.suspicious_activities) == 1


def test_default_clock_is_system_time():
    before = datetime.now()
    assert before <= SecurityGuard().now() <= datetime.now()
