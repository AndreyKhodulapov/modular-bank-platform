import dataclasses
from datetime import datetime, timedelta

import pytest

from exceptions import InvalidOperationError
from services import ClientSession, SessionStore

ISSUED_AT = datetime(2026, 9, 24, 14, 0)
TTL = timedelta(minutes=30)


@pytest.fixture
def session(owner):
    return ClientSession.issue(owner.client_id, issued_at=ISSUED_AT, ttl=TTL)


@pytest.fixture
def store(session, owner):
    store = SessionStore()
    store.add(session, owner)
    return store


def test_issued_session_lasts_its_ttl(session, owner):
    assert session.client_id == owner.client_id
    assert (session.issued_at, session.expires_at) == (ISSUED_AT, ISSUED_AT + TTL)


def test_every_session_has_its_own_id_and_token(session, owner):
    other = ClientSession.issue(owner.client_id, issued_at=ISSUED_AT, ttl=TTL)
    assert other.session_id != session.session_id
    assert other.token != session.token


def test_session_is_immutable(session):
    with pytest.raises(dataclasses.FrozenInstanceError):
        session.expires_at = ISSUED_AT + timedelta(days=1)


def test_token_is_not_shown(session):
    assert session.token not in repr(session)
    assert session.session_id in repr(session)
    assert session.token not in str(session)


def test_session_prints_as_a_line(session, owner):
    assert str(session) == f"session {session.session_id[:8]} of client {owner.client_id[:8]}, valid until 09-24 14:30"


@pytest.mark.parametrize(
    ("now", "expired"),
    [(ISSUED_AT + TTL - timedelta(seconds=1), False), (ISSUED_AT + TTL, True)],
)
def test_session_expires_at_its_end(session, now, expired):
    assert session.is_expired(now) is expired


def test_store_keeps_the_hash_of_the_token_only(store, session):
    [stored] = store._sessions.values()
    assert session.token not in vars(stored).values()
    assert session.token not in repr(stored)


def test_store_finds_the_client_of_a_session_as_issued(store, session, owner):
    assert session.session_id in store
    assert store.find(session) is owner


@pytest.mark.parametrize(
    "change",
    [
        {"token": "guessed-token"},
        {"expires_at": ISSUED_AT + timedelta(days=1)},
        {"issued_at": ISSUED_AT + timedelta(minutes=1)},
        {"client_id": "someone-else"},
        {"session_id": "unknown"},
        {"token": None},
    ],
)
def test_store_does_not_find_a_changed_session(store, session, change):
    assert store.find(dataclasses.replace(session, **change)) is None


def test_store_rejects_a_session_of_another_client(owner, make_client):
    session = ClientSession.issue(make_client("Boris").client_id, issued_at=ISSUED_AT, ttl=TTL)
    with pytest.raises(InvalidOperationError):
        SessionStore().add(session, owner)


def test_store_rejects_the_same_session_twice(store, session, owner):
    with pytest.raises(InvalidOperationError):
        store.add(session, owner)


def test_remove_closes_one_session(store, session):
    assert store.remove(session.session_id)
    assert not store.remove(session.session_id)
    assert store.find(session) is None


def test_remove_client_closes_only_their_sessions(store, session, owner, make_client):
    second = ClientSession.issue(owner.client_id, issued_at=ISSUED_AT, ttl=TTL)
    store.add(second, owner)
    boris = make_client("Boris")
    others = ClientSession.issue(boris.client_id, issued_at=ISSUED_AT, ttl=TTL)
    store.add(others, boris)
    assert store.remove_client(owner.client_id) == 2
    assert (store.find(session), store.find(second), store.find(others)) == (None, None, boris)
    assert len(store) == 1
