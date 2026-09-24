from decimal import Decimal

import pytest

from exceptions import InvalidOperationError
from utils import to_money, to_rate


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
        to_money(value, positive=True)


def test_to_money_non_negative_accepts_zero_and_rejects_negative():
    assert to_money("-0.004", non_negative=True) == Decimal("0.00")
    with pytest.raises(InvalidOperationError, match="cannot be negative"):
        to_money("-0.01", non_negative=True)


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


def test_to_rate_never_goes_below_minus_one():
    with pytest.raises(InvalidOperationError, match="below -1"):
        to_rate(-1.5, allow_negative=True)


@pytest.mark.parametrize("value", [True, None, "abc", "nan", "inf"])
def test_to_rate_rejects_unsupported_input(value):
    with pytest.raises(InvalidOperationError):
        to_rate(value)
