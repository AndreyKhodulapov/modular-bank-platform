"""Shared fixtures for unit and integration tests."""

from datetime import date, datetime

import pytest

from models import (
    AccountStatus,
    BankAccount,
    Client,
    Currency,
    InvestmentAccount,
    PremiumAccount,
    SavingsAccount,
)
from services import Bank, SecurityGuard
from utils import ManualClock

DAYTIME = datetime(2026, 9, 24, 14, 0)
PASSWORD = "correct-horse-1"


@pytest.fixture
def owner() -> Client:
    return Client(
        first_name="Anna",
        last_name="Smirnova",
        birth_date=date(1985, 3, 2),
        email="anna@example.com",
        phone="+79990001122",
    )


@pytest.fixture
def active_account(owner: Client) -> BankAccount:
    return BankAccount(owner=owner, currency=Currency.EUR, initial_balance=100)


@pytest.fixture
def frozen_account(owner: Client) -> BankAccount:
    return BankAccount(
        owner=owner,
        currency=Currency.EUR,
        status=AccountStatus.FROZEN,
        initial_balance=100,
    )


@pytest.fixture
def closed_account(owner: Client) -> BankAccount:
    return BankAccount(
        owner=owner,
        currency=Currency.EUR,
        status=AccountStatus.CLOSED,
    )


@pytest.fixture
def savings_account(owner: Client) -> SavingsAccount:
    return SavingsAccount(
        owner=owner,
        currency="RUB",
        initial_balance=1000,
        min_balance=100,
        monthly_rate="0.015",
    )


@pytest.fixture
def premium_account(owner: Client) -> PremiumAccount:
    return PremiumAccount(
        owner=owner,
        currency="USD",
        initial_balance=100,
        overdraft_limit=50,
        withdrawal_fee=5,
    )


@pytest.fixture
def investment_account(owner: Client) -> InvestmentAccount:
    return InvestmentAccount(owner=owner, currency="EUR", initial_balance=1000)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(DAYTIME)


@pytest.fixture
def security(clock: ManualClock) -> SecurityGuard:
    return SecurityGuard(clock=clock)


@pytest.fixture
def bank(security: SecurityGuard) -> Bank:
    return Bank(security=security)


@pytest.fixture
def client(bank: Bank, owner: Client) -> Client:
    """``owner`` registered in ``bank`` with ``PASSWORD``."""
    return bank.add_client(owner, PASSWORD)
