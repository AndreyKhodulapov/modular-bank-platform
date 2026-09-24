"""End-to-end scenarios that combine several models and operations."""

import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from exceptions import AccountFrozenError, BankError
from models import AccountStatus, BankAccount, Currency


def test_frozen_and_active_accounts_scenario(owner):
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
    assert account.balance == Decimal("0.10")


def test_every_domain_error_is_a_bank_error_and_keeps_balance(owner):
    account = BankAccount(owner=owner, currency="CNY", initial_balance=50)
    frozen = BankAccount(owner=owner, currency="CNY", status="frozen", initial_balance=50)
    closed = BankAccount(owner=owner, currency="CNY", status="closed", initial_balance=50)
    attempts = [
        lambda: account.withdraw(60),
        lambda: account.deposit(-1),
        lambda: account.withdraw("abc"),
        lambda: frozen.deposit(1),
        lambda: closed.deposit(1),
    ]
    for attempt in attempts:
        with pytest.raises(BankError):
            attempt()
    assert (account.balance, frozen.balance, closed.balance) == (Decimal("50.00"),) * 3


def test_demo_script_runs_without_errors():
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, str(root / "src" / "main.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "[rejected] deposit 100 USD: AccountFrozenError" in completed.stdout
    assert "[ok]       withdraw 300 RUB" in completed.stdout
