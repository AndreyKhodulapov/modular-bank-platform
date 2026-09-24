"""End-to-end scenarios that combine several models and operations."""

import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from exceptions import AccountFrozenError, BankError, InsufficientFundsError
from models import (
    AbstractAccount,
    BankAccount,
    InvestmentAccount,
    PremiumAccount,
    SavingsAccount,
)


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
        lambda: account.deposit(10_000_000),
        lambda: frozen.deposit(1),
        lambda: closed.deposit(1),
    ]
    for attempt in attempts:
        with pytest.raises(BankError):
            attempt()
    assert (account.balance, frozen.balance, closed.balance) == (Decimal("50.00"),) * 3


def test_same_withdrawal_behaves_differently_per_account_type(owner):
    """Client code sees one interface; each type applies its own funds rule."""
    accounts: list[AbstractAccount] = [
        BankAccount(owner=owner, currency="EUR", initial_balance=100),
        SavingsAccount(owner=owner, currency="EUR", initial_balance=100, min_balance=30),
        PremiumAccount(owner=owner, currency="EUR", initial_balance=100, withdrawal_fee=5),
        PremiumAccount(owner=owner, currency="EUR", initial_balance=100, overdraft_limit=10, withdrawal_fee=5),
        InvestmentAccount(owner=owner, currency="EUR", initial_balance=100),
    ]
    accounts[-1].invest("stocks", 50)

    outcomes = []
    for account in accounts:
        try:
            outcomes.append(("ok", account.withdraw(100)))
        except InsufficientFundsError as error:
            outcomes.append(("rejected", error.available))

    assert outcomes == [
        ("ok", Decimal("0.00")),  # regular: whole balance leaves
        ("rejected", Decimal("70.00")),  # savings: only 70 above the minimum
        ("rejected", Decimal("100.00")),  # premium without overdraft: 105 with fee exceeds 100
        ("ok", Decimal("-5.00")),  # premium with overdraft: fee pushes into overdraft
        ("rejected", Decimal("50.00")),  # investment: half is locked in the portfolio
    ]


@pytest.mark.parametrize("account_type", [BankAccount, SavingsAccount, PremiumAccount, InvestmentAccount])
def test_every_account_type_honours_base_contract(owner, account_type):
    frozen = account_type(owner=owner, currency="RUB", status="frozen", initial_balance=10)
    with pytest.raises(AccountFrozenError):
        frozen.withdraw(1)

    active = account_type(owner=owner, currency="RUB", account_id="X-1234", initial_balance=10)
    info = active.get_account_info()
    assert info["account_type"] == account_type.__name__
    assert {"account_id", "owner", "status", "currency", "balance"} <= info.keys()
    assert str(active).startswith(f"{account_type.__name__} | Smirnova Anna | ****1234 | active | 10.00 RUB")


def test_demo_script_runs_without_errors():
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, str(root / "src" / "main.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "STAGE 1: Accounts Basic" in completed.stdout
    assert "STAGE 2: Accounts Advanced" in completed.stdout
    assert "[rejected] deposit 100 USD: AccountFrozenError" in completed.stdout
    assert "[ok]       withdraw 300 RUB" in completed.stdout
    assert "[ok]       apply monthly interest: interest 150.00" in completed.stdout
    assert "[ok]       withdraw 2_500 USD (goes into overdraft): balance -1505.00" in completed.stdout
    assert "project yearly growth (stocks 10%, bonds 4%, etf 7%): growth 500.00" in completed.stdout
