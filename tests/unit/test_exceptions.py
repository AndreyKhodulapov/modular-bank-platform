from decimal import Decimal

import pytest

from exceptions import (
    AccountClosedError,
    AccountFrozenError,
    BankError,
    InsufficientFundsError,
    InvalidOperationError,
)


@pytest.mark.parametrize(
    "error_type",
    [
        AccountFrozenError,
        AccountClosedError,
        InsufficientFundsError,
        InvalidOperationError,
    ],
)
def test_all_domain_errors_inherit_from_bank_error(error_type):
    assert issubclass(error_type, BankError)


def test_status_errors_carry_account_id():
    assert AccountFrozenError("abc").account_id == "abc"
    assert "abc" in str(AccountClosedError("abc"))


def test_insufficient_funds_error_carries_amounts():
    error = InsufficientFundsError(Decimal("50.00"), Decimal("20.00"))
    assert error.requested == Decimal("50.00")
    assert error.available == Decimal("20.00")
    assert "50.00" in str(error) and "20.00" in str(error)
