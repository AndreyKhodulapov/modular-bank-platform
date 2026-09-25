"""Bank reports built from the bank's state: read-only views, no state of their own.

Every amount is in the bank's base currency, converted at the bank's rates.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from types import MappingProxyType

from exceptions import InvalidOperationError
from models import AccountStatus, Client, Currency, Transaction, TransactionStatus, TransactionType
from services.audit_log import TransactionEvent
from services.bank import Bank


def _freeze_mappings(report: object, *names: str) -> None:
    """Replace the named mappings of a frozen report with read-only copies, so the report is really unchangeable."""
    for name in names:
        object.__setattr__(report, name, MappingProxyType(dict(getattr(report, name))))


@dataclass(frozen=True)
class TransactionStatistics:
    """What happened to the transactions the bank has seen.

    - ``by_status`` counts the finished transactions of the history
      (completed, failed) and the cancelled ones, which never ran and are
      known only from the ``transaction_cancelled`` events of the bank's
      audit log: a queue records them only when it is given that log
      (``TransactionQueue(..., audit_log=bank.audit_log)``);
    - ``by_type`` counts the finished transactions only;
    - ``volume``, ``average_amount`` and ``largest`` cover the completed
      transactions, by their amount in the base currency;
    - ``tariff_fees`` sums the fees of the tariff (``FeePolicy``) charged on
      completed transactions; a premium account's own withdrawal fee is a
      term of the account, is part of the debit and is not in this sum;
    - ``blocked_by_risk`` counts the failed transactions refused by risk
      control; ``failure_rate`` is the share of finished transactions that
      failed, in percent.
    """

    currency: Currency
    by_status: Mapping[TransactionStatus, int]
    by_type: Mapping[TransactionType, int]
    volume: Decimal
    average_amount: Decimal
    largest: Transaction | None
    tariff_fees: Decimal
    blocked_by_risk: int
    failure_rate: Decimal

    def __post_init__(self) -> None:
        _freeze_mappings(self, "by_status", "by_type")

    @property
    def total(self) -> int:
        return sum(self.by_status.values())

    def __str__(self) -> str:
        code = self.currency.value
        statuses = ", ".join(f"{status.value} {count}" for status, count in self.by_status.items())
        types = ", ".join(f"{kind.value} {count}" for kind, count in self.by_type.items())
        largest = "-"
        if self.largest is not None:
            largest = f"{self.largest.amount} {self.largest.currency.value} ({self.largest.transaction_type.value})"
        return "\n".join(
            [
                f"Transactions: {self.total} ({statuses})",
                f"  by type: {types}",
                f"  failure rate {self.failure_rate}%, blocked by risk control {self.blocked_by_risk}",
                f"  volume {self.volume} {code}, average {self.average_amount} {code}, largest {largest}",
                f"  tariff fees collected {self.tariff_fees} {code}",
            ]
        )


@dataclass(frozen=True)
class ClientRanking:
    """The richest clients by the total value of their accounts, richest first."""

    currency: Currency
    clients: tuple[tuple[Client, Decimal], ...]

    def __str__(self) -> str:
        lines = [f"Top {len(self.clients)} clients"]
        lines.extend(
            f"  {place}. {client.full_name:<30} {total:>14} {self.currency.value}"
            for place, (client, total) in enumerate(self.clients, start=1)
        )
        return "\n".join(lines)


@dataclass(frozen=True)
class BalanceSummary:
    """Everything the bank holds for its clients.

    ``by_currency`` is in each currency itself, ``total`` in the base
    currency; portfolios count at their invested amount, an overdraft as a
    negative value. ``accounts`` and ``by_currency`` cover the open accounts
    (active and frozen): a closed account holds nothing.
    """

    currency: Currency
    total: Decimal
    by_currency: Mapping[Currency, Decimal]
    accounts: int

    def __post_init__(self) -> None:
        _freeze_mappings(self, "by_currency")

    def __str__(self) -> str:
        lines = [f"Total balance: {self.total} {self.currency.value} on {self.accounts} accounts"]
        lines.extend(f"  {amount:>14} {currency.value}" for currency, amount in self.by_currency.items())
        return "\n".join(lines)


class BankReport:
    """Builds the bank reports; every call reads the current state of the bank."""

    def __init__(self, bank: Bank) -> None:
        if not isinstance(bank, Bank):
            raise InvalidOperationError("bank must be a Bank instance.")
        self._bank = bank

    def _in_base(self, amount: Decimal, currency: Currency) -> Decimal:
        return self._bank.converter.to_base(amount, currency)

    def transaction_statistics(self) -> TransactionStatistics:
        history = self._bank.history
        finished = history.transactions()
        completed = [transaction for transaction in finished if transaction.status is TransactionStatus.COMPLETED]
        failed = [transaction for transaction in finished if transaction.status is TransactionStatus.FAILED]
        cancelled = {
            event.transaction_id for event in self._bank.audit_log.filter(event=TransactionEvent.CANCELLED.value)
        }

        amounts = {
            transaction.transaction_id: self._in_base(transaction.amount, transaction.currency)
            for transaction in completed
        }
        volume = sum(amounts.values(), Decimal("0.00"))
        average = volume / len(completed) if completed else Decimal(0)
        # the tariff fee is charged in the sender's account currency
        tariff_fees = sum(
            (
                self._in_base(transaction.fee, self._bank.get_account(transaction.sender_id).currency)
                for transaction in completed
                if transaction.fee
            ),
            Decimal("0.00"),
        )
        # a blocked transaction is not retried, so its latest assessment is the blocking one
        blocked_ids = {item.transaction_id for item in self._bank.risk_analyzer.assessments if item.blocked}
        rate = Decimal(100 * len(failed)) / len(finished) if finished else Decimal(0)
        types = Counter(transaction.transaction_type for transaction in finished)
        return TransactionStatistics(
            currency=self._bank.base_currency,
            by_status={
                TransactionStatus.COMPLETED: len(completed),
                TransactionStatus.FAILED: len(failed),
                TransactionStatus.CANCELLED: len(cancelled),
            },
            by_type={kind: types[kind] for kind in TransactionType if types[kind]},
            volume=volume,
            average_amount=average.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            largest=max(completed, key=lambda transaction: amounts[transaction.transaction_id], default=None),
            tariff_fees=tariff_fees,
            blocked_by_risk=sum(1 for transaction in failed if transaction.transaction_id in blocked_ids),
            failure_rate=rate.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP),
        )

    def top_clients(self, limit: int = 3) -> ClientRanking:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise InvalidOperationError(f"limit must be a positive integer; got {limit!r}.")
        return ClientRanking(
            currency=self._bank.base_currency,
            clients=tuple(self._bank.get_clients_ranking()[:limit]),
        )

    def total_balance(self) -> BalanceSummary:
        accounts = [account for account in self._bank.search_accounts() if account.status is not AccountStatus.CLOSED]
        by_currency: dict[Currency, Decimal] = {}
        for account in accounts:
            by_currency[account.currency] = by_currency.get(account.currency, Decimal("0.00")) + account.total_value
        return BalanceSummary(
            currency=self._bank.base_currency,
            total=self._bank.get_total_balance(),
            by_currency=dict(sorted(by_currency.items(), key=lambda item: item[0].value)),
            accounts=len(accounts),
        )
