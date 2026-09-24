"""Helper functions shared across the platform."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from exceptions import InvalidOperationError


def to_money(value: object, *, field: str = "amount") -> Decimal:
    """Convert an arbitrary numeric input into a two-decimal ``Decimal``.

    Accepted inputs: ``int``, ``Decimal``, ``float`` and numeric ``str``.
    ``bool`` is rejected explicitly because it is a subclass of ``int`` and
    would otherwise be silently treated as 0 or 1.

    Floats are converted through ``str()`` so that binary representation
    artefacts (``Decimal(0.1) == 0.1000000000000000055...``) never leak into
    the account balance. The result is rounded half-up to two decimal places.
    """
    if isinstance(value, bool):
        raise InvalidOperationError(f"{field} must be a number, not bool.")

    if isinstance(value, Decimal | int):
        money = Decimal(value)
    elif isinstance(value, float):
        money = Decimal(str(value))
    elif isinstance(value, str):
        try:
            money = Decimal(value.strip())
        except InvalidOperation as exc:
            raise InvalidOperationError(f"{field} must be a numeric string, got {value!r}.") from exc
    else:
        raise InvalidOperationError(f"{field} must be int, float, Decimal or str, got {type(value).__name__}.")

    if not money.is_finite():
        raise InvalidOperationError(f"{field} must be a finite number.")

    try:
        return money.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise InvalidOperationError(f"{field} is too large: {value!r}.") from exc
