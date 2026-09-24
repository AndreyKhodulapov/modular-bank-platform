from decimal import Decimal

import pytest

from exceptions import InvalidOperationError
from models import Currency
from services import CurrencyConverter


def test_default_converter_uses_rouble_as_base():
    converter = CurrencyConverter()
    assert converter.base is Currency.RUB


@pytest.mark.parametrize(
    ("amount", "currency", "expected"),
    [
        (100, "RUB", Decimal("100.00")),
        (100, Currency.USD, Decimal("9000.00")),
        ("-5", "EUR", Decimal("-500.00")),
        ("333.33", "KZT", Decimal("60.00")),  # 59.9994 rounded half-up
    ],
)
def test_to_base(amount, currency, expected):
    assert CurrencyConverter().to_base(amount, currency) == expected


def test_custom_base_currency():
    rates = {"USD": 1, "RUB": "0.011", "EUR": "1.1", "KZT": "0.002", "CNY": "0.14"}
    converter = CurrencyConverter(rates, base="usd")
    assert converter.to_base(1000, "RUB") == Decimal("11.00")


@pytest.mark.parametrize(
    "rates",
    [
        {**CurrencyConverter.DEFAULT_RATES, Currency.RUB: "2"},  # base rate is not 1
        {key: value for key, value in CurrencyConverter.DEFAULT_RATES.items() if key is not Currency.CNY},  # missing
        {**CurrencyConverter.DEFAULT_RATES, "usd": "91"},  # the same currency twice
        {**CurrencyConverter.DEFAULT_RATES, Currency.USD: 0},
        {**CurrencyConverter.DEFAULT_RATES, "GBP": 110},
        [("RUB", 1)],
    ],
)
def test_rejects_invalid_rates(rates):
    with pytest.raises(InvalidOperationError):
        CurrencyConverter(rates)
