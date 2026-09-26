"""ReportBuilder: the client, bank and risk reports and their files.

The numbers come from the services (``BankReport``, ``AuditReport``, the
transaction history); the builder only picks them, lays them out as a
``Report`` and hands the report to an exporter.
"""

from collections import Counter
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from itertools import count
from pathlib import Path

from exceptions import InvalidOperationError
from models import AccountStatus, Currency, Transaction
from reporting.exporters import CsvExporter, JsonExporter, ReportExporter, TextExporter
from reporting.report import KeyValueSection, Report, ReportKind, Section, TableSection
from services import AuditReport, Bank, BankReport, RiskLevel
from utils import to_enum


def _check_period(since: datetime | None, until: datetime | None) -> None:
    for name, moment in (("since", since), ("until", until)):
        if moment is not None and not isinstance(moment, datetime):
            raise InvalidOperationError(f"{name} must be a datetime or None.")
    if since is not None and until is not None and since >= until:
        raise InvalidOperationError(f"since must be earlier than until; got {since} and {until}.")


class ReportBuilder:
    """Builds the reports of a bank and writes them into ``output_dir``.

    - ``client_report()``, ``bank_report()`` and ``risk_report()`` read the
      bank as it is now and return a ``Report``; nothing is written.
    - ``to_text()`` returns a report as text; ``export_to_text()``,
      ``export_to_json()`` and ``export_to_csv()`` write it to files named
      ``<date>_<time>_<kind>`` by the moment of the call, e.g.
      ``2026-09-26_14-30-05_bank.json``. CSV gives one file per section
      (``..._bank_top_clients.csv``). All files of one call share the name;
      a name already taken gets ``-2``, ``-3``, so nothing is overwritten.

    ``clock`` names the files, so it is the wall clock by default, while a
    report's ``generated_at`` is the bank's own time.
    """

    STAMP = "%Y-%m-%d_%H-%M-%S"

    def __init__(self, bank: Bank, output_dir: Path | str, *, clock: Callable[[], datetime] = datetime.now) -> None:
        if not isinstance(bank, Bank):
            raise InvalidOperationError("bank must be a Bank instance.")
        self._bank = bank
        self._output_dir = Path(output_dir)
        self._clock = clock
        self._bank_report = BankReport(bank)
        self._audit_report = AuditReport(bank.audit_log, bank.risk_analyzer)
        self._text = TextExporter()
        self._json = JsonExporter()
        self._csv = CsvExporter()

    @property
    def output_dir(self) -> Path:
        return self._output_dir

    def _in_base(self, amount: Decimal, currency: Currency) -> Decimal:
        return self._bank.converter.to_base(amount, currency)

    def _report(self, kind: ReportKind, title: str, *sections: Section) -> Report:
        return Report(
            kind=kind,
            title=title,
            generated_at=self._bank.now(),
            currency=self._bank.base_currency,
            sections=sections,
        )

    # --- reports

    @staticmethod
    def _counterparty(transaction: Transaction, account_ids: set[str]) -> tuple[str, str | None]:
        """How a transaction looks from the client's side, and the account on the other side."""
        if transaction.sender_id in account_ids:
            direction = "internal" if transaction.recipient_id in account_ids else "outgoing"
            return direction, transaction.recipient_id
        return "incoming", transaction.sender_id

    def client_report(self, client_id: str, *, since: datetime | None = None, until: datetime | None = None) -> Report:
        """A client's accounts now, their transactions and statement for a period, and their risk profile.

        The period (``since`` inclusive, ``until`` exclusive, both open by
        default) applies to the transactions and the statement; the accounts
        are shown as they are now and the risk profile covers everything.
        """
        _check_period(since, until)
        client = self._bank.get_client(client_id)
        accounts = self._bank.search_accounts(client_id=client.client_id)
        account_ids = {account.account_id for account in accounts}
        history = self._bank.history
        transactions = history.transactions(account_ids=account_ids, since=since, until=until)
        movements = [
            movement for movement in history.movements(since=since, until=until) if movement.account_id in account_ids
        ]
        profile = self._audit_report.client_risk_profile(client.client_id)
        statuses = Counter(transaction.status.value for transaction in transactions)

        summary = KeyValueSection(
            "summary",
            "Summary",
            {
                "client_id": client.client_id,
                "full_name": client.full_name,
                "status": client.status,
                "accounts": len(accounts),
                "total_value": sum(
                    (self._in_base(account.total_value, account.currency) for account in accounts), Decimal("0.00")
                ),
                "period_start": since,
                "period_end": until,
                "transactions": len(transactions),
                "completed": statuses["completed"],
                "failed": statuses["failed"],
            },
        )
        accounts_table = TableSection(
            "accounts",
            "Accounts",
            (
                "account_id",
                "account_type",
                "currency",
                "status",
                "opened_at",
                "balance",
                "total_value",
                "value_in_base",
            ),
            [
                (
                    account.account_id,
                    account.account_type,
                    account.currency,
                    account.status,
                    self._bank.account_opened_at(account.account_id),
                    account.balance,
                    account.total_value,
                    self._in_base(account.total_value, account.currency),
                )
                for account in accounts
            ],
        )
        transaction_rows = []
        for transaction in transactions:
            direction, counterparty = self._counterparty(transaction, account_ids)
            transaction_rows.append(
                (
                    transaction.finished_at,
                    transaction.transaction_id,
                    transaction.transaction_type,
                    direction,
                    transaction.status,
                    transaction.amount,
                    transaction.currency,
                    transaction.fee,
                    counterparty,
                    transaction.failure_reason,
                )
            )
        transactions_table = TableSection(
            "transactions",
            "Transactions",
            (
                "finished_at",
                "transaction_id",
                "transaction_type",
                "direction",
                "status",
                "amount",
                "currency",
                "fee",
                "counterparty",
                "failure_reason",
            ),
            transaction_rows,
        )
        statement = TableSection(
            "statement",
            "Statement",
            ("moment", "account_id", "kind", "amount", "currency", "balance_after", "transaction_id"),
            [
                (
                    movement.moment,
                    movement.account_id,
                    movement.kind,
                    movement.amount,
                    movement.currency,
                    movement.balance_after,
                    movement.transaction_id,
                )
                for movement in movements
            ],
        )
        risk = KeyValueSection(
            "risk_profile",
            "Risk profile",
            {
                "level": profile.level,
                "assessed_transactions": profile.transactions,
                "blocked": profile.blocked,
                "max_score": profile.max_score,
                "average_score": profile.average_score,
                "factors": ", ".join(f"{rule} x{times}" for rule, times in profile.top_factors) or None,
                "security_events": profile.security_events,
                "failed_attempts": profile.failed_attempts,
            },
        )
        return self._report(
            ReportKind.CLIENT,
            f"Client report: {client.full_name}",
            summary,
            accounts_table,
            transactions_table,
            statement,
            risk,
        )

    def bank_report(self, *, top: int = 3) -> Report:
        """The whole bank: totals, balances by currency and account type, transactions and the top clients."""
        statistics = self._bank_report.transaction_statistics()
        balance = self._bank_report.total_balance()
        ranking = self._bank_report.top_clients(top)
        largest = statistics.largest
        summary = KeyValueSection(
            "summary",
            "Summary",
            {
                "clients": len(self._bank.clients),
                "open_accounts": balance.accounts,
                "total_balance": balance.total,
                "transactions": statistics.total,
                "finished": statistics.finished,
                "failure_rate_percent": statistics.failure_rate,
                "blocked_by_risk": statistics.blocked_by_risk,
                "volume": statistics.volume,
                "average_amount": statistics.average_amount,
                "largest_transaction_id": largest.transaction_id if largest else None,
                "largest_amount": largest.amount if largest else None,
                "largest_currency": largest.currency if largest else None,
                "tariff_fees": statistics.tariff_fees,
            },
        )
        by_currency = TableSection(
            "balance_by_currency",
            "Balance by currency",
            ("currency", "amount", "amount_in_base"),
            [(currency, amount, self._in_base(amount, currency)) for currency, amount in balance.by_currency.items()],
        )
        accounts = self._bank.search_accounts()
        type_rows = []
        for account_class in Bank.ACCOUNT_TYPES.values():
            of_type = [account for account in accounts if type(account) is account_class]
            if not of_type:
                continue
            statuses = Counter(account.status for account in of_type)
            type_rows.append(
                (
                    account_class.__name__,
                    *(statuses[status] for status in AccountStatus),
                    sum((self._in_base(item.total_value, item.currency) for item in of_type), Decimal("0.00")),
                )
            )
        by_type = TableSection(
            "accounts_by_type",
            "Accounts by type",
            ("account_type", *(status.value for status in AccountStatus), "value_in_base"),
            type_rows,
        )
        by_status = TableSection(
            "transactions_by_status",
            "Transactions by status",
            ("status", "count"),
            statistics.by_status.items(),
        )
        by_transaction_type = TableSection(
            "transactions_by_type",
            "Transactions by type",
            ("transaction_type", "count"),
            statistics.by_type.items(),
        )
        top_clients = TableSection(
            "top_clients",
            f"Top {len(ranking.clients)} clients",
            ("place", "client_id", "full_name", "total_value"),
            [
                (place, client.client_id, client.full_name, total)
                for place, (client, total) in enumerate(ranking.clients, start=1)
            ],
        )
        return self._report(
            ReportKind.BANK,
            "Bank report",
            summary,
            by_currency,
            by_type,
            by_status,
            by_transaction_type,
            top_clients,
        )

    def risk_report(self, *, min_level: RiskLevel | str = RiskLevel.MEDIUM) -> Report:
        """Risk control and errors: assessments by level, suspicious operations, clients by risk, failures.

        Every transaction counts by its latest assessment; ``min_level``
        chooses which of them are listed as suspicious.
        """
        lowest = to_enum(RiskLevel, min_level, field="risk level")
        # the lowest level gives the latest assessment of every transaction
        assessed = self._audit_report.suspicious_operations(RiskLevel.LOW)
        suspicious = [assessment for assessment in assessed.operations if assessment.level >= lowest]
        errors = self._audit_report.error_statistics()
        levels = Counter(assessment.level for assessment in assessed.operations)
        profiles = sorted(
            ((client, self._audit_report.client_risk_profile(client.client_id)) for client in self._bank.clients),
            key=lambda item: (-item[1].level, -item[1].max_score, item[0].full_name),
        )
        summary = KeyValueSection(
            "summary",
            "Summary",
            {
                "min_level": lowest,
                "assessed": len(assessed.operations),
                "suspicious": len(suspicious),
                "blocked_by_risk": errors.blocked_by_risk,
                "security_events": len(assessed.security_events),
                "completed": errors.completed,
                "final_failures": errors.final_failures,
                "failure_rate_percent": errors.failure_rate,
                "failed_attempts": errors.failed_attempts,
                "retried": errors.retried,
                **{f"events_{level.name.lower()}": total for level, total in errors.events_by_level.items()},
            },
        )
        by_level = TableSection(
            "assessments_by_level",
            "Assessments by risk level",
            ("level", "count"),
            [(level, levels[level]) for level in RiskLevel],
        )
        operations = TableSection(
            "suspicious_operations",
            f"Suspicious operations ({lowest.name.lower()} risk and above)",
            ("moment", "transaction_id", "client_id", "amount_in_base", "score", "level", "action", "factors"),
            [
                (
                    assessment.moment,
                    assessment.transaction_id,
                    assessment.client_id,
                    assessment.amount_in_base,
                    assessment.score,
                    assessment.level,
                    "blocked" if assessment.blocked else "allowed",
                    ", ".join(assessment.rules),
                )
                for assessment in suspicious
            ],
        )
        clients = TableSection(
            "client_risk",
            "Clients by risk",
            (
                "client_id",
                "full_name",
                "level",
                "transactions",
                "blocked",
                "max_score",
                "average_score",
                "security_events",
                "failed_attempts",
            ),
            [
                (
                    client.client_id,
                    client.full_name,
                    profile.level,
                    profile.transactions,
                    profile.blocked,
                    profile.max_score,
                    profile.average_score,
                    profile.security_events,
                    profile.failed_attempts,
                )
                for client, profile in profiles
            ],
        )
        error_types = TableSection(
            "errors_by_type",
            "Failed attempts by error type",
            ("error_type", "count"),
            errors.errors_by_type.items(),
        )
        security = TableSection(
            "security_events",
            "Security events",
            ("timestamp", "level", "event", "client_id", "account_id", "message"),
            [
                (event.timestamp, event.level, event.event, event.client_id, event.account_id, event.message)
                for event in assessed.security_events
            ],
        )
        return self._report(
            ReportKind.RISK, "Risk report", summary, by_level, operations, clients, error_types, security
        )

    # --- output

    def to_text(self, report: Report) -> str:
        return self._text.to_text(report)

    def export_to_text(self, report: Report) -> Path:
        return self._save(report, self._text)[0]

    def export_to_json(self, report: Report) -> Path:
        return self._save(report, self._json)[0]

    def export_to_csv(self, report: Report) -> list[Path]:
        """One CSV file per section of the report, in the order of the sections."""
        return self._save(report, self._csv)

    def _save(self, report: Report, exporter: ReportExporter) -> list[Path]:
        if not isinstance(report, Report):
            raise InvalidOperationError("report must be a Report instance.")
        files = exporter.render(report)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        paths = self._free_paths(f"{self._clock():{self.STAMP}}_{report.kind.value}", files, exporter.extension)
        for path, content in zip(paths, files.values(), strict=True):
            # "x" refuses to overwrite a file that appeared in the meantime
            with path.open("x", encoding="utf-8", newline="") as file:
                file.write(content)
        return paths

    def _free_paths(self, stem: str, parts: dict[str, str], extension: str) -> list[Path]:
        """Paths for ``parts`` under ``stem``, or under ``stem-2``, ``stem-3``... when one of them is taken."""
        for attempt in count(1):
            name = stem if attempt == 1 else f"{stem}-{attempt}"
            paths = [self._output_dir / f"{name}{part}{extension}" for part in parts]
            if not any(path.exists() for path in paths):
                return paths
        raise AssertionError("unreachable: count() never ends")
