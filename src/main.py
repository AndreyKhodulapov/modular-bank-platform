"""Demonstration script.

Run from the repository root:

    python src/main.py

The script walks through the core account scenario:
creating an active and a frozen account, rejecting operations on the frozen
one, and performing a valid deposit and withdrawal on the active one.
"""

from datetime import date

from exceptions import BankError
from models import AccountStatus, BankAccount, Currency, Owner


def attempt(description: str, action) -> None:
    try:
        new_balance = action()
    except BankError as error:
        print(f"  [rejected] {description}: {type(error).__name__}: {error}")
    else:
        print(f"  [ok]       {description}: balance is now {new_balance}")


def main() -> None:
    owner = Owner(
        first_name="Ivan",
        last_name="Petrov",
        middle_name="Sergeevich",
        birth_date=date(1990, 5, 17),
        email="ivan.petrov@example.com",
        phone="+79161234567",
    )

    print("1. Creating accounts")
    active = BankAccount(owner=owner, currency=Currency.RUB, initial_balance="1000")
    frozen = BankAccount(
        owner=owner,
        currency="USD",
        account_id="ACC-2024-0042",
        status=AccountStatus.FROZEN,
        initial_balance=250.50,
    )
    print(f"  {active}")
    print(f"  {frozen}")

    print("\n2. Operations on the frozen account")
    attempt("deposit 100 USD", lambda: frozen.deposit(100))
    attempt("withdraw 50 USD", lambda: frozen.withdraw(50))

    print("\n3. Valid operations on the active account")
    attempt("deposit 500.25 RUB", lambda: active.deposit(500.25))
    attempt("withdraw 300 RUB", lambda: active.withdraw("300"))

    print("\n4. Validation on the active account")
    attempt("deposit -10 RUB", lambda: active.deposit(-10))
    attempt("deposit 'ten' RUB", lambda: active.deposit("ten"))
    attempt("withdraw 1_000_000 RUB", lambda: active.withdraw(1_000_000))

    print("\n5. Final state")
    print(f"  {active}")
    for key, value in active.get_account_info().items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
