"""Execution of transactions: rules, fees, currency conversion, retries and the error log."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from exceptions import BankError, InsufficientFundsError, InvalidOperationError, OperationTimeRestrictedError
from models.account import BankAccount
from models.enums import TransactionStatus, TransactionType
from models.transaction import Transaction
from services.bank import Bank
from services.fees import FeePolicy
from services.transaction_queue import TransactionQueue


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

    Money moves only through ``bank.withdraw()`` and ``bank.deposit()``, so the
    bank's own rules apply to every transaction: the night window, blocked
    clients, account limits and the suspicious activity log. On top of them
    the processor:

    - refuses a transaction when any account involved is frozen or closed;
    - refuses a debit that would take the balance below zero, unless the
      account type allows it (``ALLOWS_NEGATIVE_BALANCE``, premium accounts);
    - converts the amount into the currency of each account;
    - charges the fee from ``fee_policy`` together with the debit;
    - keeps a transfer atomic: if crediting the recipient fails after the
      sender was debited, the debit is returned (a compensating operation).

    These checks run before any money moves. The bank's own checks on the
    recipient (a blocked owner, the deposit limit) can still refuse the credit
    after the debit; the compensation then leaves both balances as they were.
    Errors in ``RETRYABLE_ERRORS`` are temporary - the night window
    ends, money may arrive - so the transaction goes back to the queue with an
    exponential delay (``retry_delay``, then twice as long, ...) until
    ``max_attempts`` is used up. Any other ``BankError`` fails it at once.
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
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
            raise InvalidOperationError("max_attempts must be a positive integer.")
        if not isinstance(retry_delay, timedelta) or retry_delay <= timedelta(0):
            raise InvalidOperationError("retry_delay must be a positive timedelta.")
        self._bank = bank
        self._fee_policy = fee_policy if fee_policy is not None else FeePolicy()
        self._max_attempts = max_attempts
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
                self.process(transaction)
                outcome[transaction.status].append(transaction)
        finally:
            for transaction in report.rescheduled:
                queue.add(transaction)
        return report

    def process(self, transaction: Transaction) -> Transaction:
        """Make one attempt; the transaction ends completed, failed or pending for a retry."""
        transaction.start(self._bank.now())
        try:
            fee, debited, credited = self._execute(transaction)
        except BankError as error:
            self._handle_failure(transaction, error)
        else:
            transaction.complete(self._bank.now(), fee=fee, debited_amount=debited, credited_amount=credited)
        return transaction

    def _execute(self, transaction: Transaction) -> tuple[Decimal, Decimal | None, Decimal | None]:
        """Move the money; return the fee, the amount debited and the amount credited."""
        action = transaction.transaction_type.value
        sender = (
            self._bank.ensure_operational(action, transaction.sender_id) if transaction.sender_id is not None else None
        )
        # the recipient of an external transfer is in another bank, nothing to credit here
        recipient = (
            self._bank.ensure_operational(action, transaction.recipient_id)
            if transaction.recipient_id is not None
            and transaction.transaction_type is not TransactionType.EXTERNAL_TRANSFER
            else None
        )
        converter = self._bank.converter

        fee = Decimal("0.00")
        debited = credited = None
        if sender is not None:
            debit = self._convert_for(transaction, sender)
            fee = self._fee_policy.calculate(transaction.transaction_type, debit, sender.currency, converter)
            self._ensure_no_negative_balance(sender, debit, fee)
            before = sender.balance
            self._bank.withdraw(sender.account_id, debit + fee)
            # the account may charge its own fee on top (premium), so measure what actually left it
            debited = before - sender.balance

        if recipient is not None:
            credit = self._convert_for(transaction, recipient)
            try:
                self._bank.deposit(recipient.account_id, credit)
            except BankError:
                if sender is not None:
                    self._bank.deposit(sender.account_id, debited)
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

    @staticmethod
    def _ensure_no_negative_balance(account: BankAccount, debit: Decimal, fee: Decimal) -> None:
        if account.ALLOWS_NEGATIVE_BALANCE or debit + fee <= account.balance:
            return
        hint = "This account type may not go below zero."
        if fee:
            hint = f"{hint} The fee {fee} is charged on top."
        raise InsufficientFundsError(requested=debit, available=max(account.balance - fee, Decimal("0.00")), hint=hint)

    def _handle_failure(self, transaction: Transaction, error: BankError) -> None:
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
