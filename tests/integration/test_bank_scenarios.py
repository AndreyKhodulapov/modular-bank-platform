"""End-to-end scenarios of the bank: several clients, accounts, logins and security rules."""

from datetime import datetime
from decimal import Decimal

import pytest

from exceptions import (
    AccountFrozenError,
    AuthenticationError,
    ClientBlockedError,
    OperationTimeRestrictedError,
)
from models import AccountStatus
from services import SuspicionReason


def test_bank_day_from_registration_to_ranking(bank, clock, make_client):
    anna = bank.add_client(make_client("Anna"), "anna-password")
    boris = bank.add_client(make_client("Boris"), "boris-password")

    anna_rub = bank.open_account(anna.client_id, currency="RUB", initial_balance=20_000)
    anna_savings = bank.open_account(
        anna.client_id, "savings", currency="RUB", initial_balance=50_000, min_balance=10_000
    )
    boris_usd = bank.open_account(boris.client_id, "premium", currency="USD", initial_balance=1_000)
    assert anna.account_ids == [anna_rub.account_id, anna_savings.account_id]

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

    bank.freeze_account(anna_rub.account_id)
    with pytest.raises(AccountFrozenError):
        bank.withdraw(anna_rub.account_id, 100)
    bank.unfreeze_account(anna_rub.account_id)
    assert bank.withdraw(anna_rub.account_id, 100) == Decimal("19900.00")

    clock.moment = datetime(2026, 9, 25, 3, 0)
    with pytest.raises(OperationTimeRestrictedError):
        bank.deposit(anna_rub.account_id, 100)
    clock.moment = datetime(2026, 9, 25, 9, 0)
    bank.deposit(anna_rub.account_id, 100)

    bank.withdraw(anna_rub.account_id, 20_000)
    bank.close_account(anna_rub.account_id)
    assert bank.search_accounts(status=AccountStatus.CLOSED) == [anna_rub]

    boris_total = boris_usd.balance * 90  # the reference USD rate
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
