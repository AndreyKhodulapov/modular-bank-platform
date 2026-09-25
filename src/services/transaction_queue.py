"""A priority queue of pending transactions with delayed execution and cancellation."""

import heapq
import itertools
import logging
from collections.abc import Callable
from datetime import datetime

from exceptions import InvalidOperationError, TransactionNotFoundError
from models.enums import TransactionStatus
from models.transaction import Transaction
from services.audit_log import AuditCategory, AuditLevel, AuditLog, TransactionEvent

_logger = logging.getLogger("bank.queue")


class TransactionQueue:
    """Holds pending transactions and hands them out in execution order.

    Two binary heaps keep the order cheap to maintain:

    - ``_ready`` - transactions that may run now, ordered by priority (higher
      first) and then by the order they were added (FIFO within a priority);
    - ``_delayed`` - transactions with a future ``scheduled_at``, ordered by
      that moment. ``next_ready()`` moves the ones that became due into
      ``_ready``.

    A single heap would not work: a delayed urgent transaction would sit on
    top and block everything that is ready.

    Cancellation is lazy. ``cancel()`` marks the transaction cancelled and
    forgets it; its heap entry stays and is skipped when it surfaces, which
    keeps cancel at O(1) instead of rebuilding the heap.

    A waiting transaction is expected to stay ``PENDING``. One whose status
    was changed elsewhere (cancelled or processed outside the queue) is
    dropped when it surfaces instead of being handed out.

    With an ``audit_log`` the queue records every ``add()`` - a retry coming
    back included, told apart by ``details.attempts`` - and every
    ``cancel()``, both as ``INFO``. The queue does not know the clients, so
    these events name the initiating account, not its owner.
    """

    def __init__(self, clock: Callable[[], datetime] = datetime.now, audit_log: AuditLog | None = None) -> None:
        if audit_log is not None and not isinstance(audit_log, AuditLog):
            raise InvalidOperationError("audit_log must be an AuditLog instance.")
        self._clock = clock
        self._audit_log = audit_log
        self._sequence = itertools.count()
        self._ready: list[tuple[int, int, str]] = []  # (-priority, sequence, transaction_id)
        self._delayed: list[tuple[datetime, int, str]] = []  # (scheduled_at, sequence, transaction_id)
        self._queued: dict[str, int] = {}  # transaction_id -> sequence of its live heap entry
        self._transactions: dict[str, Transaction] = {}  # everything ever added, for get()

    def __len__(self) -> int:
        """Number of transactions still waiting in the queue."""
        return len(self._queued)

    def add(self, transaction: Transaction) -> Transaction:
        """Queue a pending transaction; its ``priority`` and ``scheduled_at`` decide the order."""
        if not isinstance(transaction, Transaction):
            raise InvalidOperationError("transaction must be a Transaction instance.")
        if transaction.status is not TransactionStatus.PENDING:
            raise InvalidOperationError(
                f"Only pending transactions can be queued; {transaction.transaction_id} is {transaction.status.value}."
            )
        if transaction.transaction_id in self._queued:
            raise InvalidOperationError(f"Transaction {transaction.transaction_id} is already queued.")

        now = self._clock()
        due = transaction.is_due(now)
        when = "" if due else f" for {transaction.scheduled_at:%m-%d %H:%M}"
        # recorded before the transaction enters the queue: a failed write leaves nothing queued
        self._audit(
            TransactionEvent.QUEUED,
            transaction,
            now,
            f"queued{when}",
            priority=transaction.priority.name.lower(),
            scheduled_at=transaction.scheduled_at,
            attempts=transaction.attempts,
        )
        sequence = next(self._sequence)
        self._queued[transaction.transaction_id] = sequence
        self._transactions[transaction.transaction_id] = transaction
        if due:
            self._push_ready(transaction, sequence)
        else:
            heapq.heappush(self._delayed, (transaction.scheduled_at, sequence, transaction.transaction_id))
        return transaction

    def _push_ready(self, transaction: Transaction, sequence: int) -> None:
        heapq.heappush(self._ready, (-transaction.priority.value, sequence, transaction.transaction_id))

    def _is_live(self, transaction_id: str, sequence: int) -> bool:
        # an entry is stale when its transaction was cancelled or queued again under a new sequence
        return self._queued.get(transaction_id) == sequence

    def _promote_due(self, now: datetime) -> None:
        while self._delayed and self._delayed[0][0] <= now:
            _, sequence, transaction_id = heapq.heappop(self._delayed)
            if self._is_live(transaction_id, sequence):
                transaction = self._transactions[transaction_id]
                self._push_ready(transaction, sequence)
                _logger.debug(
                    "delayed transaction is due",
                    extra={
                        "fields": {
                            "event_time": now,
                            "transaction_id": transaction_id,
                            "scheduled_at": transaction.scheduled_at,
                        }
                    },
                )

    def next_ready(self) -> Transaction | None:
        """Remove and return the most urgent transaction that is due now, or ``None``."""
        self._promote_due(self._clock())
        while self._ready:
            _, sequence, transaction_id = heapq.heappop(self._ready)
            if not self._is_live(transaction_id, sequence):
                continue
            del self._queued[transaction_id]
            transaction = self._transactions[transaction_id]
            if transaction.status is TransactionStatus.PENDING:
                return transaction
        return None

    def cancel(self, transaction_id: str) -> Transaction:
        """Cancel a transaction that is still waiting; a started or finished one cannot be cancelled."""
        transaction = self.get(transaction_id)
        if transaction_id not in self._queued:
            # already handed out by next_ready(), even if the processor has not started it yet
            raise InvalidOperationError(
                f"Transaction {transaction_id} is no longer waiting in the queue ({transaction.status.value})."
            )
        # forget the entry first: whatever cancel() says, this transaction must not be handed out
        del self._queued[transaction_id]
        now = self._clock()
        transaction.cancel(now)
        self._audit(TransactionEvent.CANCELLED, transaction, now, "cancelled")
        return transaction

    def _audit(
        self, event: TransactionEvent, transaction: Transaction, moment: datetime, outcome: str, **details: object
    ) -> None:
        if self._audit_log is None:
            return
        self._audit_log.record(
            AuditLevel.INFO,
            AuditCategory.TRANSACTION,
            event.value,
            f"{transaction.transaction_type.value} of {transaction.amount} {transaction.currency.value} {outcome}",
            timestamp=moment,
            account_id=transaction.initiator_id,
            transaction_id=transaction.transaction_id,
            details={
                "type": transaction.transaction_type,
                "amount": transaction.amount,
                "currency": transaction.currency,
                "sender_id": transaction.sender_id,
                "recipient_id": transaction.recipient_id,
                **details,
            },
        )

    def get(self, transaction_id: str) -> Transaction:
        """Any transaction that has passed through the queue, whatever its status."""
        transaction = self._transactions.get(transaction_id)
        if transaction is None:
            raise TransactionNotFoundError(transaction_id)
        return transaction

    def pending(self) -> list[Transaction]:
        """A snapshot of the waiting transactions: due ones by priority, then delayed ones by time."""
        now = self._clock()

        def order(item: tuple[str, int]) -> tuple:
            transaction = self._transactions[item[0]]
            if transaction.is_due(now):
                return (0, -transaction.priority.value, item[1])
            return (1, transaction.scheduled_at, item[1])

        return [self._transactions[transaction_id] for transaction_id, _ in sorted(self._queued.items(), key=order)]
