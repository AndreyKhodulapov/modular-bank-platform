"""Account owner model."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from exceptions import InvalidOperationError


@dataclass(frozen=True, kw_only=True)
class Owner:
    """Immutable personal data of an account holder.

    The class is frozen: once an owner is created its data cannot be altered
    by accident, which keeps the account's ``owner`` attribute trustworthy.
    """

    EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
    PHONE_PATTERN = re.compile(r"\+?\d{10,15}")

    first_name: str
    last_name: str
    middle_name: str | None = None
    birth_date: date
    email: str
    phone: str

    def __post_init__(self) -> None:
        for field_name in ("first_name", "last_name", "middle_name"):
            value = getattr(self, field_name)
            if field_name == "middle_name" and value is None:
                continue
            self._validate_name(field_name, value)
            # the dataclass is frozen, so normalisation has to bypass __setattr__
            object.__setattr__(self, field_name, value.strip())

        # datetime is a subclass of date but cannot be compared with a plain date
        if not isinstance(self.birth_date, date) or isinstance(self.birth_date, datetime):
            raise InvalidOperationError("birth_date must be a datetime.date.")
        if self.birth_date > date.today():
            raise InvalidOperationError("birth_date cannot be in the future.")

        if not isinstance(self.email, str) or not self.EMAIL_PATTERN.fullmatch(self.email):
            raise InvalidOperationError(f"Invalid email address: {self.email!r}.")

        if not isinstance(self.phone, str) or not self.PHONE_PATTERN.fullmatch(self.phone):
            raise InvalidOperationError(
                f"Invalid phone number: {self.phone!r} (expected 10-15 digits with optional leading '+')."
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
