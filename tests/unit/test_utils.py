import uuid
from datetime import datetime
from decimal import Decimal

import pytest

from exceptions import InvalidOperationError
from models import AccountStatus, Currency
from utils import ManualClock, resolve_identifier, to_enum, to_money, to_positive_decimal, to_rate


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (10, Decimal("10.00")),
        (Decimal("10.5"), Decimal("10.50")),
        ("  7.25 ", Decimal("7.25")),
        (0.1 + 0.2, Decimal("0.30")),
        (1.005, Decimal("1.01")),
        ("2.345", Decimal("2.35")),
        (-3, Decimal("-3.00")),
        ("-0", Decimal("0.00")),
        (-0.004, Decimal("0.00")),
    ],
)
def test_to_money_converts_and_rounds_half_up(value, expected):
    money = to_money(value)
    assert money == expected
    assert money.as_tuple() == expected.as_tuple()


@pytest.mark.parametrize("value", [True, None, [1], object(), "ten", "nan", "1e30"])
def test_to_money_rejects_unsupported_input(value):
    with pytest.raises(InvalidOperationError):
        to_money(value)


def test_to_money_reports_field_name():
    with pytest.raises(InvalidOperationError, match="initial_balance"):
        to_money("oops", field="initial_balance")


@pytest.mark.parametrize("value", [0, "-0.01", -0.004])
def test_to_money_positive_rejects_zero_and_negative(value):
    with pytest.raises(InvalidOperationError, match="greater than zero"):
        to_money(value, require="positive")


def test_to_money_non_negative_accepts_zero_and_rejects_negative():
    assert to_money("-0.004", require="non_negative") == Decimal("0.00")
    with pytest.raises(InvalidOperationError, match="cannot be negative"):
        to_money("-0.01", require="non_negative")


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0.005", Decimal("0.005")), (0.1, Decimal("0.1")), (1, Decimal("1")), (Decimal("0"), Decimal("0"))],
)
def test_to_rate_keeps_precision(value, expected):
    assert to_rate(value) == expected


def test_to_rate_rejects_negative_unless_allowed():
    with pytest.raises(InvalidOperationError, match="negative"):
        to_rate("-0.1")
    assert to_rate("-0.1", allow_negative=True) == Decimal("-0.1")


@pytest.mark.parametrize("value", [-1, 1, "0.999"])
def test_to_rate_accepts_boundaries(value):
    assert to_rate(value, allow_negative=True) == Decimal(str(value))


@pytest.mark.parametrize("value", [-1.5, "1.01", 50])
def test_to_rate_stays_within_minus_one_and_one(value):
    with pytest.raises(InvalidOperationError, match="between -1 and 1"):
        to_rate(value, allow_negative=True)


@pytest.mark.parametrize("value", [True, None, "abc", "nan", "inf"])
def test_to_rate_rejects_unsupported_input(value):
    with pytest.raises(InvalidOperationError):
        to_rate(value)


def test_resolve_identifier_generates_uuid4_for_none():
    assert uuid.UUID(resolve_identifier(None)).version == 4


def test_resolve_identifier_strips_value():
    assert resolve_identifier("  ID-1 ") == "ID-1"


@pytest.mark.parametrize("value", ["", "   ", 7, b"id"])
def test_resolve_identifier_rejects_invalid_value(value):
    with pytest.raises(InvalidOperationError, match="client_id"):
        resolve_identifier(value, field="client_id")


@pytest.mark.parametrize(
    ("enum_type", "value", "expected"),
    [
        (Currency, Currency.USD, Currency.USD),
        (Currency, "usd", Currency.USD),
        (AccountStatus, "FROZEN", AccountStatus.FROZEN),
    ],
)
def test_to_enum_accepts_member_or_value_in_any_case(enum_type, value, expected):
    assert to_enum(enum_type, value, field="x") is expected


def test_to_enum_rejects_unknown_value_and_lists_allowed():
    with pytest.raises(InvalidOperationError, match="Unsupported currency 'GBP'; allowed: RUB, USD"):
        to_enum(Currency, "GBP", field="currency")


def test_to_positive_decimal_keeps_precision():
    assert to_positive_decimal("0.1826", field="rate") == Decimal("0.1826")


@pytest.mark.parametrize("value", [0, -1, "abc", None])
def test_to_positive_decimal_rejects_non_positive_or_invalid(value):
    with pytest.raises(InvalidOperationError):
        to_positive_decimal(value, field="rate")


def test_manual_clock_returns_the_moment_it_was_set_to():
    clock = ManualClock(datetime(2026, 1, 1, 12, 0))
    assert clock() == datetime(2026, 1, 1, 12, 0)
    clock.moment = datetime(2026, 1, 2, 3, 0)
    assert clock() == datetime(2026, 1, 2, 3, 0)
