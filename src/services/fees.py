"""The bank's commission on transactions."""

from decimal import Decimal

from exceptions import InvalidOperationError
from models.enums import Currency, TransactionType
from services.currency import CurrencyConverter
from utils import to_money, to_rate


class FeePolicy:
    """Calculates the commission for a transaction (the Strategy pattern).

    The processor only calls ``calculate()``, so another tariff is introduced
    by passing a different policy object, without touching the processor.

    The default tariff charges external transfers ``rate`` of the amount,
    clamped to ``[minimum, maximum]``; transfers inside the bank, deposits and
    withdrawals are free. ``minimum`` and ``maximum`` are set in the
    converter's base currency and converted into the sender's currency.
    """

    def __init__(self, rate: object = "0.01", minimum: object = 50, maximum: object = 5000) -> None:
        self._rate = to_rate(rate, field="fee rate")
        self._minimum = to_money(minimum, field="minimum fee", require="non_negative")
        self._maximum = to_money(maximum, field="maximum fee", require="non_negative")
        if self._minimum > self._maximum:
            raise InvalidOperationError(f"minimum fee {self._minimum} exceeds maximum fee {self._maximum}.")

    @property
    def rate(self) -> Decimal:
        return self._rate

    @property
    def minimum(self) -> Decimal:
        return self._minimum

    @property
    def maximum(self) -> Decimal:
        return self._maximum

    def calculate(
        self,
        transaction_type: TransactionType,
        amount: Decimal,
        currency: Currency,
        converter: CurrencyConverter,
    ) -> Decimal:
        """Return the fee for moving ``amount`` of ``currency``, in that currency."""
        if transaction_type is not TransactionType.EXTERNAL_TRANSFER:
            return Decimal("0.00")
        low = converter.convert(self._minimum, converter.base, currency)
        high = converter.convert(self._maximum, converter.base, currency)
        return min(max(to_money(amount * self._rate), low), high)
