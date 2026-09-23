"""Shared fixtures for unit and integration tests."""

from datetime import date

import pytest

from models import AccountStatus, BankAccount, Currency, Owner


@pytest.fixture
def owner() -> Owner:
    return Owner(
        first_name="Anna",
        last_name="Smirnova",
        birth_date=date(1985, 3, 2),
        email="anna@example.com",
        phone="+79990001122",
    )


@pytest.fixture
def active_account(owner: Owner) -> BankAccount:
    return BankAccount(owner=owner, currency=Currency.EUR, initial_balance=100)


@pytest.fixture
def frozen_account(owner: Owner) -> BankAccount:
    return BankAccount(
        owner=owner,
        currency=Currency.EUR,
        status=AccountStatus.FROZEN,
        initial_balance=100,
    )


@pytest.fixture
def closed_account(owner: Owner) -> BankAccount:
    return BankAccount(
        owner=owner,
        currency=Currency.EUR,
        status=AccountStatus.CLOSED,
    )
