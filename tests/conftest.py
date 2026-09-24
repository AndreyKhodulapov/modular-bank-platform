"""Shared fixtures for unit and integration tests."""

from datetime import date

import pytest

from models import (
    AccountStatus,
    BankAccount,
    Currency,
    InvestmentAccount,
    Owner,
    PremiumAccount,
    SavingsAccount,
)


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


@pytest.fixture
def savings_account(owner: Owner) -> SavingsAccount:
    return SavingsAccount(
        owner=owner,
        currency="RUB",
        initial_balance=1000,
        min_balance=100,
        monthly_rate="0.015",
    )


@pytest.fixture
def premium_account(owner: Owner) -> PremiumAccount:
    return PremiumAccount(
        owner=owner,
        currency="USD",
        initial_balance=100,
        overdraft_limit=50,
        withdrawal_fee=5,
    )


@pytest.fixture
def investment_account(owner: Owner) -> InvestmentAccount:
    return InvestmentAccount(owner=owner, currency="EUR", initial_balance=1000)
