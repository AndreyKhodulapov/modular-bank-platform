"""Account owner model."""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from exceptions import InvalidOperationError

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_PATTERN = re.compile(r"^\+?\d{10,15}$")


@dataclass(frozen=True, kw_only=True)
class Owner:
    """Immutable personal data of an account holder.

    The class is frozen: once an owner is created its data cannot be altered
    by accident, which keeps the account's ``owner`` attribute trustworthy.
    """

    first_name: str
    last_name: str
    middle_name: str | None = field(default=None)
    birth_date: date
    email: str
    phone: str

    def __post_init__(self) -> None:
        self._validate_name("first_name", self.first_name)
        self._validate_name("last_name", self.last_name)
        if self.middle_name is not None:
            self._validate_name("middle_name", self.middle_name)

        if not isinstance(self.birth_date, date):
            raise InvalidOperationError("birth_date must be a datetime.date.")
        if self.birth_date > date.today():
            raise InvalidOperationError("birth_date cannot be in the future.")

        if not isinstance(self.email, str) or not _EMAIL_PATTERN.match(self.email):
            raise InvalidOperationError(f"Invalid email address: {self.email!r}.")

        if not isinstance(self.phone, str) or not _PHONE_PATTERN.match(self.phone):
            raise InvalidOperationError(
                f"Invalid phone number: {self.phone!r} "
                "(expected 10-15 digits with optional leading '+')."
            )

    @staticmethod
    def _validate_name(field_name: str, value: object) -> None:
        if not isinstance(value, str) or not value.strip():
            raise InvalidOperationError(f"{field_name} must be a non-empty string.")

    @property
    def full_name(self) -> str:
        """Return ``"Last First Middle"`` with the middle name omitted if absent."""
        parts = [self.last_name, self.first_name]
        if self.middle_name:
            parts.append(self.middle_name)
        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the owner into a plain dictionary (dates as ISO strings)."""
        return {
            "first_name": self.first_name,
            "last_name": self.last_name,
            "middle_name": self.middle_name,
            "birth_date": self.birth_date.isoformat(),
            "email": self.email,
            "phone": self.phone,
        }

    def __str__(self) -> str:
        return self.full_name
