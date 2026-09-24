"""Demonstration script.

Run from the repository root:

    python src/main.py

The script is organised in stages, one per feature set.
Each stage prints a banner, walks through its scenario and returns the
accounts it created; the final section treats all of them polymorphically.

Stages:
1. Accounts Basic - a regular account, status enforcement, validation.
2. Accounts Advanced - savings, premium and investment accounts.
3. Bank System - clients, login lockout, freezing, the night window,
   search, totals in roubles and the suspicious activity log.
"""

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal

from exceptions import BankError
from models import (
    AbstractAccount,
    AccountStatus,
    AssetType,
    BankAccount,
    Client,
    Currency,
    InvestmentAccount,
    PremiumAccount,
    SavingsAccount,
)
from services import Bank, SecurityGuard
from utils import ManualClock


def print_stage(number: int, title: str) -> None:
    banner = f" STAGE {number}: {title} "
    print(f"\n{banner:=^72}")


def print_step(number: int, title: str) -> None:
    print(f"\n{number}. {title}")


def attempt(description: str, action: Callable[[], object], label: str | None = "balance") -> None:
    """Run ``action`` and print its result, or the domain error that rejected it."""
    try:
        value = action()
    except BankError as error:
        print(f"  [rejected] {description}: {type(error).__name__}: {error}")
    else:
        result = f": {label} {value}" if label else ""
        print(f"  [ok]       {description}{result}")


def run_accounts_basic(owner: Client) -> list[AbstractAccount]:
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


def run_accounts_advanced(owner: Client) -> list[AbstractAccount]:
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
    growth_rates = {AssetType.STOCKS: "0.10", AssetType.BONDS: "0.04", AssetType.ETF: "0.07"}
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
    rates = ", ".join(f"{asset.value} {Decimal(rate):.0%}" for asset, rate in growth_rates.items())
    print(f"  Portfolio: {investment.get_account_info()['portfolio']}")
    attempt(
        f"project yearly growth ({rates})",
        lambda: investment.project_yearly_growth(growth_rates),
        label="growth",
    )
    attempt(
        "project yearly growth (rate only for stocks)",
        lambda: investment.project_yearly_growth({"stocks": 0.1}),
        label="growth",
    )

    return [savings, savings_frozen, premium, premium_no_overdraft, investment, investment_small]


