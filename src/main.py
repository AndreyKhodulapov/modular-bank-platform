"""One day of a small bank, from the first salary to the reports.

Run from the repository root:

    python src/main.py

The program sets up a bank with seven clients and twelve accounts, then
plays a scripted day: forty transactions go through the priority
queue while the bank's clock moves from the morning into the night and on
to the next morning. Some of them are ordinary, some fail (a frozen or
closed account, a blocked client, a limit, missing money, an unknown
account) and some look suspicious to risk control, which lets them through
with a warning or blocks them.

Sections:
1. Initialization - the bank, its clients and accounts.
2. Simulation - the transactions, round by round, with a feed of what the
   audit log recorded: queued, completed, retried, failed, blocked.
3. Logging - totals of the audit log and the life cycle of a few
   transactions as the journal holds it.
4. Client view - a client logs in and sees their accounts, a statement,
   their transactions and the suspicious operations.
5. Reports - the top three clients, transaction statistics and the total
   balance of the bank.

The audit log is appended to ``logs/audit.jsonl`` and the application log
to ``logs/app.jsonl``; warnings and errors of the application log also
appear in the terminal between the program's lines. Paths and the terminal
level come from environment variables, see ``settings.py``.
"""

from collections import Counter
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from exceptions import AuthenticationError, ClientBlockedError, InvalidOperationError
from logging_setup import configure_logging
from models import BankAccount, Client, Transaction
from services import (
    AuditCategory,
    AuditLevel,
    AuditLog,
    AuditReport,
    Bank,
    BankReport,
    RiskEvent,
    SecurityGuard,
    TransactionEvent,
    TransactionProcessor,
    TransactionQueue,
)
from settings import Settings
from utils import ManualClock


def print_section(number: int, title: str) -> None:
    banner = f" {number}. {title} "
    print(f"\n{banner:=^100}")


def print_block(text: object, indent: str = "  ") -> None:
    for line in str(text).splitlines():
        print(f"{indent}{line}")


@dataclass(frozen=True)
class DemoBank:
    """The bank of the program with its clients and accounts under short names."""

    bank: Bank
    clock: ManualClock
    clients: dict[str, Client]
    accounts: dict[str, BankAccount]
    passwords: dict[str, str]


class Simulation:
    """Queues the transactions of the day, processes them as the clock moves and prints the feed.

    The feed is read from the audit log, not printed by hand: after every
    round it shows the transaction events the round added and the risk
    events of a medium or high risk.
    """

    # the feed tag of each event; a failed attempt is a retry or a final failure
    TAGS = {
        TransactionEvent.QUEUED.value: "queued",
        TransactionEvent.CANCELLED.value: "cancelled",
        TransactionEvent.COMPLETED.value: "completed",
        RiskEvent.ASSESSED.value: "warning",
        RiskEvent.BLOCKED.value: "blocked",
    }

    def __init__(self, demo: DemoBank) -> None:
        self._demo = demo
        bank = demo.bank
        self._queue = TransactionQueue(clock=bank.now, audit_log=bank.audit_log)
        # a long retry delay lets a transaction refused at night succeed in the morning
        self._processor = TransactionProcessor(bank, retry_delay=timedelta(hours=2))
        self._labels: dict[str, str] = {}
        self._seen = len(bank.audit_log)

    @property
    def queued(self) -> int:
        return len(self._labels)

    def label(self, transaction_id: str | None) -> str:
        """The numbered label of a queued transaction, empty for anything else."""
        return self._labels.get(transaction_id, "") if transaction_id is not None else ""

    def transaction(self, label: str) -> Transaction:
        """The queued transaction labelled ``label`` (without its number); the label must be unique."""
        matches = [key for key, value in self._labels.items() if value.split(" ", 1)[1] == label]
        if len(matches) != 1:
            raise LookupError(f"Label {label!r} matches {len(matches)} transactions; expected exactly one.")
        return self._queue.get(matches[0])

    def _account_id(self, reference: str | None) -> str | None:
        # a short name of the demo's accounts, or an account number outside them kept as is
        if reference is None or reference not in self._demo.accounts:
            return reference
        return self._demo.accounts[reference].account_id

    def enqueue(self, *rows: tuple) -> None:
        """Queue one transaction per row: ``(label, type, amount, currency, sender, recipient[, params])``."""
        for label, kind, amount, currency, sender, recipient, *params in rows:
            transaction = Transaction(
                kind,
                amount,
                currency,
                sender_id=self._account_id(sender),
                recipient_id=self._account_id(recipient),
                created_at=self._demo.clock(),
                **(params[0] if params else {}),
            )
            self._labels[transaction.transaction_id] = f"#{len(self._labels) + 1:02} {label}"
            self._queue.add(transaction)

    def cancel(self, label: str) -> None:
        self._queue.cancel(self.transaction(label).transaction_id)

    def process(self) -> None:
        """Run everything that is due by the bank's clock and print the feed."""
        self._processor.process_queue(self._queue)
        self.print_feed()

    def print_feed(self) -> None:
        events = self._demo.bank.audit_log.events[self._seen :]
        self._seen += len(events)
        for event in events:
            if event.category is AuditCategory.RISK and event.level < AuditLevel.WARNING:
                continue  # a low risk is the normal case
            if event.event == TransactionEvent.FAILED.value:
                tag = "retry" if event.details.get("will_retry") else "failed"
            elif event.category in (AuditCategory.TRANSACTION, AuditCategory.RISK):
                tag = self.TAGS[event.event]
            else:
                continue
            print(f"    {event.timestamp:%H:%M}  {tag:<9}  {self.label(event.transaction_id):<24} {event.message}")


