"""Client sessions: what a successful login gives a client, and where the bank keeps it."""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from exceptions import InvalidOperationError
from models.client import Client


@dataclass(frozen=True)
class ClientSession:
    """Proof of a successful login, valid until ``expires_at``; immutable.

    ``session_id`` names the session and may be written to the audit log;
    ``token`` is the secret that proves the holder logged in, so it is left
    out of ``repr`` and the bank keeps only its hash. A session is honoured
    only as the bank issued it: changing any field makes it unknown.
    """

    session_id: str
    client_id: str
    issued_at: datetime
    expires_at: datetime
    token: str = field(repr=False)

    @classmethod
    def issue(cls, client_id: str, *, issued_at: datetime, ttl: timedelta) -> "ClientSession":
        return cls(
            session_id=str(uuid.uuid4()),
            client_id=client_id,
            issued_at=issued_at,
            expires_at=issued_at + ttl,
            token=secrets.token_urlsafe(32),
        )

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at


def _digest(token: str) -> bytes:
    # a random token needs no salt: there is no dictionary to guess it from
    return hashlib.sha256(token.encode()).digest()


@dataclass(frozen=True)
class _StoredSession:
    client: Client
    issued_at: datetime
    expires_at: datetime
    token_digest: bytes


class SessionStore:
    """The open sessions by ``session_id``; a plain store without a clock or rules.

    Keeps the hash of a session's token, never the token itself, and the
    client the session belongs to. Whether a session is still valid is
    decided by ``SecurityGuard``.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, _StoredSession] = {}

    def __len__(self) -> int:
        return len(self._sessions)

    def __contains__(self, session_id: object) -> bool:
        return session_id in self._sessions

    def add(self, session: ClientSession, client: Client) -> None:
        if session.client_id != client.client_id:
            raise InvalidOperationError(f"Session {session.session_id} belongs to another client.")
        if session.session_id in self._sessions:
            raise InvalidOperationError(f"Session {session.session_id} is already open.")
        self._sessions[session.session_id] = _StoredSession(
            client=client,
            issued_at=session.issued_at,
            expires_at=session.expires_at,
            token_digest=_digest(session.token),
        )

    def find(self, session: ClientSession) -> Client | None:
        """The client of ``session`` if it is open and presented exactly as issued; otherwise ``None``."""
        stored = self._sessions.get(session.session_id)
        if stored is None or not isinstance(session.token, str):
            return None
        issued_as = (stored.client.client_id, stored.issued_at, stored.expires_at)
        presented_as = (session.client_id, session.issued_at, session.expires_at)
        # constant-time comparison does not reveal how much of the token matched
        if issued_as != presented_as or not hmac.compare_digest(stored.token_digest, _digest(session.token)):
            return None
        return stored.client

    def remove(self, session_id: str) -> bool:
        """Close one session; ``False`` if it was not open."""
        return self._sessions.pop(session_id, None) is not None

    def remove_client(self, client_id: str) -> int:
        """Close every session of the client and return how many were open."""
        closing = [session_id for session_id, stored in self._sessions.items() if stored.client.client_id == client_id]
        for session_id in closing:
            del self._sessions[session_id]
        return len(closing)
