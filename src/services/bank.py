"""The bank: a single entry point to clients, accounts, their security and risk control."""

import inspect
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from exceptions import (
    AccountClosedError,
    AccountFrozenError,
    AccountNotFoundError,
    ClientBlockedError,
    ClientNotFoundError,
    InvalidOperationError,
    RiskBlockedError,
)
from models.account import BankAccount
from models.client import Client
from models.enums import AccountStatus, Currency
from models.investment_account import InvestmentAccount
from models.premium_account import PremiumAccount
from models.savings_account import SavingsAccount
from models.transaction import Transaction
from services.audit_log import AuditCategory, AuditLevel, AuditLog, RiskEvent
from services.currency import CurrencyConverter
from services.risk import RiskAnalyzer, RiskAssessment, RiskContext, RiskLevel
from services.security import SecurityGuard, SuspicionReason, SuspiciousActivity
from utils import to_enum, to_money


class Bank:
    """Facade over the clients, the accounts, security and currency conversion.

    The bank keeps two registries - clients by ``client_id`` and accounts by
    ``account_id`` - and routes every operation through the same checks:

    - operations that move money or give access back (open, close, unfreeze,
      deposit, withdraw, unblock) are forbidden in the night window;
    - a blocked client cannot operate on their accounts;
    - failed logins, night attempts, operations on frozen or closed accounts
      and large amounts are recorded as suspicious;
    - ``screen()`` scores a transaction with the risk analyzer before money
      moves and refuses a high-risk one.

    Protective actions (``freeze_account``), logins and read-only queries are
    allowed at any time. Totals and the ranking are expressed in the
    converter's base currency (RUB by default).
    """

    # account type registry: a new account type is offered by adding one entry
    ACCOUNT_TYPES: dict[str, type[BankAccount]] = {
        "basic": BankAccount,
        "savings": SavingsAccount,
        "premium": PremiumAccount,
        "investment": InvestmentAccount,
    }
    RESERVED_ACCOUNT_PARAMS = frozenset({"owner", "account_id", "status"})

    def __init__(
        self,
        *,
        security: SecurityGuard | None = None,
        converter: CurrencyConverter | None = None,
        risk_analyzer: RiskAnalyzer | None = None,
    ) -> None:
        if risk_analyzer is not None and not isinstance(risk_analyzer, RiskAnalyzer):
            raise InvalidOperationError("risk_analyzer must be a RiskAnalyzer instance.")
        self._security = security if security is not None else SecurityGuard()
        self._converter = converter if converter is not None else CurrencyConverter()
        self._risk = risk_analyzer if risk_analyzer is not None else RiskAnalyzer()
        self._clients: dict[str, Client] = {}
        self._accounts: dict[str, BankAccount] = {}
        # kept by the bank, not the account: the models have no clock
        self._opened_at: dict[str, datetime] = {}

    @property
    def base_currency(self) -> Currency:
        return self._converter.base

    @property
    def converter(self) -> CurrencyConverter:
        return self._converter

    @property
    def suspicious_activities(self) -> list[SuspiciousActivity]:
        return self._security.suspicious_activities

    @property
    def audit_log(self) -> AuditLog:
        """The journal shared by security, transactions and risk control."""
        return self._security.audit_log

    @property
    def risk_analyzer(self) -> RiskAnalyzer:
        return self._risk

    def now(self) -> datetime:
        """The bank's current time, from the security guard's clock."""
        return self._security.now()

    def get_client(self, client_id: str) -> Client:
        client = self._clients.get(client_id)
        if client is None:
            raise ClientNotFoundError(client_id)
        return client

    def get_account(self, account_id: str) -> BankAccount:
        account = self._accounts.get(account_id)
        if account is None:
            raise AccountNotFoundError(account_id)
        return account

    def account_opened_at(self, account_id: str) -> datetime:
        """When the bank opened the account, by its own clock."""
        self.get_account(account_id)
        return self._opened_at[account_id]

    @classmethod
    def _resolve_account_class(cls, account_type: str) -> type[BankAccount]:
        account_class = cls.ACCOUNT_TYPES.get(str(account_type).lower())
        if account_class is None:
            allowed = ", ".join(cls.ACCOUNT_TYPES)
            raise InvalidOperationError(f"Unsupported account type {account_type!r}; allowed: {allowed}.")
        return account_class

    def _guard(self, action: str, client: Client, account: BankAccount | None = None) -> None:
        """Run the checks shared by every restricted operation: night window, then client status."""
        account_id = account.account_id if account is not None else None
        self._security.ensure_daytime(action, client_id=client.client_id, account_id=account_id)
        if client.is_blocked:
            self._security.flag(
                SuspicionReason.BLOCKED_CLIENT_ACTIVITY,
                f"{action} attempted by a blocked client",
                client_id=client.client_id,
                account_id=account_id,
            )
            raise ClientBlockedError(client.client_id)

    def _review_amount(self, action: str, account: BankAccount, amount: Decimal) -> None:
        self._security.review_amount(
            self._converter.to_base(amount, account.currency),
            action,
            client_id=account.owner.client_id,
            account_id=account.account_id,
        )

    def add_client(self, client: Client, password: str) -> Client:
        """Register ``client`` with a login password; the password is stored as a hash only."""
        if not isinstance(client, Client):
            raise InvalidOperationError("client must be a Client instance.")
        if client.client_id in self._clients:
            raise InvalidOperationError(f"Client {client.client_id} is already registered.")
        self._security.register_password(client.client_id, password)
        self._clients[client.client_id] = client
        return client

    def authenticate_client(self, client_id: str, password: str) -> Client:
        """Return the client when ``password`` is right; the third failure in a row blocks them."""
        if not isinstance(password, str):
            raise InvalidOperationError("password must be a string.")
        client = self._clients.get(client_id)
        if client is None:
            self._security.flag(
                SuspicionReason.UNKNOWN_CLIENT_LOGIN, "login attempt for an unknown client id", client_id=client_id
            )
            raise ClientNotFoundError(client_id)
        self._security.authenticate(client, password)
        return client

    def unblock_client(self, client_id: str) -> Client:
        client = self.get_client(client_id)
        # only a real unblock is a night-restricted action; an active client gets the model's error
        if client.is_blocked:
            self._security.ensure_daytime("unblock_client", client_id=client_id)
        client.unblock()
        return client

    def open_account(self, client_id: str, account_type: str = "basic", **params: object) -> BankAccount:
        """Open an account of a registered type for the client.

        ``params`` are passed to the account constructor (``currency``,
        ``initial_balance``, ``min_balance`` and so on). The bank decides the
        rest: the owner is the client, the number is generated and a new
        account is always active.
        """
        client = self.get_client(client_id)
        account_class = self._resolve_account_class(account_type)
        reserved = sorted(self.RESERVED_ACCOUNT_PARAMS & params.keys())
        if reserved:
            raise InvalidOperationError(f"The bank sets {', '.join(reserved)} of a new account itself.")
        try:
            inspect.signature(account_class).bind(owner=client, **params)
        except TypeError as error:
            # an unknown or missing constructor argument, e.g. min_balance for a basic account
            raise InvalidOperationError(f"Invalid parameters for {account_class.__name__}: {error}.") from error
        self._guard("open_account", client)
        account = account_class(owner=client, **params)
        self._accounts[account.account_id] = account
        self._opened_at[account.account_id] = self.now()
        client.add_account_id(account.account_id)
        self._review_amount("open_account", account, account.total_value)
        return account

    def _run_on_account[T](self, action: str, account: BankAccount, operation: Callable[[], T]) -> T:
        """Run ``operation`` and record it as suspicious when the account turns out frozen or closed."""
        try:
            return operation()
        except (AccountFrozenError, AccountClosedError):
            self._security.flag(
                SuspicionReason.INACTIVE_ACCOUNT_OPERATION,
                f"{action} on a {account.status.value} account",
                client_id=account.owner.client_id,
                account_id=account.account_id,
            )
            raise

    def close_account(self, account_id: str) -> Decimal:
        """Close an account and return the cash paid out to the client."""
        account = self.get_account(account_id)
        self._guard("close_account", account.owner, account)
        payout = self._run_on_account("close_account", account, account.close)
        self._review_amount("close_account", account, payout)
        return payout

    def ensure_operational(self, action: str, account_id: str) -> BankAccount:
        """Return the account if it is active; otherwise record the attempt and raise.

        Lets a caller check an account before a multi-step operation, e.g. both
        sides of a transfer before any money moves.
        """
        account = self.get_account(account_id)
        self._run_on_account(action, account, account.ensure_operational)
        return account

    def screen(self, transaction: Transaction) -> RiskAssessment:
        """Check a transaction before its money moves; refuse it when the risk is high.

        The hard rules come first - the night window and a blocked client -
        so a transaction they refuse is not scored. Then the risk analyzer
        scores it and the result goes to the audit log: ``INFO`` for a low
        risk, ``WARNING`` for a medium one (the transaction goes on) and
        ``CRITICAL`` for a high one, which raises ``RiskBlockedError``.
        """
        if not isinstance(transaction, Transaction):
            raise InvalidOperationError("transaction must be a Transaction instance.")
        initiator = self.get_account(transaction.initiator_id)
        recipient_id = transaction.internal_recipient_id
        recipient = self.get_account(recipient_id) if recipient_id is not None else None
        action = transaction.transaction_type.value
        self._guard(action, initiator.owner, initiator)

        assessment = self._risk.assess(
            RiskContext(
                transaction=transaction,
                client_id=initiator.owner.client_id,
                moment=self.now(),
                amount_in_base=self._converter.to_base(transaction.amount, transaction.currency),
                recipient_opened_at=self._opened_at[recipient.account_id] if recipient is not None else None,
            )
        )
        levels = {
            RiskLevel.LOW: AuditLevel.INFO,
            RiskLevel.MEDIUM: AuditLevel.WARNING,
            RiskLevel.HIGH: AuditLevel.CRITICAL,
        }
        rules = ", ".join(assessment.rules) or "no risk factors"
        self.audit_log.record(
            levels[assessment.level],
            AuditCategory.RISK,
            (RiskEvent.BLOCKED if assessment.blocked else RiskEvent.ASSESSED).value,
            f"{action} of {transaction.amount} {transaction.currency.value}: "
            f"{assessment.level.name.lower()} risk, score {assessment.score} ({rules})",
            timestamp=assessment.moment,
            client_id=assessment.client_id,
            account_id=initiator.account_id,
            transaction_id=transaction.transaction_id,
            details={
                "score": assessment.score,
                "risk_level": assessment.level.name.lower(),
                "factors": assessment.rules,
                "amount_in_base": assessment.amount_in_base,
            },
        )
        if assessment.blocked:
            raise RiskBlockedError(transaction.transaction_id, assessment.score, assessment.rules)
        return assessment

    def freeze_account(self, account_id: str) -> BankAccount:
        """Freeze an active account; allowed at any time, since freezing only protects money."""
        account = self.get_account(account_id)
        self._run_on_account("freeze_account", account, account.freeze)
        return account

    def unfreeze_account(self, account_id: str) -> BankAccount:
        account = self.get_account(account_id)
        self._guard("unfreeze_account", account.owner, account)
        self._run_on_account("unfreeze_account", account, account.unfreeze)
        return account

    def deposit(self, account_id: str, amount: object) -> Decimal:
        account = self.get_account(account_id)
        return self._move_money("deposit", account, account.deposit, amount)

    def withdraw(self, account_id: str, amount: object) -> Decimal:
        account = self.get_account(account_id)
        return self._move_money("withdraw", account, account.withdraw, amount)

    def _move_money(
        self, action: str, account: BankAccount, operation: Callable[[Decimal], Decimal], amount: object
    ) -> Decimal:
        value = to_money(amount, require="positive")
        self._guard(action, account.owner, account)
        before = account.balance
        balance = self._run_on_account(action, account, lambda: operation(value))
        self._review_amount(action, account, abs(balance - before))
        return balance

    def search_accounts(
        self,
        *,
        client_id: str | None = None,
        status: AccountStatus | str | None = None,
        currency: Currency | str | None = None,
        account_type: str | None = None,
        min_balance: object = None,
        max_balance: object = None,
    ) -> list[BankAccount]:
        """Return the accounts matching every given filter, in opening order.

        ``account_type`` is a registry key and matches the exact type, so
        ``"basic"`` does not return savings accounts. Balance bounds are
        inclusive and compare ``balance`` in the account's own currency.
        """
        status_filter = to_enum(AccountStatus, status, field="account status") if status is not None else None
        currency_filter = to_enum(Currency, currency, field="currency") if currency is not None else None
        type_filter = self._resolve_account_class(account_type) if account_type is not None else None
        low = to_money(min_balance, field="min_balance") if min_balance is not None else None
        high = to_money(max_balance, field="max_balance") if max_balance is not None else None

        return [
            account
            for account in self._accounts.values()
            if (client_id is None or account.owner.client_id == client_id)
            and (status_filter is None or account.status is status_filter)
            and (currency_filter is None or account.currency is currency_filter)
            and (type_filter is None or type(account) is type_filter)
            and (low is None or account.balance >= low)
            and (high is None or account.balance <= high)
        ]

    def _value_in_base(self, account: BankAccount) -> Decimal:
        return self._converter.to_base(account.total_value, account.currency)

    def get_total_balance(self) -> Decimal:
        """Everything the bank holds for its clients, in the base currency.

        Portfolios count at their invested amount; an overdraft counts as a
        negative value.
        """
        return sum((self._value_in_base(account) for account in self._accounts.values()), Decimal("0.00"))

    def get_clients_ranking(self) -> list[tuple[Client, Decimal]]:
        """Clients ordered by their total value in the base currency, richest first.

        Clients without accounts are included with zero; ties are ordered by
        full name so that the ranking is deterministic.
        """
        totals = {client_id: Decimal("0.00") for client_id in self._clients}
        for account in self._accounts.values():
            totals[account.owner.client_id] += self._value_in_base(account)
        ranking = [(self._clients[client_id], total) for client_id, total in totals.items()]
        ranking.sort(key=lambda item: (-item[1], item[0].full_name, item[0].client_id))
        return ranking