def open_bank(audit_log: AuditLog) -> DemoBank:
    """Register the clients and open their accounts three weeks before the day; Sofia opens hers on the day."""
    clock = ManualClock(datetime(2026, 9, 1, 10, 0))
    bank = Bank(security=SecurityGuard(clock=clock, audit_log=audit_log))
    clients: dict[str, Client] = {}
    passwords: dict[str, str] = {}
    for key, first_name, last_name, birth_date, phone in (
        ("maria", "Maria", "Volkova", date(1988, 11, 3), "+79035550101"),
        ("oleg", "Oleg", "Sokolov", date(1975, 2, 14), "+79035550202"),
        ("alina", "Alina", "Nurlanova", date(2001, 7, 21), "+77015550303"),
        ("dmitry", "Dmitry", "Orlov", date(1993, 6, 30), "+79035550404"),
        ("elena", "Elena", "Smirnova", date(1981, 12, 9), "+79035550505"),
        ("timur", "Timur", "Akhmetov", date(1997, 3, 18), "+77015550606"),
        ("sofia", "Sofia", "Lebedeva", date(2004, 8, 25), "+79035550707"),
    ):
        clients[key] = Client(
            first_name=first_name,
            last_name=last_name,
            birth_date=birth_date,
            email=f"{first_name}.{last_name}@example.com".lower(),
            phone=phone,
        )
        passwords[key] = f"{key}-pass-2026"
        bank.add_client(clients[key], passwords[key])

    accounts: dict[str, BankAccount] = {}
    for name, owner, account_type, params in (
        ("maria_rub", "maria", "basic", {"currency": "RUB", "initial_balance": 50_000}),
        (
            "maria_savings",
            "maria",
            "savings",
            {"currency": "RUB", "initial_balance": 100_000, "min_balance": 10_000, "monthly_rate": "0.01"},
        ),
        (
            "oleg_usd",
            "oleg",
            "premium",
            {"currency": "USD", "initial_balance": 10_000, "overdraft_limit": 3_000, "withdrawal_fee": 2},
        ),
        ("oleg_invest", "oleg", "investment", {"currency": "EUR", "initial_balance": 15_000}),
        ("alina_kzt", "alina", "basic", {"currency": "KZT", "initial_balance": 500_000}),
        ("alina_rub", "alina", "basic", {"currency": "RUB", "initial_balance": 20_000}),
        ("dmitry_rub", "dmitry", "basic", {"currency": "RUB", "initial_balance": 40_000}),
        ("dmitry_cny", "dmitry", "basic", {"currency": "CNY", "initial_balance": 2_000}),
        ("elena_eur", "elena", "basic", {"currency": "EUR", "initial_balance": 5_000}),
        (
            "elena_savings",
            "elena",
            "savings",
            {"currency": "RUB", "initial_balance": 700_000, "min_balance": 50_000, "monthly_rate": "0.012"},
        ),
        ("timur_rub", "timur", "basic", {"currency": "RUB", "initial_balance": 15_000}),
    ):
        accounts[name] = bank.open_account(clients[owner].client_id, account_type, **params)
    clock.moment = datetime(2026, 9, 24, 9, 0)
    accounts["sofia_rub"] = bank.open_account(clients["sofia"].client_id, "basic", currency="RUB")
    return DemoBank(bank=bank, clock=clock, clients=clients, accounts=accounts, passwords=passwords)


def initialize(audit_log: AuditLog) -> DemoBank:
    print_section(1, "Initialization")
    demo = open_bank(audit_log)
    bank = demo.bank
    print(f"  Bank: base currency {bank.base_currency.value}, audit log {bank.audit_log.file_path}")
    print(f"  {len(demo.clients)} clients, {len(demo.accounts)} accounts")
    for account in demo.accounts.values():
        print(f"    opened {bank.account_opened_at(account.account_id):%m-%d}  {account}")
    return demo


