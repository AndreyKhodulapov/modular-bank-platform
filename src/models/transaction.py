"""Transaction model: a request to move money with a strict status lifecycle."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from exceptions import InvalidOperationError, InvalidTransactionStateError
from models.enums import Currency, TransactionPriority, TransactionStatus, TransactionType
from utils import resolve_identifier, to_enum, to_money


class Transaction:
    """A single money movement between accounts.

    ``amount`` is expressed in ``currency``; the processor converts it into the
    currencies of the accounts involved and records what was actually debited
    and credited. ``sender_id`` and ``recipient_id`` are account ids of this
    bank, except the recipient of an ``EXTERNAL_TRANSFER``, which is an account
    number in another bank.

    The status changes only through ``start()``, ``complete()``, ``fail()``,
    ``retry()`` and ``cancel()``; each of them checks the transition against
    the status machine and stamps ``updated_at``:

        PENDING -> PROCESSING -> COMPLETED | FAILED
        PROCESSING -> PENDING   (a retry is scheduled)
        PENDING -> CANCELLED

    ``scheduled_at`` delays execution until that moment; ``None`` means as
    soon as possible.
    """

    # which parties each transaction type needs: (sender, recipient)
    PARTIES: dict[TransactionType, tuple[bool, bool]] = {
        TransactionType.DEPOSIT: (False, True),
        TransactionType.WITHDRAWAL: (True, False),
        TransactionType.TRANSFER: (True, True),
        TransactionType.EXTERNAL_TRANSFER: (True, True),
    }
    # the status machine: every allowed move from one status to the next
    TRANSITIONS: dict[TransactionStatus, frozenset[TransactionStatus]] = {
        TransactionStatus.PENDING: frozenset({TransactionStatus.PROCESSING, TransactionStatus.CANCELLED}),
        TransactionStatus.PROCESSING: frozenset(
            {TransactionStatus.COMPLETED, TransactionStatus.FAILED, TransactionStatus.PENDING}
        ),
        TransactionStatus.COMPLETED: frozenset(),
        TransactionStatus.FAILED: frozenset(),
        TransactionStatus.CANCELLED: frozenset(),
    }

    def __init__(
        self,
        transaction_type: TransactionType | str,
        amount: object,
        currency: Currency | str,
        *,
        sender_id: str | None = None,
        recipient_id: str | None = None,
        priority: TransactionPriority | str = TransactionPriority.NORMAL,
        scheduled_at: datetime | None = None,
        created_at: datetime | None = None,
        transaction_id: str | None = None,
    ) -> None:
        self._transaction_id = resolve_identifier(transaction_id, field="transaction_id")
        self._type = to_enum(TransactionType, transaction_type, field="transaction type")
        self._amount = to_money(amount, require="positive")
        self._currency = to_enum(Currency, currency, field="currency")
        self._priority = to_enum(TransactionPriority, priority, field="priority")
        self._sender_id, self._recipient_id = self._validate_parties(self._type, sender_id, recipient_id)

        self._created_at = self._validate_moment(created_at if created_at is not None else datetime.now(), "created_at")
        self._scheduled_at = None if scheduled_at is None else self._validate_moment(scheduled_at, "scheduled_at")
        self._updated_at = self._created_at
        self._finished_at: datetime | None = None

        self._status = TransactionStatus.PENDING
        self._attempts = 0
        self._failure_reason: str | None = None
        self._fee = Decimal("0.00")
        self._debited_amount: Decimal | None = None
        self._credited_amount: Decimal | None = None

    @staticmethod
    def _validate_parties(
        transaction_type: TransactionType, sender_id: str | None, recipient_id: str | None
    ) -> tuple[str | None, str | None]:
        needs_sender, needs_recipient = Transaction.PARTIES[transaction_type]

        def party(field: str, value: str | None, needed: bool) -> str | None:
            if needed and value is None:
                raise InvalidOperationError(f"A {transaction_type.value} needs {field}.")
            if not needed and value is not None:
                raise InvalidOperationError(f"A {transaction_type.value} has no {field}.")
            return None if value is None else resolve_identifier(value, field=field)

        sender = party("sender_id", sender_id, needs_sender)
        recipient = party("recipient_id", recipient_id, needs_recipient)
        if sender is not None and sender == recipient:
            raise InvalidOperationError("Sender and recipient must be different accounts.")
        return sender, recipient

    @staticmethod
    def _validate_moment(value: object, field: str) -> datetime:
        if not isinstance(value, datetime):
            raise InvalidOperationError(f"{field} must be a datetime.")
        return value

    # read-only state

    @property
    def transaction_id(self) -> str:
        return self._transaction_id

    @property
    def transaction_type(self) -> TransactionType:
        return self._type

    @property
    def amount(self) -> Decimal:
        return self._amount

    @property
    def currency(self) -> Currency:
        return self._currency

    @property
    def fee(self) -> Decimal:
        """Commission charged by the bank, in the sender's account currency."""
        return self._fee

    @property
    def sender_id(self) -> str | None:
        return self._sender_id

    @property
    def recipient_id(self) -> str | None:
        return self._recipient_id

    @property
    def priority(self) -> TransactionPriority:
        return self._priority

    @property
    def status(self) -> TransactionStatus:
        return self._status

    @property
    def failure_reason(self) -> str | None:
        """Why the last attempt failed; kept on a retry so the cause stays visible."""
        return self._failure_reason

    @property
    def attempts(self) -> int:
        return self._attempts

    @property
    def debited_amount(self) -> Decimal | None:
        """What left the sender's account in its currency, including every fee."""
        return self._debited_amount

    @property
    def credited_amount(self) -> Decimal | None:
        """What reached the recipient's account in its currency."""
        return self._credited_amount

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def scheduled_at(self) -> datetime | None:
        return self._scheduled_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    @property
    def finished_at(self) -> datetime | None:
        """When the transaction reached a final status (completed, failed or cancelled)."""
        return self._finished_at

    @property
    def is_final(self) -> bool:
        return not self.TRANSITIONS[self._status]

    def is_due(self, now: datetime) -> bool:
        return self._scheduled_at is None or self._scheduled_at <= now

    # status transitions

    def _move_to(self, target: TransactionStatus, at: datetime) -> None:
        moment = self._validate_moment(at, "at")
        if target not in self.TRANSITIONS[self._status]:
            raise InvalidTransactionStateError(self._transaction_id, self._status.value, target.value)
        self._status = target
        self._updated_at = moment
        if self.is_final:
            self._finished_at = moment

    def start(self, at: datetime) -> None:
        """Begin an attempt: PENDING -> PROCESSING."""
        self._move_to(TransactionStatus.PROCESSING, at)
        self._attempts += 1

    def complete(
        self, at: datetime, *, fee: Decimal, debited_amount: Decimal | None, credited_amount: Decimal | None
    ) -> None:
        self._move_to(TransactionStatus.COMPLETED, at)
        self._fee = fee
        self._debited_amount = debited_amount
        self._credited_amount = credited_amount
        self._failure_reason = None

    def fail(self, reason: str, at: datetime) -> None:
        self._move_to(TransactionStatus.FAILED, at)
        self._failure_reason = reason

    def retry(self, reason: str, at: datetime, next_attempt_at: datetime) -> None:
        """Put the transaction back to PENDING and delay the next attempt."""
        next_attempt = self._validate_moment(next_attempt_at, "next_attempt_at")
        self._move_to(TransactionStatus.PENDING, at)
        self._failure_reason = reason
        self._scheduled_at = next_attempt

    def cancel(self, at: datetime) -> None:
        self._move_to(TransactionStatus.CANCELLED, at)

    # presentation

    def to_dict(self) -> dict[str, Any]:
        def optional(value: Decimal | datetime | None) -> str | None:
            if value is None:
                return None
            return value.isoformat() if isinstance(value, datetime) else str(value)

        return {
            "transaction_id": self._transaction_id,
            "type": self._type.value,
            "amount": str(self._amount),
            "currency": self._currency.value,
            "fee": str(self._fee),
            "sender_id": self._sender_id,
            "recipient_id": self._recipient_id,
            "priority": self._priority.name.lower(),
            "status": self._status.value,
            "failure_reason": self._failure_reason,
            "attempts": self._attempts,
            "debited_amount": optional(self._debited_amount),
            "credited_amount": optional(self._credited_amount),
            "created_at": optional(self._created_at),
            "scheduled_at": optional(self._scheduled_at),
            "updated_at": optional(self._updated_at),
            "finished_at": optional(self._finished_at),
        }

    def __repr__(self) -> str:
        return (
            f"Transaction(transaction_id={self._transaction_id!r}, type={self._type.value!r}, "
            f"amount={self._amount}, currency={self._currency.value!r}, status={self._status.value!r})"
        )

    def __str__(self) -> str:
        return (
            f"{self._type.value:<17} {self._amount:>10} {self._currency.value} | "
            f"{self._priority.name.lower():<6} | {self._status.value}"
        )
