from decimal import Decimal

import pytest

from exceptions import InvalidOperationError
from utils import to_money


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
    ],
)
def test_to_money_converts_and_rounds_half_up(value, expected):
    result = to_money(value)
    assert result == expected
    assert result.as_tuple().exponent == -2


@pytest.mark.parametrize("value", [True, False, None, [1], object(), "ten", "nan"])
def test_to_money_rejects_unsupported_input(value):
    with pytest.raises(InvalidOperationError):
        to_money(value)


def test_to_money_reports_field_name():
    with pytest.raises(InvalidOperationError, match="initial_balance"):
        to_money("oops", field="initial_balance")
