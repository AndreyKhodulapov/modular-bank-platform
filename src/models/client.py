"""Bank client model."""

import re
from datetime import date, datetime
from typing import Any

from exceptions import InvalidOperationError
from models.enums import ClientStatus
from utils import resolve_identifier


class Client:
    """A person served by the bank: personal data, access status and account numbers.

    Personal data is validated once and exposed through read-only properties.
    Only the status and the list of account numbers change over the client's
    lifetime, and only through dedicated methods. Two clients are equal when
    they share a ``client_id``, not when their personal data coincides.
    """

    MIN_AGE = 18
    EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
    PHONE_PATTERN = re.compile(r"\+?[0-9]{10,15}")
    PHONE_FORMAT = "10-15 digits with optional leading '+'"

    def __init__(
        self,
        *,
        first_name: str,
        last_name: str,
        birth_date: date,
        email: str,
        phone: str,
        middle_name: str | None = None,
        client_id: str | None = None,
        today: date | None = None,
    ) -> None:
        """Validate and store the client's data.

        ``today`` is the reference date for the age check; it defaults to the
        current date and exists so that tests can pin the calendar.
        """
        self._first_name = self._validate_name("first_name", first_name)
        self._last_name = self._validate_name("last_name", last_name)
        self._middle_name = None if middle_name is None else self._validate_name("middle_name", middle_name)
        self._birth_date = self._validate_birth_date(birth_date, today if today is not None else date.today())
        self._email = self._validate_contact("email", email, self.EMAIL_PATTERN)
        self._phone = self._validate_contact("phone", phone, self.PHONE_PATTERN, self.PHONE_FORMAT)
        self._client_id = resolve_identifier(client_id, field="client_id")
        self._status = ClientStatus.ACTIVE
        self._account_ids: list[str] = []

    @staticmethod
    def _validate_name(field: str, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise InvalidOperationError(f"{field} must be a non-empty string.")
        return value.strip()

    @staticmethod
    def _ensure_plain_date(field: str, value: object) -> None:
        # datetime is a subclass of date but cannot be compared with a plain date
        if not isinstance(value, date) or isinstance(value, datetime):
            raise InvalidOperationError(f"{field} must be a datetime.date.")

    @classmethod
    def _validate_birth_date(cls, birth_date: object, today: object) -> date:
        cls._ensure_plain_date("birth_date", birth_date)
        cls._ensure_plain_date("today", today)
        if birth_date > today:
            raise InvalidOperationError("birth_date cannot be in the future.")
        age = cls._full_years(birth_date, today)
        if age < cls.MIN_AGE:
            raise InvalidOperationError(f"Client must be at least {cls.MIN_AGE} years old, got {age}.")
        return birth_date

    @staticmethod
    def _validate_contact(field: str, value: object, pattern: re.Pattern[str], expected: str | None = None) -> str:
        if not isinstance(value, str) or not pattern.fullmatch(value):
            hint = f" (expected {expected})" if expected else ""
            raise InvalidOperationError(f"Invalid {field}: {value!r}{hint}.")
        return value

    @staticmethod
    def _full_years(birth_date: date, on: date) -> int:
        years = on.year - birth_date.year
        # a person born on 29 February becomes a year older on 1 March in non-leap years
        if (on.month, on.day) < (birth_date.month, birth_date.day):
            years -= 1
        return years

    @property
    def client_id(self) -> str:
        return self._client_id

    @property
    def first_name(self) -> str:
        return self._first_name

    @property
    def last_name(self) -> str:
        return self._last_name

    @property
    def middle_name(self) -> str | None:
        return self._middle_name

    @property
    def birth_date(self) -> date:
        return self._birth_date

    @property
    def email(self) -> str:
        return self._email

    @property
    def phone(self) -> str:
        return self._phone

    @property
    def full_name(self) -> str:
        """Return ``"Last First Middle"`` with the middle name omitted if absent."""
        parts = [self._last_name, self._first_name]
        if self._middle_name:
            parts.append(self._middle_name)
        return " ".join(parts)

    @property
    def contacts(self) -> dict[str, str]:
        return {"email": self._email, "phone": self._phone}

    @property
    def status(self) -> ClientStatus:
        return self._status

    @property
    def is_blocked(self) -> bool:
        return self._status is ClientStatus.BLOCKED

    def block(self) -> None:
        if self.is_blocked:
            raise InvalidOperationError(f"Client {self._client_id} is already blocked.")
        self._status = ClientStatus.BLOCKED

    def unblock(self) -> None:
        if not self.is_blocked:
            raise InvalidOperationError(f"Client {self._client_id} is not blocked.")
        self._status = ClientStatus.ACTIVE

    @property
    def account_ids(self) -> list[str]:
        """A copy of the client's account numbers; mutating it does not affect the client."""
        return list(self._account_ids)

    def add_account_id(self, account_id: str) -> None:
        """Record an account number opened for this client.

        The bank calls this when it opens an account, so an account created
        directly (outside the bank) is not listed here.
        """
        if account_id in self._account_ids:
            raise InvalidOperationError(f"Account {account_id} is already linked to client {self._client_id}.")
        self._account_ids.append(account_id)

    def to_summary_dict(self) -> dict[str, Any]:
        """Identify the client and give their contacts; embedded into account snapshots."""
        return {"client_id": self._client_id, "full_name": self.full_name, **self.contacts}

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Client):
            return NotImplemented
        return self._client_id == other._client_id

    def __hash__(self) -> int:
        return hash(self._client_id)

    def __repr__(self) -> str:
        return f"Client(client_id={self._client_id!r}, full_name={self.full_name!r}, status={self._status.value!r})"

    def __str__(self) -> str:
        return f"{self.full_name} | {self._email} | {self._phone} | {self._status.value}"
