"""Shared fixtures for unit and integration tests."""

from collections.abc import Callable
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
    Transaction,
)
from services import Bank, SecurityGuard, TransactionProcessor, TransactionQueue
from utils import ManualClock


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
def make_client() -> Callable[..., Client]:
    """Factory of valid clients that differ by name; ``birth_date`` and ``last_name`` can be overridden."""

    def factory(first_name: str, last_name: str = "Ivanova", birth_date: date = date(1990, 1, 1)) -> Client:
        return Client(
            first_name=first_name,
            last_name=last_name,
            birth_date=birth_date,
            email=f"{first_name.lower()}@example.com",
            phone="+79990002233",
            today=date(2026, 9, 24),
        )

    return factory


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
    return ManualClock(datetime(2026, 9, 24, 14, 0))


@pytest.fixture
def security(clock: ManualClock) -> SecurityGuard:
    return SecurityGuard(clock=clock)


@pytest.fixture
def bank(security: SecurityGuard) -> Bank:
    return Bank(security=security)


@pytest.fixture
def password() -> str:
    return "correct-horse-1"


@pytest.fixture
def client(bank: Bank, owner: Client, password: str) -> Client:
    """``owner`` registered in ``bank`` with ``password``."""
    return bank.add_client(owner, password)


@pytest.fixture
def queue(bank: Bank) -> TransactionQueue:
    return TransactionQueue(clock=bank.now)


@pytest.fixture
def processor(bank: Bank) -> TransactionProcessor:
    return TransactionProcessor(bank)


@pytest.fixture
def parties(bank: Bank, make_client: Callable[..., Client]) -> tuple[BankAccount, BankAccount, BankAccount]:
    """Anna with 10 000 RUB, Boris with 100 USD and Vera with a frozen RUB account."""
    anna, boris, vera = (bank.add_client(make_client(name), "password-1") for name in ("Anna", "Boris", "Vera"))
    anna_rub = bank.open_account(anna.client_id, currency="RUB", initial_balance=10_000)
    boris_usd = bank.open_account(boris.client_id, currency="USD", initial_balance=100)
    vera_rub = bank.open_account(vera.client_id, currency="RUB")
    bank.freeze_account(vera_rub.account_id)
    return anna_rub, boris_usd, vera_rub


@pytest.fixture
def processed(bank: Bank, clock: ManualClock, parties: tuple[BankAccount, BankAccount, BankAccount]) -> None:
    """Six transactions: three completed, one failed, one blocked by risk control, one cancelled."""
    anna_rub, boris_usd, vera_rub = parties
    queue = TransactionQueue(clock=bank.now, audit_log=bank.audit_log)
    anna, boris = anna_rub.account_id, boris_usd.account_id
    for kind, amount, currency, params in (
        ("deposit", 1_000, "RUB", {"recipient_id": anna}),
        ("transfer", 50, "USD", {"sender_id": boris, "recipient_id": anna}),  # 4 500 RUB
        ("external_transfer", 1_000, "RUB", {"sender_id": anna, "recipient_id": "DE-1"}),  # the minimal fee, 50 RUB
        ("transfer", 100, "RUB", {"sender_id": anna, "recipient_id": vera_rub.account_id}),  # frozen recipient
        ("transfer", 2_000_000, "RUB", {"sender_id": anna, "recipient_id": boris}),  # high risk
    ):
        queue.add(Transaction(kind, amount, currency, created_at=clock(), **params))
    typo = queue.add(Transaction("withdrawal", 10, "RUB", sender_id=anna, created_at=clock()))
    queue.cancel(typo.transaction_id)
    TransactionProcessor(bank).process_queue(queue)
