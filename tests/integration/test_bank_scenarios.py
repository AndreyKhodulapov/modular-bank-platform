"""End-to-end scenarios of the bank: several clients, accounts, logins and security rules."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from exceptions import (
    AccountFrozenError,
    AuthenticationError,
    ClientBlockedError,
    InvalidOperationError,
    OperationTimeRestrictedError,
)
from models import AccountStatus, Client
from services import SuspicionReason


def new_client(first_name: str, birth_date: date = date(1990, 5, 5)) -> Client:
    return Client(
        first_name=first_name,
        last_name="Test",
        birth_date=birth_date,
        email=f"{first_name.lower()}@example.com",
        phone="+79990001100",
        today=date(2026, 9, 24),
    )


def test_bank_day_from_registration_to_ranking(bank, clock):
    # several clients; a minor is refused before reaching the bank
    with pytest.raises(InvalidOperationError, match="at least 18"):
        new_client("Kid", birth_date=date(2010, 1, 1))
    anna = bank.add_client(new_client("Anna"), "anna-password")
    boris = bank.add_client(new_client("Boris"), "boris-password")

    # accounts of different types and currencies
    anna_rub = bank.open_account(anna.client_id, currency="RUB", initial_balance=20_000)
    anna_savings = bank.open_account(
        anna.client_id, "savings", currency="RUB", initial_balance=50_000, min_balance=10_000
    )
    boris_usd = bank.open_account(boris.client_id, "premium", currency="USD", initial_balance=1_000)
    assert anna.account_ids == [anna_rub.account_id, anna_savings.account_id]

    # login attempts: Boris gets blocked, his money is out of reach until he is unblocked
    assert bank.authenticate_client(anna.client_id, "anna-password") is anna
    for _ in range(2):
        with pytest.raises(AuthenticationError):
            bank.authenticate_client(boris.client_id, "not-his-password")
    with pytest.raises(ClientBlockedError):
        bank.authenticate_client(boris.client_id, "not-his-password")
    with pytest.raises(ClientBlockedError):
        bank.withdraw(boris_usd.account_id, 10)
    bank.unblock_client(boris.client_id)
    bank.authenticate_client(boris.client_id, "boris-password")
    assert bank.withdraw(boris_usd.account_id, 10) < Decimal("1000")

    # freezing stops operations until the account is unfrozen
    bank.freeze_account(anna_rub.account_id)
    with pytest.raises(AccountFrozenError):
        bank.withdraw(anna_rub.account_id, 100)
    bank.unfreeze_account(anna_rub.account_id)
    assert bank.withdraw(anna_rub.account_id, 100) == Decimal("19900.00")

    # nothing moves at night, and the attempts are recorded
    clock.moment = datetime(2026, 9, 25, 3, 0)
    with pytest.raises(OperationTimeRestrictedError):
        bank.deposit(anna_rub.account_id, 100)
    clock.moment = datetime(2026, 9, 25, 9, 0)
    bank.deposit(anna_rub.account_id, 100)

    # empty accounts close; the history keeps them
    bank.withdraw(anna_rub.account_id, 20_000)
    bank.close_account(anna_rub.account_id)
    assert bank.search_accounts(status=AccountStatus.CLOSED) == [anna_rub]

    # totals in roubles: 50_000 RUB for Anna and 990 USD minus the premium fee for Boris
    boris_total = boris_usd.balance * 90
    assert bank.get_total_balance() == Decimal("50000.00") + boris_total
    assert [client for client, _ in bank.get_clients_ranking()] == [boris, anna]

    assert [activity.reason for activity in bank.suspicious_activities] == [
        SuspicionReason.FAILED_LOGIN,
        SuspicionReason.FAILED_LOGIN,
        SuspicionReason.FAILED_LOGIN,
        SuspicionReason.CLIENT_BLOCKED,
        SuspicionReason.BLOCKED_CLIENT_ACTIVITY,
        SuspicionReason.INACTIVE_ACCOUNT_OPERATION,
        SuspicionReason.NIGHT_OPERATION,
    ]


def test_rejected_operations_never_change_balances(bank, clock):
    client = bank.add_client(new_client("Vera"), "vera-password")
    account = bank.open_account(client.client_id, currency="RUB", initial_balance=1_000)

    clock.moment = datetime(2026, 9, 25, 0, 0)
    with pytest.raises(OperationTimeRestrictedError):
        bank.withdraw(account.account_id, 500)
    clock.moment = datetime(2026, 9, 25, 5, 0)

    bank.freeze_account(account.account_id)
    with pytest.raises(AccountFrozenError):
        bank.withdraw(account.account_id, 500)
    bank.unfreeze_account(account.account_id)

    client.block()
    with pytest.raises(ClientBlockedError):
        bank.withdraw(account.account_id, 500)

    assert account.balance == Decimal("1000.00")
    assert bank.get_total_balance() == Decimal("1000.00")
