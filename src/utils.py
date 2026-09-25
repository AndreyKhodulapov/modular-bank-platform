"""Helper functions shared across the platform."""

import uuid
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
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
    A negative rate is only accepted with ``allow_negative=True``; rates
    outside ``[-1, 1]`` are rejected.
    """
    rate = _to_decimal(value, field)
    if rate < 0 and not allow_negative:
        raise InvalidOperationError(f"{field} cannot be negative, got {rate}.")
    if not -1 <= rate <= 1:
        raise InvalidOperationError(f"{field} must be between -1 and 1, got {rate}.")
    return rate


def resolve_identifier(value: str | None, *, field: str = "id") -> str:
    """Return a stripped identifier, or a new UUID4 string when ``value`` is ``None``."""
    if value is None:
        return str(uuid.uuid4())
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationError(f"{field} must be a non-empty string.")
    return value.strip()


def to_enum[E: Enum](enum_type: type[E], value: E | str, *, field: str) -> E:
    """Return the ``enum_type`` member for ``value``.

    Strings match a member's value or name in any case, so an enum with
    numeric values (a priority) is still addressed by a readable word.
    """
    if isinstance(value, enum_type):
        return value
    text = str(value).casefold()
    for member in enum_type:
        if text in (str(member.value).casefold(), member.name.casefold()):
            return member
    allowed = ", ".join(
        str(member.value) if isinstance(member.value, str) else member.name.lower() for member in enum_type
    )
    raise InvalidOperationError(f"Unsupported {field} {value!r}; allowed: {allowed}.")


def to_positive_decimal(value: object, *, field: str) -> Decimal:
    """Convert a numeric input into a ``Decimal`` greater than zero, keeping its precision."""
    number = _to_decimal(value, field)
    if number <= 0:
        raise InvalidOperationError(f"{field} must be greater than zero, got {number}.")
    return number


class ManualClock:
    """A clock that shows a moment set by hand.

    Anything that asks for the current time accepts a zero-argument callable
    (``datetime.now`` in production). Passing a ``ManualClock`` instead lets
    the demo and the tests move time explicitly, e.g. into the night window.
    """

    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment
