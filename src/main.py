"""Demonstration script.

Run from the repository root:

    python src/main.py

The script is organised in stages that mirror the growth of the platform.
Each stage prints a banner, walks through its scenario and returns the
accounts it created; the final section treats all of them polymorphically.

Stages:
1. Accounts Basic - a regular account, status enforcement, validation.
2. Accounts Advanced - savings, premium and investment accounts.
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal

from exceptions import BankError
from models import (
    AbstractAccount,
    AccountStatus,
    AssetType,
    BankAccount,
    Currency,
    InvestmentAccount,
    Owner,
    PremiumAccount,
    SavingsAccount,
)

# Illustrative yearly growth rates for the investment demo; not market data.
DEMO_GROWTH_RATES = {AssetType.STOCKS: "0.10", AssetType.BONDS: "0.04", AssetType.ETF: "0.07"}


def print_stage(number: int, title: str) -> None:
    banner = f" STAGE {number}: {title} "
    print(f"\n{banner:=^72}")


def print_step(number: int, title: str) -> None:
    print(f"\n{number}. {title}")


def attempt(description: str, action: Callable[[], Decimal], label: str = "balance") -> None:
    try:
        value = action()
    except BankError as error:
        print(f"  [rejected] {description}: {type(error).__name__}: {error}")
    else:
        print(f"  [ok]       {description}: {label} {value}")


def run_accounts_basic(owner: Owner) -> list[AbstractAccount]:
    print_stage(1, "Accounts Basic")

    print_step(1, "Creating accounts")
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

    print_step(2, "Operations on the frozen account")
    attempt("deposit 100 USD", lambda: frozen.deposit(100))
    attempt("withdraw 50 USD", lambda: frozen.withdraw(50))

    print_step(3, "Valid operations on the active account")
    attempt("deposit 500.25 RUB", lambda: active.deposit(500.25))
    attempt("withdraw 300 RUB", lambda: active.withdraw("300"))

    print_step(4, "Validation on the active account")
    attempt("deposit -10 RUB", lambda: active.deposit(-10))
    attempt("deposit 'ten' RUB", lambda: active.deposit("ten"))
    attempt("withdraw 1_000_000 RUB", lambda: active.withdraw(1_000_000))
    attempt("withdraw 1_000_001 RUB", lambda: active.withdraw(1_000_001))

    return [active, frozen]


def run_accounts_advanced(owner: Owner) -> list[AbstractAccount]:
    print_stage(2, "Accounts Advanced")

    print_step(1, "Savings accounts: minimum balance and monthly interest")
    savings = SavingsAccount(
        owner=owner, currency="RUB", initial_balance=10_000, min_balance=1_000, monthly_rate="0.015"
    )
    savings_frozen = SavingsAccount(
        owner=owner,
        currency="RUB",
        status="frozen",
        initial_balance=5_000,
        min_balance=500,
        monthly_rate="0.02",
    )
    print(f"  {savings}")
    print(f"  {savings_frozen}")
    attempt("apply monthly interest", savings.apply_monthly_interest, label="interest")
    attempt("apply monthly interest (frozen)", savings_frozen.apply_monthly_interest, label="interest")
    attempt("withdraw 9_000 RUB (keeps min balance)", lambda: savings.withdraw(9_000))
    attempt("withdraw 200 RUB (breaks min balance)", lambda: savings.withdraw(200))

    print_step(2, "Premium accounts: overdraft, fixed fee, higher limits")
    premium = PremiumAccount(
        owner=owner, currency="USD", initial_balance=1_000, overdraft_limit=2_000, withdrawal_fee=5
    )
    premium_no_overdraft = PremiumAccount(owner=owner, currency="EUR", initial_balance=100, withdrawal_fee="1.50")
    print(f"  {premium}")
    print(f"  {premium_no_overdraft}")
    attempt("withdraw 2_500 USD (goes into overdraft)", lambda: premium.withdraw(2_500))
    attempt("withdraw 500 USD (exceeds overdraft)", lambda: premium.withdraw(500))
    attempt("deposit 5_000_000 USD (premium limit)", lambda: premium.deposit(5_000_000))
    attempt("withdraw 100 EUR (fee makes it 101.50)", lambda: premium_no_overdraft.withdraw(100))
    attempt("withdraw 98.50 EUR", lambda: premium_no_overdraft.withdraw("98.50"))

    print_step(3, "Investment accounts: portfolio and yearly projection")
    investment = InvestmentAccount(owner=owner, currency="EUR", initial_balance=10_000)
    investment_small = InvestmentAccount(owner=owner, currency="KZT", initial_balance=1_000)
    print(f"  {investment}")
    print(f"  {investment_small}")
    attempt("invest 5_000 EUR in stocks", lambda: investment.invest("stocks", 5_000), label="cash")
    attempt("invest 2_000 EUR in bonds", lambda: investment.invest(AssetType.BONDS, 2_000), label="cash")
    attempt("invest 1_000 EUR in etf", lambda: investment.invest("etf", 1_000), label="cash")
    attempt("invest 100 EUR in crypto", lambda: investment.invest("crypto", 100), label="cash")
    attempt("invest 5_000 EUR in etf (not enough cash)", lambda: investment.invest("etf", 5_000), label="cash")
    attempt("withdraw 3_000 EUR (only cash)", lambda: investment.withdraw(3_000))
    attempt("divest 1_500 EUR from stocks", lambda: investment.divest("stocks", 1_500), label="cash")
    attempt("withdraw 3_000 EUR", lambda: investment.withdraw(3_000))
    attempt("invest 1_000 KZT in etf", lambda: investment_small.invest("etf", 1_000), label="cash")
    rates = ", ".join(f"{asset.value} {Decimal(rate):.0%}" for asset, rate in DEMO_GROWTH_RATES.items())
    print(f"  Portfolio: {investment.get_account_info()['portfolio']}")
    attempt(f"project yearly growth ({rates})", lambda: investment.project_yearly_growth(DEMO_GROWTH_RATES), "growth")
    attempt(
        "project yearly growth (rate only for stocks)",
        lambda: investment.project_yearly_growth({"stocks": 0.1}),
        "growth",
    )

    return [savings, savings_frozen, premium, premium_no_overdraft, investment, investment_small]


def print_summary(accounts: list[AbstractAccount]) -> None:
    """Treat every account through the common interface, whatever its type."""
    print(f"\n{' SUMMARY ':=^72}")
    for account in accounts:
        print(f"  {account}")
    print()
    for account in accounts:
        info = account.get_account_info()
        extra = {key: value for key, value in info.items() if key not in ("account_id", "owner", "account_type")}
        print(f"  {info['account_type']:<18} {extra}")


def main() -> None:
    owner = Owner(
        first_name="Ivan",
        last_name="Petrov",
        middle_name="Sergeevich",
        birth_date=date(1990, 5, 17),
        email="ivan.petrov@example.com",
        phone="+79161234567",
    )
    accounts = run_accounts_basic(owner) + run_accounts_advanced(owner)
    print_summary(accounts)


if __name__ == "__main__":
    main()
