from decimal import Decimal

import pytest

from exceptions import InvalidOperationError
from models import Currency, TransactionType
from services import CurrencyConverter, FeePolicy

converter = CurrencyConverter()


@pytest.mark.parametrize(
    ("amount", "currency", "expected"),
    [
        ("10000", Currency.RUB, Decimal("100.00")),  # 1%
        ("1000", Currency.RUB, Decimal("50.00")),  # raised to the minimum
        ("1000000", Currency.RUB, Decimal("5000.00")),  # capped at the maximum
        ("100", Currency.USD, Decimal("1.00")),
        ("10", Currency.USD, Decimal("0.56")),  # minimum 50 RUB in dollars
        ("100000", Currency.USD, Decimal("55.56")),  # maximum 5000 RUB in dollars
    ],
)
def test_external_transfer_fee(amount, currency, expected):
    fee = FeePolicy().calculate(TransactionType.EXTERNAL_TRANSFER, Decimal(amount), currency, converter)
    assert fee == expected


@pytest.mark.parametrize(
    "transaction_type", [TransactionType.DEPOSIT, TransactionType.WITHDRAWAL, TransactionType.TRANSFER]
)
def test_other_types_are_free(transaction_type):
    assert FeePolicy().calculate(transaction_type, Decimal("10000"), Currency.RUB, converter) == Decimal("0.00")


def test_custom_tariff():
    policy = FeePolicy(rate="0.02", minimum=0, maximum=100)
    assert (policy.rate, policy.minimum, policy.maximum) == (Decimal("0.02"), Decimal("0.00"), Decimal("100.00"))
    assert policy.calculate(TransactionType.EXTERNAL_TRANSFER, Decimal("100"), Currency.RUB, converter) == Decimal(
        "2.00"
    )


@pytest.mark.parametrize(
    "params",
    [{"rate": "-0.01"}, {"rate": 2}, {"minimum": -1}, {"minimum": 100, "maximum": 10}, {"maximum": "lots"}],
)
def test_rejects_invalid_tariff(params):
    with pytest.raises(InvalidOperationError):
        FeePolicy(**params)
