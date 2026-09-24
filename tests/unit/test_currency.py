from decimal import Decimal

import pytest

from exceptions import InvalidOperationError
from models import Currency
from services import DEFAULT_RATES_TO_RUB, CurrencyConverter


def test_default_converter_uses_rouble_as_base():
    converter = CurrencyConverter()
    assert converter.base is Currency.RUB
    assert converter.rates[Currency.USD] == Decimal("90")


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


def test_rates_property_is_a_copy():
    converter = CurrencyConverter()
    converter.rates[Currency.USD] = Decimal("1")
    assert converter.rates[Currency.USD] == Decimal("90")


def test_custom_base_currency():
    rates = {"USD": 1, "RUB": "0.011", "EUR": "1.1", "KZT": "0.002", "CNY": "0.14"}
    converter = CurrencyConverter(rates, base="usd")
    assert converter.to_base(1000, "RUB") == Decimal("11.00")


@pytest.mark.parametrize(
    "rates",
    [
        {**DEFAULT_RATES_TO_RUB, Currency.RUB: "2"},  # base rate is not 1
        {key: value for key, value in DEFAULT_RATES_TO_RUB.items() if key is not Currency.CNY},  # missing
        {**DEFAULT_RATES_TO_RUB, "usd": "91"},  # the same currency twice
        {**DEFAULT_RATES_TO_RUB, Currency.USD: 0},
        {**DEFAULT_RATES_TO_RUB, "GBP": 110},
        [("RUB", 1)],
    ],
)
def test_rejects_invalid_rates(rates):
    with pytest.raises(InvalidOperationError):
        CurrencyConverter(rates)