def note(text: str) -> None:
    print(f"    *      {text}")


def simulate(demo: DemoBank) -> Simulation:
    print_section(2, "Simulation")
    bank, clients, accounts = demo.bank, demo.clients, demo.accounts
    simulation = Simulation(demo)

    def start_round(moment: datetime, title: str) -> None:
        """Move the clock to ``moment``: the round's transactions are created and processed then."""
        demo.clock.moment = moment
        print(f"\n  --- {moment:%m-%d %H:%M} {title} ---")

    start_round(datetime(2026, 9, 24, 9, 0), "Morning: salaries and everyday payments")
    simulation.enqueue(
        ("salary Maria", "deposit", 150_000, "RUB", None, "maria_rub", {"priority": "high"}),
        ("salary Dmitry", "deposit", 120_000, "RUB", None, "dmitry_rub", {"priority": "high"}),
        ("salary Elena", "deposit", 2_000, "EUR", None, "elena_eur", {"priority": "high"}),
        ("rent", "transfer", 45_000, "RUB", "maria_rub", "dmitry_rub"),
        ("to savings", "transfer", 20_000, "RUB", "maria_rub", "maria_savings", {"priority": "low"}),
        ("cash", "withdrawal", 30_000, "KZT", "alina_kzt", None, {"priority": "urgent"}),
        ("cash", "withdrawal", 5_000, "RUB", "timur_rub", None),
        ("utilities", "transfer", 4_500, "RUB", "timur_rub", "dmitry_rub"),
        ("subscription", "withdrawal", 999, "RUB", "maria_rub", None),
        ("abroad", "external_transfer", 500, "USD", "oleg_usd", "DE89-3704-0044-0532-0130-00"),
        ("gift EUR->KZT", "transfer", 300, "EUR", "elena_eur", "alina_kzt"),
        ("RUB->CNY", "transfer", 10_000, "RUB", "dmitry_rub", "dmitry_cny"),
        ("planned", "transfer", 1_000, "EUR", "elena_eur", "oleg_invest", {"scheduled_at": datetime(2026, 9, 24, 12)}),
    )
    simulation.process()

    start_round(datetime(2026, 9, 24, 12, 0), "Noon: mistakes and refusals")
    bank.freeze_account(accounts["alina_rub"].account_id)
    note("Alina lost her card: her RUB account is frozen")
    payout = bank.close_account(accounts["dmitry_cny"].account_id)
    note(f"Dmitry closed his CNY account, payout {payout} CNY")
    for _ in range(3):
        with suppress(AuthenticationError, ClientBlockedError):
            bank.authenticate_client(clients["timur"].client_id, "forgotten-pass")
    note(f"Timur typed a wrong password three times: his access is {clients['timur'].status.value}")
    simulation.enqueue(
        ("to frozen", "transfer", 2_000, "RUB", "maria_rub", "alina_rub"),
        ("from closed", "transfer", 500, "CNY", "dmitry_cny", "maria_rub"),
        ("blocked client", "transfer", 1_000, "RUB", "timur_rub", "maria_rub"),
        ("over limit", "withdrawal", 1_200_000, "RUB", "dmitry_rub", None),
        ("unknown account", "deposit", 5_000, "RUB", None, "ACC-404"),
        ("short of money", "transfer", 800_000, "KZT", "alina_kzt", "elena_eur"),
        ("typo", "transfer", 5_000, "RUB", "maria_rub", "dmitry_rub", {"scheduled_at": datetime(2026, 9, 24, 13)}),
    )
    simulation.cancel("typo")
    simulation.process()

    start_round(datetime(2026, 9, 24, 15, 0), "Afternoon: suspicious activity")
    simulation.enqueue(
        ("large", "transfer", 7_000, "USD", "oleg_usd", "maria_rub"),
        ("huge abroad", "external_transfer", 25_000, "USD", "oleg_usd", "CY17-0020-0128-0000-0012-0052-7600"),
        *((f"rapid {number}/6", "transfer", 10_000, "KZT", "alina_kzt", "sofia_rub") for number in range(1, 7)),
    )
    simulation.process()

    start_round(datetime(2026, 9, 24, 23, 30), "Late evening")
    simulation.enqueue(
        ("evening", "transfer", 3_000, "RUB", "maria_rub", "dmitry_rub"),
        ("late large", "transfer", 600_000, "RUB", "elena_savings", "sofia_rub"),
    )
    simulation.process()

    start_round(datetime(2026, 9, 25, 2, 0), "Night: the bank does not move money until 05:00")
    simulation.enqueue(("night", "transfer", 1_000, "RUB", "dmitry_rub", "maria_rub"))
    simulation.process()
    start_round(datetime(2026, 9, 25, 4, 0), "Night: a retry is still too early")
    simulation.process()

    start_round(datetime(2026, 9, 25, 8, 0), "Next morning")
    bank.unblock_client(clients["timur"].client_id)
    note("Timur called the bank and was unblocked")
    simulation.enqueue(
        ("car service", "transfer", 4_000, "USD", "oleg_usd", "maria_rub"),
        ("cash", "withdrawal", 5_000, "RUB", "sofia_rub", None),
        ("groceries", "withdrawal", 3_200, "RUB", "dmitry_rub", None),
        ("pocket money", "transfer", 5_000, "RUB", "maria_rub", "sofia_rub"),
        ("RUB->EUR", "transfer", 50_000, "RUB", "elena_savings", "elena_eur"),
        ("KZT->EUR", "transfer", 50_000, "KZT", "alina_kzt", "elena_eur"),
        ("cash", "withdrawal", 2_000, "RUB", "timur_rub", None),
        ("dinner", "transfer", 10_000, "RUB", "maria_rub", "elena_eur"),
        ("cash deposit", "deposit", 15_000, "RUB", None, "dmitry_rub"),
    )
    simulation.process()

    print(f"\n  {simulation.queued} transactions queued")
    return simulation


