"""Conversion of money into the bank's base currency."""

from collections.abc import Mapping
from decimal import Decimal

from exceptions import InvalidOperationError
from models.enums import Currency
from utils import to_enum, to_money, to_positive_decimal


class CurrencyConverter:
    """Converts amounts into a base currency using fixed reference rates.

    ``rates`` maps every supported currency to the price of one unit in the
    base currency; the base currency itself must have the rate 1. Without
    ``rates`` the converter uses ``DEFAULT_RATES``: reference prices in
    roubles, fixed on purpose because the platform has no market data feed.
    """

    DEFAULT_RATES: dict[Currency, str] = {
        Currency.RUB: "1",
        Currency.USD: "90",
        Currency.EUR: "100",
        Currency.KZT: "0.18",
        Currency.CNY: "12.5",
    }

    def __init__(
        self,
        rates: Mapping[Currency | str, object] | None = None,
        base: Currency | str = Currency.RUB,
    ) -> None:
        self._base = to_enum(Currency, base, field="currency")
        self._rates = self._parse_rates(self.DEFAULT_RATES if rates is None else rates)
        if self._rates[self._base] != 1:
            raise InvalidOperationError(f"The rate of the base currency {self._base.value} must be 1.")

    @staticmethod
    def _parse_rates(rates: Mapping[Currency | str, object]) -> dict[Currency, Decimal]:
        if not isinstance(rates, Mapping):
            raise InvalidOperationError("rates must be a mapping of currency to rate.")
        parsed: dict[Currency, Decimal] = {}
        for key, rate in rates.items():
            currency = to_enum(Currency, key, field="currency")
            if currency in parsed:
                raise InvalidOperationError(f"rates has more than one rate for {currency.value}.")
            parsed[currency] = to_positive_decimal(rate, field=f"rates[{currency.value}]")
        missing = [currency.value for currency in Currency if currency not in parsed]
        if missing:
            raise InvalidOperationError(f"rates is missing: {', '.join(missing)}.")
        return parsed

    @property
    def base(self) -> Currency:
        return self._base

    def to_base(self, amount: object, currency: Currency | str) -> Decimal:
        """Return ``amount`` of ``currency`` expressed in the base currency."""
        return self.convert(amount, currency, self._base)

    def convert(self, amount: object, source: Currency | str, target: Currency | str) -> Decimal:
        """Return ``amount`` of ``source`` expressed in ``target``.

        Two foreign currencies are converted through the base currency (a
        cross rate); the result is rounded once, at the end.
        """
        source_rate = self._rates[to_enum(Currency, source, field="currency")]
        target_rate = self._rates[to_enum(Currency, target, field="currency")]
        return to_money(to_money(amount) * source_rate / target_rate)