def run_bank_system() -> list[AbstractAccount]:
    print_stage(3, "Bank System")
    # the bank reads time from this clock, so the demo can step into the night window
    clock = ManualClock(datetime(2026, 9, 24, 14, 0))
    bank = Bank(security=SecurityGuard(clock=clock))

    print_step(1, "Registering clients")
    maria = Client(
        first_name="Maria",
        last_name="Volkova",
        birth_date=date(1988, 11, 3),
        email="maria.volkova@example.com",
        phone="+79035550101",
    )
    oleg = Client(
        first_name="Oleg",
        last_name="Sokolov",
        middle_name="Igorevich",
        birth_date=date(1975, 2, 14),
        email="oleg.sokolov@example.com",
        phone="+79035550202",
    )
    alina = Client(
        first_name="Alina",
        last_name="Nurlanova",
        birth_date=date(2001, 7, 21),
        email="alina.n@example.kz",
        phone="+77015550303",
    )
    for client, password in ((maria, "maria-pass-1"), (oleg, "oleg-pass-22"), (alina, "alina-pass-333")):
        bank.add_client(client, password)
        print(f"  {client}")
    teenager = {"first_name": "Timur", "last_name": "Volkov", "email": "timur@example.com", "phone": "+79035550404"}
    attempt(
        "register a 16-year-old client",
        lambda: Client(**teenager, birth_date=date(date.today().year - 16, 1, 1)),
        label=None,
    )

    print_step(2, "Opening accounts")
    maria_current = bank.open_account(maria.client_id, currency="RUB", initial_balance=150_000)
    maria_savings = bank.open_account(
        maria.client_id, "savings", currency="RUB", initial_balance=300_000, min_balance=50_000, monthly_rate="0.01"
    )
    oleg_premium = bank.open_account(
        oleg.client_id, "premium", currency="USD", initial_balance=6_000, overdraft_limit=1_000, withdrawal_fee=2
    )
    oleg_investment = bank.open_account(oleg.client_id, "investment", currency="EUR", initial_balance=2_000)
    oleg_investment.invest("etf", 1_500)
    alina_current = bank.open_account(alina.client_id, currency="KZT", initial_balance=400_000)
    alina_spare = bank.open_account(alina.client_id, currency="CNY")
    for account in (maria_current, maria_savings, oleg_premium, oleg_investment, alina_current, alina_spare):
        print(f"  {account}")
    attempt("open a 'crypto' account", lambda: bank.open_account(maria.client_id, "crypto", currency="RUB"))

    print_step(3, "Authentication and lockout after three failures")
    attempt("Maria logs in", lambda: bank.authenticate_client(maria.client_id, "maria-pass-1"), label="client")
    for number in range(1, 4):
        attempt(
            f"Oleg, wrong password #{number}",
            lambda: bank.authenticate_client(oleg.client_id, "guess-123"),
            label="client",
        )
    attempt("Oleg, right password", lambda: bank.authenticate_client(oleg.client_id, "oleg-pass-22"), label="client")
    attempt("withdraw 100 USD (blocked client)", lambda: bank.withdraw(oleg_premium.account_id, 100))
    attempt("unknown client logs in", lambda: bank.authenticate_client("no-such-id", "whatever-1"), label="client")
    attempt("unblock Oleg", lambda: bank.unblock_client(oleg.client_id), label="client")
    attempt("Oleg, right password", lambda: bank.authenticate_client(oleg.client_id, "oleg-pass-22"), label="client")

    print_step(4, "Freezing and unfreezing")
    attempt("freeze Maria's current account", lambda: bank.freeze_account(maria_current.account_id), "account")
    attempt("deposit 1_000 RUB (frozen)", lambda: bank.deposit(maria_current.account_id, 1_000))
    attempt("unfreeze", lambda: bank.unfreeze_account(maria_current.account_id), "account")
    attempt("deposit 1_000 RUB", lambda: bank.deposit(maria_current.account_id, 1_000))
    attempt("deposit 6_000 USD (540_000 RUB, large)", lambda: bank.deposit(oleg_premium.account_id, 6_000))

    print_step(5, "Night window 00:00-05:00")
    clock.moment = datetime(2026, 9, 25, 2, 30)
    print(f"  clock: {clock():%Y-%m-%d %H:%M}")
    attempt("withdraw 5_000 KZT", lambda: bank.withdraw(alina_current.account_id, 5_000))
    attempt("open a savings account", lambda: bank.open_account(alina.client_id, "savings", currency="KZT"))
    attempt("freeze Alina's account", lambda: bank.freeze_account(alina_current.account_id), "account")
    attempt("Alina logs in", lambda: bank.authenticate_client(alina.client_id, "alina-pass-333"), label="client")
    clock.moment = datetime(2026, 9, 25, 5, 0)
    print(f"  clock: {clock():%Y-%m-%d %H:%M}")
    attempt("unfreeze Alina's account", lambda: bank.unfreeze_account(alina_current.account_id), "account")
    attempt("withdraw 5_000 KZT", lambda: bank.withdraw(alina_current.account_id, 5_000))

    print_step(6, "Closing accounts")
    attempt("close Maria's savings (not empty)", lambda: bank.close_account(maria_savings.account_id), "account")
    attempt("close Alina's empty CNY account", lambda: bank.close_account(alina_spare.account_id), "account")
    attempt("deposit 10 CNY (closed)", lambda: bank.deposit(alina_spare.account_id, 10))

    print_step(7, "Searching accounts")
    searches = {
        "Maria's accounts": {"client_id": maria.client_id},
        "RUB accounts with balance >= 200_000": {"currency": "RUB", "min_balance": 200_000},
        "premium accounts": {"account_type": "premium"},
        "closed accounts": {"status": "closed"},
    }
    for title, filters in searches.items():
        found = bank.search_accounts(**filters)
        print(f"  {title}: {len(found)}")
        for account in found:
            print(f"    {account}")

    print_step(8, f"Totals in {bank.base_currency.value}")
    print(f"  total balance: {bank.get_total_balance()} {bank.base_currency.value}")
    for place, (client, total) in enumerate(bank.get_clients_ranking(), start=1):
        print(f"  {place}. {client.full_name:<28} {total:>14} {bank.base_currency.value}")

    print_step(9, "Suspicious activity log")
    for activity in bank.suspicious_activities:
        subject = activity.client_id if activity.client_id else "-"
        print(f"  {activity.timestamp:%m-%d %H:%M} {activity.reason.value:<27} {subject[:8]:<8} {activity.details}")

    return bank.search_accounts()


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
    owner = Client(
        first_name="Ivan",
        last_name="Petrov",
        middle_name="Sergeevich",
        birth_date=date(1990, 5, 17),
        email="ivan.petrov@example.com",
        phone="+79161234567",
    )
    accounts = run_accounts_basic(owner) + run_accounts_advanced(owner) + run_bank_system()
    print_summary(accounts)


if __name__ == "__main__":
    main()
