"""Security rules of the bank: passwords, login lockout, the night window and suspicious activities."""

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
from services.audit_log import AuditCategory, AuditEvent, AuditLevel, AuditLog


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
    """A security event of the audit log, seen from the security side; immutable."""

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
    - blocks a client after ``MAX_FAILED_ATTEMPTS`` failed logins in a row;
    - forbids restricted operations inside ``[NIGHT_START, NIGHT_END)``;
    - records suspicious activities in the audit log (category
      ``security``; a blocked client is ``CRITICAL``, the rest ``WARNING``).

    The current time comes from the injected ``clock`` (``datetime.now`` by
    default), which keeps the night rule testable. The audit log is injected
    too, so the bank, the processor and the guard can share one journal.
    """

    MAX_FAILED_ATTEMPTS = 3
    NIGHT_START = time(0, 0)
    NIGHT_END = time(5, 0)
    LARGE_OPERATION_THRESHOLD = Decimal("500000.00")  # in the bank's base currency
    MIN_PASSWORD_LENGTH = 8
    HASH_ITERATIONS = 100_000
    CRITICAL_REASONS = frozenset({SuspicionReason.CLIENT_BLOCKED})
    REASON_NAMES = frozenset(reason.value for reason in SuspicionReason)

    def __init__(self, clock: Callable[[], datetime] = datetime.now, audit_log: AuditLog | None = None) -> None:
        if audit_log is not None and not isinstance(audit_log, AuditLog):
            raise InvalidOperationError("audit_log must be an AuditLog instance.")
        self._clock = clock
        self._passwords: dict[str, _PasswordHash] = {}
        self._audit_log = audit_log if audit_log is not None else AuditLog()

    def now(self) -> datetime:
        return self._clock()

    @property
    def audit_log(self) -> AuditLog:
        return self._audit_log

    def ensure_daytime(self, action: str, *, client_id: str | None = None, account_id: str | None = None) -> None:
        """Reject ``action`` and record the attempt when it happens in the night window."""
        moment = self.now()
        if not self.NIGHT_START <= moment.time() < self.NIGHT_END:
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

    def _password_matches(self, client_id: str, password: str) -> bool:
        stored = self._passwords.get(client_id)
        if stored is None:
            return False
        # constant-time comparison does not reveal how many leading bytes matched
        return hmac.compare_digest(stored.digest, self._hash(password, stored.salt))

    def authenticate(self, client: Client, password: str) -> None:
        """Check ``password``; the ``MAX_FAILED_ATTEMPTS``-th failure in a row blocks the client.

        Raises ``ClientBlockedError`` for a blocked client (even with the right
        password) and ``AuthenticationError`` for a wrong password.
        """
        if not isinstance(password, str):
            raise InvalidOperationError("password must be a string.")
        client_id = client.client_id
        if client.is_blocked:
            self.flag(SuspicionReason.BLOCKED_CLIENT_ACTIVITY, "login attempt by a blocked client", client_id=client_id)
            raise ClientBlockedError(client_id)

        if self._password_matches(client_id, password):
            client.reset_failed_logins()
            return

        failures = client.record_failed_login()
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

    def review_amount(
        self, amount_in_base: Decimal, action: str, *, client_id: str | None = None, account_id: str | None = None
    ) -> None:
        """Record ``action`` if its amount reaches the threshold; the operation itself is not stopped."""
        if amount_in_base < self.LARGE_OPERATION_THRESHOLD:
            return
        self.flag(
            SuspicionReason.LARGE_OPERATION,
            f"{action} of {amount_in_base} in base currency (threshold {self.LARGE_OPERATION_THRESHOLD})",
            client_id=client_id,
            account_id=account_id,
        )

    def flag(
        self,
        reason: SuspicionReason,
        details: str,
        *,
        client_id: str | None = None,
        account_id: str | None = None,
    ) -> SuspiciousActivity:
        level = AuditLevel.CRITICAL if reason in self.CRITICAL_REASONS else AuditLevel.WARNING
        event = self._audit_log.record(
            level,
            AuditCategory.SECURITY,
            reason.value,
            details,
            timestamp=self.now(),
            client_id=client_id,
            account_id=account_id,
        )
        return self._to_activity(event)

    @staticmethod
    def _to_activity(event: AuditEvent) -> SuspiciousActivity:
        return SuspiciousActivity(
            timestamp=event.timestamp,
            reason=SuspicionReason(event.event),
            details=event.message,
            client_id=event.client_id,
            account_id=event.account_id,
        )

    @property
    def suspicious_activities(self) -> list[SuspiciousActivity]:
        """The security events of the audit log, in the order they were recorded.

        Only events named after a ``SuspicionReason`` are included: other
        code may record its own security events in the shared log.
        """
        return [
            self._to_activity(event)
            for event in self._audit_log.filter(category=AuditCategory.SECURITY)
            if event.event in self.REASON_NAMES
        ]
