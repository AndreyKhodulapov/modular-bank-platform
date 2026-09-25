"""Execution of transactions: rules, risk control, fees, currency conversion, retries and the error log."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from exceptions import (
    AccountNotFoundError,
    BankError,
    InsufficientFundsError,
    InvalidOperationError,
    OperationTimeRestrictedError,
)
from models.account import BankAccount
from models.enums import TransactionStatus
from models.transaction import Transaction
from services.audit_log import AuditCategory, AuditLevel, TransactionEvent
from services.bank import Bank
from services.fees import FeePolicy
from services.transaction_queue import TransactionQueue
from utils import to_positive_int

_logger = logging.getLogger("bank.transactions")


@dataclass(frozen=True)
class TransactionErrorRecord:
    """One failed attempt in the processor's error log; immutable once recorded."""

    timestamp: datetime
    transaction_id: str
    attempt: int
    error_type: str
    message: str
    will_retry: bool


@dataclass
class ProcessingReport:
    """What one ``process_queue()`` run did with the transactions it took from the queue."""

    completed: list[Transaction] = field(default_factory=list)
    failed: list[Transaction] = field(default_factory=list)
    rescheduled: list[Transaction] = field(default_factory=list)


class TransactionProcessor:
    """Executes transactions through the ``Bank`` facade.

    Money moves only through ``bank.withdraw()`` and ``bank.deposit()``, so
    every rule of the bank and of the account types applies to every
    transaction: the night window, blocked clients, frozen and closed
    accounts, operation limits, the suspicious activity log, and how far an
    account may be debited (a regular account never goes below zero, a
    premium account has its overdraft). On top of them the processor:

    - checks both accounts and converts the amount into their currencies
      before any money moves;
    - lets the bank screen the transaction (``bank.screen()``): a high risk
      fails it at once with ``RiskBlockedError``;
    - charges the fee from ``fee_policy`` together with the debit;
    - keeps a transfer atomic: if crediting the recipient fails after the
      sender was debited, the debit is put back with ``bank.refund()``,
      which no bank rule or limit can refuse;
    - passes the transaction id with every movement of money, so the
      bank's history links each balance change to its transaction, and
      puts the transaction itself into the history once it is final.

    Errors in ``RETRYABLE_ERRORS`` are temporary - the night window ends,
    money may arrive - so the transaction goes back to the queue with an
    exponential delay (``retry_delay``, then twice as long, ...) until
    ``max_attempts`` is used up. Any other ``BankError`` fails it at once.
    An error that is not a ``BankError`` is a defect, not a business
    outcome: the transaction is failed and logged all the same, and the
    error is raised to the caller.

    Every outcome goes to the bank's audit log: a completed transaction as
    ``INFO``, a failed attempt as ``ERROR`` (``details.will_retry`` tells
    whether it comes back) and an unexpected error as ``CRITICAL``; the
    details name both parties, ``sender_id`` and ``recipient_id``. If the
    audit log cannot be written, processing stops with that error rather
    than move more money without an audit trail; the transaction's status
    is already set, and a pending one still returns to the queue.

    The queue that feeds ``process_queue()`` should share the bank's clock
    and audit log (``TransactionQueue(clock=bank.now,
    audit_log=bank.audit_log)``): the queue decides when a delayed or
    retried transaction is due, the processor stamps the moments, and one
    journal holds the whole story of a transaction.
    """

    RETRYABLE_ERRORS: tuple[type[BankError], ...] = (OperationTimeRestrictedError, InsufficientFundsError)

    def __init__(
        self,
        bank: Bank,
        *,
        fee_policy: FeePolicy | None = None,
        max_attempts: int = 3,
        retry_delay: timedelta = timedelta(minutes=5),
    ) -> None:
        if not isinstance(bank, Bank):
            raise InvalidOperationError("bank must be a Bank instance.")
        if not isinstance(retry_delay, timedelta) or retry_delay <= timedelta(0):
            raise InvalidOperationError("retry_delay must be a positive timedelta.")
        self._bank = bank
        self._fee_policy = fee_policy if fee_policy is not None else FeePolicy()
        self._max_attempts = to_positive_int(max_attempts, field="max_attempts")
        self._retry_delay = retry_delay
        self._errors: list[TransactionErrorRecord] = []
        self._collected_fees = Decimal("0.00")

    @property
    def errors(self) -> list[TransactionErrorRecord]:
        """A copy of the error log, one record per failed attempt, in order."""
        return list(self._errors)

    @property
    def collected_fees(self) -> Decimal:
        """Fees earned on completed transactions, in the bank's base currency."""
        return self._collected_fees

    def process_queue(self, queue: TransactionQueue) -> ProcessingReport:
        """Run every transaction that is due now and put the retried ones back into ``queue``."""
        report = ProcessingReport()
        outcome = {
            TransactionStatus.COMPLETED: report.completed,
            TransactionStatus.FAILED: report.failed,
            TransactionStatus.PENDING: report.rescheduled,
        }
        # one run makes one attempt per transaction, so retries return to the queue after the loop;
        # `finally` gets them back even if an attempt raises halfway through the run
        try:
            while (transaction := queue.next_ready()) is not None:
                try:
                    self.process(transaction)
                finally:
                    # sorted even when process() raised (e.g. the audit write failed after the status
                    # was set): a pending one must go back to the queue, not be lost with the error
                    bucket = outcome.get(transaction.status)
                    if bucket is not None:
                        bucket.append(transaction)
        finally:
            queue.requeue(report.rescheduled)
        return report

    def process(self, transaction: Transaction) -> Transaction:
        """Make one attempt; the transaction ends completed, failed or pending for a retry."""
        transaction.start(self._bank.now())
        _logger.debug(
            "attempt started",
            extra={
                "fields": {
                    "event_time": transaction.updated_at,
                    "transaction_id": transaction.transaction_id,
                    "attempt": transaction.attempts,
                }
            },
        )
        try:
            fee, debited, credited = self._execute(transaction)
        except BankError as error:
            self._handle_failure(transaction, error)
        except Exception as error:
            self._handle_failure(transaction, error)
            raise
        else:
            transaction.complete(self._bank.now(), fee=fee, debited_amount=debited, credited_amount=credited)
            self._bank.risk_analyzer.record_completed(transaction)
            self._bank.history.record_transaction(transaction)
            self._audit_completed(transaction)
        return transaction

    def _initiator(self, transaction: Transaction) -> tuple[str | None, str | None]:
        """The client and account on whose behalf the transaction runs, if the bank knows them."""
        try:
            account = self._bank.get_account(transaction.initiator_id)
        except AccountNotFoundError:
            return None, transaction.initiator_id
        return account.owner.client_id, account.account_id

    def _audit_completed(self, transaction: Transaction) -> None:
        client_id, account_id = self._initiator(transaction)
        self._bank.audit_log.record(
            AuditLevel.INFO,
            AuditCategory.TRANSACTION,
            TransactionEvent.COMPLETED.value,
            f"{transaction.transaction_type.value} of {transaction.amount} {transaction.currency.value} completed",
            timestamp=transaction.finished_at,
            client_id=client_id,
            account_id=account_id,
            transaction_id=transaction.transaction_id,
            details={
                "type": transaction.transaction_type,
                "amount": transaction.amount,
                "currency": transaction.currency,
                "sender_id": transaction.sender_id,
                "recipient_id": transaction.recipient_id,
                "fee": transaction.fee,
                "attempt": transaction.attempts,
            },
        )

    def _execute(self, transaction: Transaction) -> tuple[Decimal, Decimal | None, Decimal | None]:
        """Move the money; return the fee, the amount debited and the amount credited."""
        sender = (
            self._bank.ensure_operational("withdraw", transaction.sender_id)
            if transaction.sender_id is not None
            else None
        )
        # the recipient of an external transfer is in another bank, nothing to credit here
        recipient_id = transaction.internal_recipient_id
        recipient = self._bank.ensure_operational("deposit", recipient_id) if recipient_id is not None else None
        converter = self._bank.converter
        # both conversions come before the debit: a credit that rounds away to nothing must not strand it
        debit = self._convert_for(transaction, sender) if sender is not None else None
        credit = self._convert_for(transaction, recipient) if recipient is not None else None
        # the last check before money moves: the night window, a blocked client, then the risk score
        self._bank.screen(transaction)

        fee = Decimal("0.00")
        debited = credited = None
        if sender is not None:
            fee = self._fee_policy.calculate(transaction.transaction_type, debit, sender.currency, converter)
            before = sender.balance
            self._bank.withdraw(sender.account_id, debit + fee, transaction_id=transaction.transaction_id)
            # the account may charge its own fee on top (premium), so measure what actually left it
            debited = before - sender.balance

        if recipient is not None:
            try:
                self._bank.deposit(recipient.account_id, credit, transaction_id=transaction.transaction_id)
            except Exception:
                if sender is not None:
                    self._bank.refund(sender.account_id, debited, transaction_id=transaction.transaction_id)
                raise
            credited = credit

        if sender is not None:
            self._collected_fees += converter.to_base(fee, sender.currency)
        return fee, debited, credited

    def _convert_for(self, transaction: Transaction, account: BankAccount) -> Decimal:
        """The transaction amount in the account's currency; refuse one that rounds away to nothing."""
        value = self._bank.converter.convert(transaction.amount, transaction.currency, account.currency)
        if value <= 0:
            raise InvalidOperationError(
                f"{transaction.amount} {transaction.currency.value} is {value} in {account.currency.value}."
            )
        return value

    def _handle_failure(self, transaction: Transaction, error: Exception) -> None:
        now = self._bank.now()
        will_retry = isinstance(error, self.RETRYABLE_ERRORS) and transaction.attempts < self._max_attempts
        self._errors.append(
            TransactionErrorRecord(
                timestamp=now,
                transaction_id=transaction.transaction_id,
                attempt=transaction.attempts,
                error_type=type(error).__name__,
                message=str(error),
                will_retry=will_retry,
            )
        )
        reason = f"{type(error).__name__}: {error}"
        if will_retry:
            delay = self._retry_delay * 2 ** (transaction.attempts - 1)
            transaction.retry(reason, now, now + delay)
        else:
            transaction.fail(reason, now)
            # before the audit write: a failing write must not keep a finished transaction out of the history
            self._bank.history.record_transaction(transaction)
        # logged after the status change: a failing audit write must not leave the transaction in PROCESSING
        client_id, account_id = self._initiator(transaction)
        self._bank.audit_log.record(
            AuditLevel.ERROR if isinstance(error, BankError) else AuditLevel.CRITICAL,
            AuditCategory.TRANSACTION,
            TransactionEvent.FAILED.value,
            f"attempt {transaction.attempts}: {type(error).__name__}: {error}",
            timestamp=now,
            client_id=client_id,
            account_id=account_id,
            transaction_id=transaction.transaction_id,
            details={
                "sender_id": transaction.sender_id,
                "recipient_id": transaction.recipient_id,
                "error_type": type(error).__name__,
                "attempt": transaction.attempts,
                "will_retry": will_retry,
            },
        )
