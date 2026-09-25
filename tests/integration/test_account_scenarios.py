"""End-to-end scenarios that combine several models and operations."""

import json
import os
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
        ("rejected", Decimal("95.00")),  # premium without overdraft: 100 minus the 5 fee
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
    assert {"account_id", "account_type", "owner", "status", "currency", "balance"} <= info.keys()


def test_demo_script_runs_without_errors(tmp_path):
    root = Path(__file__).resolve().parents[2]
    audit_path = tmp_path / "audit.jsonl"  # keeps the test run out of the project's logs/ folder
    completed = subprocess.run(
        [sys.executable, str(root / "src" / "main.py")],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ | {"BANK_AUDIT_LOG": str(audit_path)},
    )
    assert completed.returncode == 0, completed.stderr
    assert "STAGE 1: Accounts Basic" in completed.stdout
    assert "STAGE 2: Accounts Advanced" in completed.stdout
    assert "[rejected] deposit 100 USD: AccountFrozenError" in completed.stdout
    assert "[ok]       withdraw 300 RUB" in completed.stdout
    assert "[ok]       apply monthly interest: interest 150.00" in completed.stdout
    assert "[ok]       withdraw 2_500 USD (goes into overdraft): balance -1505.00" in completed.stdout
    assert "project yearly growth (stocks 10%, bonds 4%, etf 7%): growth 500.00" in completed.stdout
    assert "STAGE 3: Bank System" in completed.stdout
    assert "[rejected] register a 16-year-old client: InvalidOperationError" in completed.stdout
    assert "[rejected] Oleg, wrong password #3: ClientBlockedError" in completed.stdout
    assert "[rejected] withdraw 5_000 KZT: OperationTimeRestrictedError" in completed.stdout
    assert "(min_balance does not hold money back): payout 300000.00" in completed.stdout
    assert "total balance: 1502100.00 RUB" in completed.stdout
    assert "STAGE 4: Transactions" in completed.stdout
    assert "[ok]       cancel 'typo': status cancelled" in completed.stdout
    assert "[failed] to frozen: AccountFrozenError" in completed.stdout
    assert "night      completed attempts 3" in completed.stdout
    assert "fees collected: 180.00 RUB" in completed.stdout
    assert "STAGE 5: Audit and Risk" in completed.stdout
    assert f"file: {audit_path}" in completed.stdout
    assert "[failed   ] huge abroad      high   score  90" in completed.stdout
    events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert f"this run added {len(events)} events" in completed.stdout
    assert sum(event["event"] == "operation_blocked" for event in events) == 2
