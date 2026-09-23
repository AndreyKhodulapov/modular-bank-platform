"""End-to-end scenarios that combine several models and operations."""

import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from exceptions import AccountFrozenError, BankError, InsufficientFundsError
from models import AccountStatus, BankAccount, Currency

ROOT = Path(__file__).resolve().parents[2]


def test_day_one_scenario(owner):
    active = BankAccount(owner=owner, currency=Currency.RUB, initial_balance="1000")
    frozen = BankAccount(
        owner=owner,
        currency="USD",
        account_id="ACC-2024-0042",
        status=AccountStatus.FROZEN,
        initial_balance=250.50,
    )

    with pytest.raises(AccountFrozenError):
        frozen.deposit(100)
    with pytest.raises(AccountFrozenError):
        frozen.withdraw(50)
    assert frozen.balance == Decimal("250.50")

    active.deposit(500.25)
    active.withdraw("300")
    assert active.balance == Decimal("1200.25")
    assert str(active).endswith("| active | 1200.25 RUB")


def test_sequence_of_operations_keeps_exact_decimal_balance(owner):
    account = BankAccount(owner=owner, currency="EUR")
    for _ in range(10):
        account.deposit(0.1)
    for _ in range(3):
        account.withdraw(0.3)
    # Floats would accumulate rounding noise here; Decimal stays exact.
    assert account.balance == Decimal("0.10")


def test_failed_operations_never_change_balance(owner):
    account = BankAccount(owner=owner, currency="CNY", initial_balance=50)
    attempts = [
        lambda: account.withdraw(60),
        lambda: account.deposit(-1),
        lambda: account.withdraw("abc"),
    ]
    for attempt in attempts:
        with pytest.raises(BankError):
            attempt()
    assert account.balance == Decimal("50.00")


def test_insufficient_funds_reports_state(owner):
    account = BankAccount(owner=owner, currency="KZT", initial_balance=10)
    with pytest.raises(InsufficientFundsError) as info:
        account.withdraw(10.01)
    assert (info.value.requested, info.value.available) == (
        Decimal("10.01"),
        Decimal("10.00"),
    )


def test_demo_script_runs_without_errors():
    result = subprocess.run(
        [sys.executable, str(ROOT / "src" / "main.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "[rejected] deposit 100 USD: AccountFrozenError" in result.stdout
    assert "[ok]       withdraw 300 RUB" in result.stdout