def show_logging(demo: DemoBank, simulation: Simulation) -> None:
    print_section(3, "Logging")
    audit_log = demo.bank.audit_log
    print(f"  The audit log holds {len(audit_log)} events of this run, file: {audit_log.file_path}")
    counts = Counter(
        event.event for event in audit_log if event.category in (AuditCategory.TRANSACTION, AuditCategory.RISK)
    )
    print("  " + ", ".join(f"{name} {count}" for name, count in counts.items()))

    print("\n  Life cycle of selected transactions, as the audit log holds it:")
    for label in ("rent", "night", "short of money", "to frozen", "huge abroad", "typo"):
        transaction = simulation.transaction(label)
        print(f"\n  {simulation.label(transaction.transaction_id)}: {transaction.status.value}")
        for event in audit_log.filter(transaction_id=transaction.transaction_id):
            print(f"    {event}")


def show_client(demo: DemoBank, simulation: Simulation, key: str) -> None:
    bank, client = demo.bank, demo.clients[key]
    print_section(4, f"Client view: {client.full_name}")
    bank.authenticate_client(client.client_id, demo.passwords[key])
    print("  Logged in")

    accounts = bank.search_accounts(client_id=client.client_id)
    print("\n  Accounts:")
    for account in accounts:
        print(f"    {account}")

    for account in accounts:
        print(f"\n  Statement of {account.account_type} {account.account_id[:8]}:")
        for movement in bank.history.movements(account.account_id):
            print(f"    {movement}  {simulation.label(movement.transaction_id)}".rstrip())

    print("\n  Transactions:")
    for transaction in bank.history.transactions(account_ids=[account.account_id for account in accounts]):
        reason = f" - {transaction.failure_reason}" if transaction.failure_reason else ""
        print(
            f"    {transaction.finished_at:%m-%d %H:%M} {simulation.label(transaction.transaction_id):<24} "
            f"{transaction.status.value:<9} {transaction.transaction_type.value} of "
            f"{transaction.amount} {transaction.currency.value}{reason}"
        )

    print("\n  Suspicious operations:")
    for event in bank.audit_log.filter(client_id=client.client_id, min_level=AuditLevel.WARNING):
        if event.category in (AuditCategory.SECURITY, AuditCategory.RISK):
            print(f"    {event}")
    print()
    print_block(AuditReport(bank.audit_log, bank.risk_analyzer).client_risk_profile(client.client_id))


def show_reports(demo: DemoBank) -> None:
    print_section(5, "Reports")
    report = BankReport(demo.bank)
    for section in (report.top_clients(), report.transaction_statistics(), report.total_balance()):
        print_block(section)
        print()


def main() -> None:
    try:
        settings = Settings.from_env()
    except InvalidOperationError as error:
        raise SystemExit(f"Invalid settings: {error}") from None
    configure_logging(console_level=settings.console_log_level, file_path=settings.log_file_path)

    demo = initialize(AuditLog(settings.audit_log_path))
    simulation = simulate(demo)
    show_logging(demo, simulation)
    show_client(demo, simulation, "oleg")
    show_reports(demo)


if __name__ == "__main__":
    main()
