"""Security rules of the bank: passwords, login lockout, the night window and the audit log."""

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
from enum import Enum

from exceptions import AuthenticationError, ClientBlockedError, InvalidOperationError, OperationTimeRestrictedError
from models.client import Client


class SuspicionReason(Enum):
    """Why an action was recorded as suspicious."""

    FAILED_LOGIN = "failed_login"
    CLIENT_BLOCKED = "client_blocked"
    UNKNOWN_CLIENT_LOGIN = "unknown_client_login"
    BLOCKED_CLIENT_ACTIVITY = "blocked_client_activity"
    NIGHT_OPERATION = "night_operation"
    INACTIVE_ACCOUNT_OPERATION = "inactive_account_operation"
    LARGE_OPERATION = "large_operation"


@dataclass(frozen=True)
class SuspiciousActivity:
    """One entry of the audit log; immutable once recorded."""

    timestamp: datetime
    reason: SuspicionReason
    details: str
    client_id: str | None = None
    account_id: str | None = None


@dataclass(frozen=True)
class _PasswordHash:
    salt: bytes
    digest: bytes


class SecurityGuard:
    """Owns everything security-related so that ``Bank`` only coordinates.

    - stores password hashes (PBKDF2-HMAC-SHA256 with a random salt), never
      the passwords themselves;
    - counts failed logins and blocks a client after ``MAX_FAILED_ATTEMPTS``;
    - forbids restricted operations inside ``[NIGHT_START, NIGHT_END)``;
    - keeps an append-only log of suspicious activities.

    The current time comes from the injected ``clock`` (``datetime.now`` by
    default), which keeps the night rule testable.
    """

    MAX_FAILED_ATTEMPTS = 3
    NIGHT_START = time(0, 0)
    NIGHT_END = time(5, 0)
    LARGE_OPERATION_THRESHOLD = Decimal("500000.00")  # in the bank's base currency
    MIN_PASSWORD_LENGTH = 8
    HASH_ITERATIONS = 100_000

    def __init__(self, clock: Callable[[], datetime] = datetime.now) -> None:
        self._clock = clock
        self._passwords: dict[str, _PasswordHash] = {}
        self._failed_attempts: dict[str, int] = {}
        self._log: list[SuspiciousActivity] = []

    def now(self) -> datetime:
        return self._clock()

    @classmethod
    def _is_night_at(cls, moment: datetime) -> bool:
        return cls.NIGHT_START <= moment.time() < cls.NIGHT_END

    def is_night(self) -> bool:
        return self._is_night_at(self.now())

    def ensure_daytime(self, action: str, *, client_id: str | None = None, account_id: str | None = None) -> None:
        """Reject ``action`` and record the attempt when it happens in the night window."""
        moment = self.now()
        if not self._is_night_at(moment):
            return
        window = f"{self.NIGHT_START:%H:%M} and {self.NIGHT_END:%H:%M}"
        self.flag(
            SuspicionReason.NIGHT_OPERATION,
            f"{action} attempted at {moment:%H:%M}",
            client_id=client_id,
            account_id=account_id,
        )
        raise OperationTimeRestrictedError(action, window)

    @classmethod
    def _hash(cls, password: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, cls.HASH_ITERATIONS)

    def register_password(self, client_id: str, password: str) -> None:
        if not isinstance(password, str) or len(password) < self.MIN_PASSWORD_LENGTH:
            raise InvalidOperationError(f"password must be a string of at least {self.MIN_PASSWORD_LENGTH} characters.")
        salt = secrets.token_bytes(16)
        self._passwords[client_id] = _PasswordHash(salt=salt, digest=self._hash(password, salt))

    def _password_matches(self, client_id: str, password: object) -> bool:
        stored = self._passwords.get(client_id)
        if stored is None or not isinstance(password, str):
            return False
        # constant-time comparison does not reveal how many leading bytes matched
        return hmac.compare_digest(stored.digest, self._hash(password, stored.salt))

    def failed_attempts(self, client_id: str) -> int:
        return self._failed_attempts.get(client_id, 0)

    def authenticate(self, client: Client, password: object) -> None:
        """Check ``password``; the ``MAX_FAILED_ATTEMPTS``-th failure in a row blocks the client.

        Raises ``ClientBlockedError`` for a blocked client (even with the right
        password) and ``AuthenticationError`` for a wrong password.
        """
        client_id = client.client_id
        if client.is_blocked:
            self.flag(SuspicionReason.BLOCKED_CLIENT_ACTIVITY, "login attempt by a blocked client", client_id=client_id)
            raise ClientBlockedError(client_id)

        if self._password_matches(client_id, password):
            self._failed_attempts.pop(client_id, None)
            return

        failures = self.failed_attempts(client_id) + 1
        self._failed_attempts[client_id] = failures
        self.flag(
            SuspicionReason.FAILED_LOGIN,
            f"failed login attempt {failures} of {self.MAX_FAILED_ATTEMPTS}",
            client_id=client_id,
        )
        if failures >= self.MAX_FAILED_ATTEMPTS:
            client.block()
            self.flag(
                SuspicionReason.CLIENT_BLOCKED,
                f"blocked after {failures} failed login attempts",
                client_id=client_id,
            )
            raise ClientBlockedError(client_id)
        raise AuthenticationError(client_id, attempts_left=self.MAX_FAILED_ATTEMPTS - failures)

    def reset_failed_attempts(self, client_id: str) -> None:
        self._failed_attempts.pop(client_id, None)

    def review_amount(
        self, amount_in_base: Decimal, action: str, *, client_id: str | None = None, account_id: str | None = None
    ) -> bool:
        """Record ``action`` if its amount reaches the threshold; the operation itself is not stopped."""
        if amount_in_base < self.LARGE_OPERATION_THRESHOLD:
            return False
        self.flag(
            SuspicionReason.LARGE_OPERATION,
            f"{action} of {amount_in_base} in base currency (threshold {self.LARGE_OPERATION_THRESHOLD})",
            client_id=client_id,
            account_id=account_id,
        )
        return True

    def flag(
        self,
        reason: SuspicionReason,
        details: str,
        *,
        client_id: str | None = None,
        account_id: str | None = None,
    ) -> SuspiciousActivity:
        activity = SuspiciousActivity(
            timestamp=self.now(),
            reason=reason,
            details=details,
            client_id=client_id,
            account_id=account_id,
        )
        self._log.append(activity)
        return activity

    @property
    def suspicious_activities(self) -> list[SuspiciousActivity]:
        """A copy of the log in the order the activities were recorded."""
        return list(self._log)
