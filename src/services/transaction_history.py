"""Transaction history: finished transactions and every movement of money on the accounts."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from exceptions import InvalidOperationError
from models.enums import Currency, TransactionStatus, TransactionType
from models.transaction import Transaction
from utils import to_enum, to_money


class MovementKind(Enum):
    """Why an account balance changed."""

    OPENING = "opening"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    REFUND = "refund"
    PAYOUT = "payout"
    INTEREST = "interest"
    INVESTMENT = "investment"  # free cash moved into the portfolio
    DIVESTMENT = "divestment"  # money moved from the portfolio back to free cash


@dataclass(frozen=True)
class BalanceMovement:
    """One change of an account balance; immutable once recorded.

    ``amount`` is signed and in the account's ``currency``: a credit is
    positive, a debit negative, with every fee the account charged.
    ``balance_after`` is the balance right after the change, so the
    movements of an account draw its balance over time without replaying
    them. ``total_value_after`` is everything the account was worth at that
    moment (``total_value``): the same as ``balance_after``, except for an
    investment account, whose portfolio is added, so moving money between
    its cash and its portfolio changes the balance but not the value.
    ``transaction_id`` links the movement to the transaction that caused
    it; ``None`` for a back-office operation of the bank.
    """

    moment: datetime
    account_id: str
    kind: MovementKind
    amount: Decimal
    currency: Currency
    balance_after: Decimal
    total_value_after: Decimal
    transaction_id: str | None = None

    def __str__(self) -> str:
        return (
            f"{self.moment:%m-%d %H:%M} {self.account_id[:8]:<8} {self.kind.value:<10} "
            f"{self.amount:>+12} {self.balance_after:>12} {self.currency.value}"
        )


def _within(moment: datetime, since: datetime | None, until: datetime | None) -> bool:
    return (since is None or moment >= since) and (until is None or moment < until)


class TransactionHistory:
    """What happened to the money: finished transactions and balance movements, in order.

    - A transaction enters the history once, when it reaches a final
      status after its last attempt: completed or failed. A cancelled one
      never ran, so it stays in the queue and the audit log only.
    - A transaction id is used once across the bank: the processor claims
      it here before the first attempt, so another transaction under a known
      id is refused before its money moves, whichever processor runs it.
      The refused one still enters the history as failed
      (``record_duplicate()``), without taking the id: the history counts
      every submission that finished, as the audit log does, and the id
      stays with the first transaction.
    - A movement is recorded by the bank whenever a balance changes through
      it: an account opened with money, a deposit, a withdrawal, a refund of
      a rolled-back debit, the payout on closing, monthly interest, and
      money moved into an investment portfolio or back.

    ``claimed_by()`` tells which transaction holds an id, and
    ``movements()`` filters by account, kind, transaction and time, so the
    bank can check that a refund puts back a debit that really happened.

    The history is the bank's record of state, not a log: it is kept in
    memory and read by the reports, while the logs describe what the
    system did.
    """

    FINAL_STATUSES = frozenset({TransactionStatus.COMPLETED, TransactionStatus.FAILED})

    def __init__(self) -> None:
        self._finished: list[Transaction] = []  # in the order they finished, refused duplicates included
        self._recorded: set[str] = set()  # ids whose holder is in the history
        self._claimed: dict[str, Transaction] = {}  # transaction_id -> the transaction that holds the id
        self._movements: list[BalanceMovement] = []

    def claim(self, transaction: Transaction) -> Transaction:
        """Take the transaction's id for it, before any attempt moves money.

        The same transaction may claim its id again, for a retry; another
        transaction under a known id is refused, whether the holder is still
        waiting for a retry or already finished.
        """
        if not isinstance(transaction, Transaction):
            raise InvalidOperationError("transaction must be a Transaction instance.")
        holder = self._claimed.setdefault(transaction.transaction_id, transaction)
        if holder is not transaction:
            raise InvalidOperationError(
                f"Transaction id {transaction.transaction_id} is already used by another transaction."
            )
        return transaction

    def claimed_by(self, transaction_id: str) -> Transaction | None:
        """The transaction that holds this id, finished or not; ``None`` for an id nobody claimed."""
        return self._claimed.get(transaction_id)

    def record_transaction(self, transaction: Transaction) -> Transaction:
        self.claim(transaction)
        if transaction.status not in self.FINAL_STATUSES:
            raise InvalidOperationError(
                f"Only completed or failed transactions enter the history; "
                f"{transaction.transaction_id} is {transaction.status.value}."
            )
        if transaction.transaction_id in self._recorded:
            raise InvalidOperationError(f"Transaction {transaction.transaction_id} is already in the history.")
        self._recorded.add(transaction.transaction_id)
        self._finished.append(transaction)
        return transaction

    def record_duplicate(self, transaction: Transaction) -> Transaction:
        """Record a transaction refused because another one holds its id; the id stays with that one.

        Only a failed transaction under an id claimed by another transaction
        is a refused duplicate, and it is recorded once.
        """
        if not isinstance(transaction, Transaction):
            raise InvalidOperationError("transaction must be a Transaction instance.")
        if transaction.status is not TransactionStatus.FAILED:
            raise InvalidOperationError(
                f"A refused duplicate is failed; {transaction.transaction_id} is {transaction.status.value}."
            )
        holder = self._claimed.get(transaction.transaction_id)
        if holder is None or holder is transaction:
            raise InvalidOperationError(
                f"Transaction {transaction.transaction_id} is not a duplicate: no other transaction holds its id."
            )
        if any(recorded is transaction for recorded in self._finished):
            raise InvalidOperationError(f"Transaction {transaction.transaction_id} is already in the history.")
        self._finished.append(transaction)
        return transaction

    def record_movement(
        self,
        *,
        moment: datetime,
        account_id: str,
        kind: MovementKind | str,
        amount: object,
        currency: Currency | str,
        balance_after: object,
        total_value_after: object,
        transaction_id: str | None = None,
    ) -> BalanceMovement:
        if not isinstance(moment, datetime):
            raise InvalidOperationError("moment must be a datetime.")
        change = to_money(amount)
        if change == 0:
            raise InvalidOperationError("A movement must change the balance.")
        movement = BalanceMovement(
            moment=moment,
            account_id=account_id,
            kind=to_enum(MovementKind, kind, field="movement kind"),
            amount=change,
            currency=to_enum(Currency, currency, field="currency"),
            balance_after=to_money(balance_after, field="balance_after"),
            total_value_after=to_money(total_value_after, field="total_value_after"),
            transaction_id=transaction_id,
        )
        self._movements.append(movement)
        return movement

    def transactions(
        self,
        *,
        account_ids: Iterable[str] | None = None,
        status: TransactionStatus | str | None = None,
        transaction_type: TransactionType | str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Transaction]:
        """Finished transactions matching every given filter, in the order they finished.

        ``account_ids`` matches a transaction sent from or to any of them, so
        one call returns both the outgoing and the incoming ones. The time
        range applies to ``finished_at``; ``since`` is inclusive, ``until``
        exclusive.
        """
        accounts = None if account_ids is None else frozenset(account_ids)
        status_filter = to_enum(TransactionStatus, status, field="transaction status") if status is not None else None
        type_filter = (
            to_enum(TransactionType, transaction_type, field="transaction type")
            if transaction_type is not None
            else None
        )
        return [
            transaction
            for transaction in self._finished
            if (accounts is None or not accounts.isdisjoint({transaction.sender_id, transaction.recipient_id}))
            and (status_filter is None or transaction.status is status_filter)
            and (type_filter is None or transaction.transaction_type is type_filter)
            and _within(transaction.finished_at, since, until)
        ]

    def movements(
        self,
        account_id: str | None = None,
        *,
        kind: MovementKind | str | None = None,
        transaction_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[BalanceMovement]:
        """Balance movements of one account (all accounts without ``account_id``), in order.

        ``kind`` and ``transaction_id`` keep only the movements of that kind
        and of that transaction. ``since`` is inclusive, ``until`` exclusive.
        """
        kind_filter = to_enum(MovementKind, kind, field="movement kind") if kind is not None else None
        return [
            movement
            for movement in self._movements
            if (account_id is None or movement.account_id == account_id)
            and (kind_filter is None or movement.kind is kind_filter)
            and (transaction_id is None or movement.transaction_id == transaction_id)
            and _within(movement.moment, since, until)
        ]
