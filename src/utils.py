"""Helper functions shared across the platform."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

from exceptions import InvalidOperationError


def _to_decimal(value: object, field: str) -> Decimal:
    """Convert a numeric input into a finite ``Decimal`` without rounding.

    Accepted inputs: ``int``, ``Decimal``, ``float`` and numeric ``str``.
    ``bool`` is rejected explicitly because it is a subclass of ``int`` and
    would otherwise be silently treated as 0 or 1.

    Floats are converted through ``str()`` so that binary representation
    artefacts (``Decimal(0.1) == 0.1000000000000000055...``) never leak into
    the domain.
    """
    if isinstance(value, bool):
        raise InvalidOperationError(f"{field} must be a number, not bool.")

    if isinstance(value, Decimal | int):
        number = Decimal(value)
    elif isinstance(value, float):
        number = Decimal(str(value))
    elif isinstance(value, str):
        try:
            number = Decimal(value.strip())
        except InvalidOperation as exc:
            raise InvalidOperationError(f"{field} must be a numeric string, got {value!r}.") from exc
    else:
        raise InvalidOperationError(f"{field} must be int, float, Decimal or str, got {type(value).__name__}.")

    if not number.is_finite():
        raise InvalidOperationError(f"{field} must be a finite number.")
    return number


def to_money(
    value: object,
    *,
    field: str = "amount",
    require: Literal["positive", "non_negative"] | None = None,
) -> Decimal:
    """Convert an arbitrary numeric input into a two-decimal ``Decimal``.

    The result is rounded half-up to two decimal places; a negative zero
    produced by rounding (``"-0.004"``) is normalised to ``0.00``.
    ``require="positive"`` demands a rounded value strictly greater than
    zero, ``require="non_negative"`` a value that is not below zero.
    """
    number = _to_decimal(value, field)
    try:
        money = +number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise InvalidOperationError(f"{field} is too large: {value!r}.") from exc

    if require == "positive" and money <= 0:
        raise InvalidOperationError(f"{field} must be greater than zero, got {money}.")
    if require == "non_negative" and money < 0:
        raise InvalidOperationError(f"{field} cannot be negative, got {money}.")
    return money


def to_rate(value: object, *, field: str = "rate", allow_negative: bool = False) -> Decimal:
    """Convert a numeric input into a ``Decimal`` rate expressed as a fraction.

    Rates are fractions, not percentages: ``0.10`` means 10%. Unlike money
    they are not rounded to two decimal places, so ``0.005`` (0.5%) survives.
    A negative rate is only accepted with ``allow_negative=True``. Either way
    the rate stays within ``[-1, 1]``: a loss of more than 100% is
    meaningless, and a gain above 100% per period is outside what this
    model is meant to describe.
    """
    rate = _to_decimal(value, field)
    if rate < 0 and not allow_negative:
        raise InvalidOperationError(f"{field} cannot be negative, got {rate}.")
    if not -1 <= rate <= 1:
        raise InvalidOperationError(f"{field} must be between -1 and 1, got {rate}.")
    return rate
