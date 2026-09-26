"""ReportBuilder: the client, bank and risk reports and their files.

The numbers come from the services (``BankReport``, ``AuditReport``, the
transaction history); the builder only picks them, lays them out as a
``Report`` and hands the report to an exporter.
"""

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from decimal import Decimal
from itertools import count
from pathlib import Path

from exceptions import InvalidOperationError
from models import AccountStatus, BankAccount, Currency, Transaction, TransactionStatus
from reporting.charts import ChartRenderer
from reporting.exporters import CsvExporter, JsonExporter, ReportExporter, TextExporter
from reporting.report import (
    BarChart,
    Chart,
    KeyValueSection,
    LineChart,
    PieChart,
    Report,
    ReportKind,
    Section,
    TableSection,
)
from services import AuditReport, BalanceMovement, Bank, BankReport, RiskLevel
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
    - ``save_charts()`` draws the report's charts, one PNG each
      (``..._bank_top_clients.png``); a chart with nothing to draw is skipped.

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
        self._charts = ChartRenderer()

    @property
    def output_dir(self) -> Path:
        return self._output_dir

    def _in_base(self, amount: Decimal, currency: Currency) -> Decimal:
        return self._bank.converter.to_base(amount, currency)

    def _report(
        self, kind: ReportKind, title: str, sections: Iterable[Section], charts: Iterable[Chart] = ()
    ) -> Report:
        return Report(
            kind=kind,
            title=title,
            generated_at=self._bank.now(),
            currency=self._bank.base_currency,
            sections=tuple(sections),
            charts=tuple(charts),
        )

    def _balance_steps(
        self, movements: Iterable[BalanceMovement], since: datetime | None, until: datetime | None
    ) -> list[tuple[datetime, Decimal]]:
        """The total balance of the accounts in ``movements``, in the base currency, after each moment of a period.

        The first point is the total at ``since`` when money was there before
        it; the last value is repeated at the end of the period (``until``,
        or now), so the last step reaches it.

        This is the cash on the accounts (``balance_after``): an investment
        portfolio is not a movement, so it is not in the history, while
        ``total_value`` elsewhere in the reports includes it. Past balances
        are converted at the bank's current rates, not at the rates of
        their day.
        """
        latest: dict[str, Decimal] = {}  # the balance of each account after its latest movement
        points: dict[datetime, Decimal] = {}  # one point per moment: the total after all its movements
        for movement in movements:
            if until is not None and movement.moment >= until:
                break
            latest[movement.account_id] = self._in_base(movement.balance_after, movement.currency)
            moment = movement.moment if since is None or movement.moment >= since else since
            points[moment] = sum(latest.values(), Decimal("0.00"))
        end = self._bank.now() if until is None else min(until, self._bank.now())
        if points and end > max(points):
            points[end] = points[max(points)]
        return list(points.items())

    def _balance_lines(
        self,
        accounts: list[BankAccount],
        names: Mapping[str, str],
        values: Mapping[str, Decimal],
        since: datetime | None,
        until: datetime | None,
    ) -> dict[str, list[tuple[datetime, Decimal]]]:
        """One balance line per account; past ``LineChart.MAX_SERIES`` the smallest by value share one summed line."""
        limit = LineChart.MAX_SERIES
        if len(accounts) > limit:
            accounts_by_value = sorted(accounts, key=lambda account: -values[account.account_id])
            kept = {account.account_id for account in accounts_by_value[: limit - 1]}
        else:
            kept = {account.account_id for account in accounts}
        history = self._bank.history
        lines = {
            names[account.account_id]: self._balance_steps(history.movements(account.account_id), since, until)
            for account in accounts
            if account.account_id in kept
        }
        rest = {account.account_id for account in accounts} - kept
        if rest:
            movements = [movement for movement in history.movements() if movement.account_id in rest]
            lines[f"Other {len(rest)} accounts"] = self._balance_steps(movements, since, until)
        return lines

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
        statuses = Counter(transaction.status for transaction in transactions)
        values = {account.account_id: self._in_base(account.total_value, account.currency) for account in accounts}

        summary = KeyValueSection(
            "summary",
            "Summary",
            {
                "client_id": client.client_id,
                "full_name": client.full_name,
                "status": client.status,
                "accounts": len(accounts),
                "total_value": sum(values.values(), Decimal("0.00")),
                "period_start": since,
                "period_end": until,
                "transactions": len(transactions),
                "completed": statuses[TransactionStatus.COMPLETED],
                "failed": statuses[TransactionStatus.FAILED],
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
                    values[account.account_id],
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
        base = self._bank.base_currency.value
        names = {
            account.account_id: f"{account.account_type} {account.currency.value} {account.account_id[:8]}"
            for account in accounts
        }
        charts = (
            PieChart(
                "assets",
                "Assets by account",
                base,
                labels=[names[account.account_id] for account in accounts],
                values=[values[account.account_id] for account in accounts],
            ),
            BarChart(
                "transactions_by_status",
                "Transactions by status",
                "transactions",
                # the order of the statuses, not of the transactions, so the charts of two clients compare
                labels=[status.value for status in TransactionStatus if statuses[status]],
                values=[statuses[status] for status in TransactionStatus if statuses[status]],
            ),
            LineChart(
                "balance",
                "Balance by account",
                base,
                self._balance_lines(accounts, names, values, since, until),
            ),
        )
        return self._report(
            ReportKind.CLIENT,
            f"Client report: {client.full_name}",
            (summary, accounts_table, transactions_table, statement, risk),
            charts,
        )

    def bank_report(self, *, top: int = 3, since: datetime | None = None, until: datetime | None = None) -> Report:
        """The whole bank: totals, balances by currency and account type, transactions, top clients, balance history.

        The period (``since`` inclusive, ``until`` exclusive, both open by
        default) applies to the history of the total balance; everything
        else is the bank as it is now and all its transactions.
        """
        _check_period(since, until)
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
        steps = self._balance_steps(self._bank.history.movements(), since, until)
        history = TableSection("balance_history", "Total balance over time", ("moment", "total_in_base"), steps)
        base = self._bank.base_currency.value
        charts = (
            PieChart(
                "balance_by_currency",
                "Balance by currency",
                base,
                labels=[currency.value for currency in balance.by_currency],
                values=[amount_in_base for _, _, amount_in_base in by_currency.rows],
            ),
            BarChart(
                "transactions_by_type",
                "Transactions by type",
                "transactions",
                labels=[kind.value for kind in statistics.by_type],
                values=list(statistics.by_type.values()),
            ),
            BarChart(
                "top_clients",
                top_clients.title,
                base,
                labels=[client.full_name for client, _ in ranking.clients],
                values=[total for _, total in ranking.clients],
            ),
            LineChart("total_balance", "Total balance of the bank", base, {"Total": steps}),
        )
        return self._report(
            ReportKind.BANK,
            "Bank report",
            (summary, by_currency, by_type, by_status, by_transaction_type, top_clients, history),
            charts,
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
        factors = Counter(rule for assessment in assessed.operations for rule in assessment.rules).most_common()
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
        factor_table = TableSection("risk_factors", "Risk factors", ("factor", "count"), factors)
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
        charts = (
            PieChart(
                "assessments_by_level",
                by_level.title,
                "transactions",
                labels=[level.name.lower() for level in RiskLevel],
                values=[levels[level] for level in RiskLevel],
            ),
            BarChart(
                "risk_factors",
                factor_table.title,
                "transactions",
                labels=[factor for factor, _ in factors],
                values=[times for _, times in factors],
            ),
            BarChart(
                "errors_by_type",
                error_types.title,
                "attempts",
                labels=list(errors.errors_by_type),
                values=list(errors.errors_by_type.values()),
            ),
        )
        return self._report(
            ReportKind.RISK,
            "Risk report",
            (summary, by_level, factor_table, operations, clients, error_types, security),
            charts,
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

    def save_charts(self, report: Report) -> list[Path]:
        """One PNG image per chart of the report that has something to draw, in the order of the charts."""
        self._check_report(report)
        images = {f"_{chart.name}": self._charts.to_png(chart) for chart in report.charts if not chart.is_empty}
        return self._write(report, images, ".png")

    @staticmethod
    def _check_report(report: object) -> None:
        if not isinstance(report, Report):
            raise InvalidOperationError("report must be a Report instance.")

    def _save(self, report: Report, exporter: ReportExporter) -> list[Path]:
        self._check_report(report)
        return self._write(report, exporter.render(report), exporter.extension)

    def _write(self, report: Report, files: Mapping[str, str | bytes], extension: str) -> list[Path]:
        """Write ``files`` (name part -> content) under ``stem``, or ``stem-2``, ``stem-3``... when a name is taken.

        Text is written as UTF-8, as it is. The files of one call always
        share a name: when another process takes one of them while they are
        being written, the ones already written are removed and the next
        name is tried.
        """
        if not files:
            return []
        self._output_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{self._clock():{self.STAMP}}_{report.kind.value}"
        for attempt in count(1):
            name = stem if attempt == 1 else f"{stem}-{attempt}"
            paths = [self._output_dir / f"{name}{part}{extension}" for part in files]
            if not any(path.exists() for path in paths) and self._write_all(paths, files.values()):
                return paths
        raise AssertionError("unreachable: count() never ends")

    @staticmethod
    def _write_all(paths: list[Path], contents: Iterable[str | bytes]) -> bool:
        """Write every file, or none: False when one of the names turned out to be taken."""
        written: list[Path] = []
        try:
            for path, content in zip(paths, contents, strict=True):
                # "x" refuses to overwrite a file that appeared after the check
                with path.open("xb") as file:
                    written.append(path)
                    file.write(content.encode("utf-8") if isinstance(content, str) else content)
        except FileExistsError:
            for path in written:
                path.unlink(missing_ok=True)
            return False
        return True
