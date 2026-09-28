"""The client portal: what a logged-in client does with their own accounts, through a session."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from exceptions import AccountNotFoundError, InvalidOperationError
from models.client import Client
from models.enums import AssetType, TransactionPriority, TransactionType
from models.transaction import Transaction
from services.bank import Bank
from services.session import ClientSession
from services.transaction_history import BalanceMovement
from services.transaction_queue import TransactionQueue
from utils import to_enum


class ClientPortal:
    """A thin layer over ``Bank`` that lets a client act only on their own accounts.

    Every method first resolves the session to its client (an unknown,
    closed or expired session, or one of a blocked client, is refused),
    then checks that the account belongs to that client, then delegates to
    the bank with the session as ``actor``, so the audit log says who acted.
    Someone else's account is reported as not found, the same as a
    non-existent one, so the portal does not confirm other clients' account
    numbers.

    The bank's rules stay the bank's: the night window, a frozen account and
    the amount review apply as they do to any caller. Accounts come back as
    snapshots (``get_account_info()``), not as the account objects: the
    money methods of an account model are public, and handing one out would
    let a client move money past the bank and the session.

    The portal never credits an account: money the bank does not hold yet
    comes in through the bank (``Bank.deposit()``, an account the bank opens
    with money), not through a client's session. So there is no ``deposit``
    here, an account opens empty, and a transaction submitted here must debit
    one of the client's accounts. The portal builds the transaction itself
    from what the client asks for, stamping ``created_at`` by the bank's
    clock and giving it its id, so the moments risk control scores are the
    bank's record of when the client acted, not the client's word. The
    transaction only enters the queue: the processor runs it later on the
    bank's behalf, when the session may be long gone.
    """

    # the portal passes these to the bank itself: the client and the session come from the session,
    # and a new account opens empty
    RESERVED_PARAMS = frozenset({"client_id", "actor", "initial_balance"})

    def __init__(self, bank: Bank) -> None:
        if not isinstance(bank, Bank):
            raise InvalidOperationError("bank must be a Bank instance.")
        self._bank = bank

    def _client(self, session: ClientSession) -> Client:
        return self._bank.resolve_session(session)

    def _owner_of(self, session: ClientSession, account_id: str) -> Client:
        client = self._client(session)
        if account_id not in client.account_ids:
            raise AccountNotFoundError(account_id)
        return client

    def accounts(self, session: ClientSession) -> list[dict[str, Any]]:
        """Snapshots of the client's accounts, closed ones included, in opening order."""
        client = self._client(session)
        return [account.get_account_info() for account in self._bank.search_accounts(client_id=client.client_id)]

    def open_account(self, session: ClientSession, account_type: str = "basic", **params: object) -> dict[str, Any]:
        """Open an empty account of ``account_type`` for the client; money comes to it by a deposit or a transfer."""
        client = self._client(session)
        reserved = sorted(self.RESERVED_PARAMS & params.keys())
        if reserved:
            raise InvalidOperationError(f"The portal sets {', '.join(reserved)} of a new account itself.")
        account = self._bank.open_account(client.client_id, account_type, actor=session, **params)
        return account.get_account_info()

    def close_account(self, session: ClientSession, account_id: str) -> Decimal:
        """Close the client's account and return the cash paid out to them."""
        self._owner_of(session, account_id)
        return self._bank.close_account(account_id, actor=session)

    def freeze_account(self, session: ClientSession, account_id: str) -> dict[str, Any]:
        self._owner_of(session, account_id)
        return self._bank.freeze_account(account_id, actor=session).get_account_info()

    def withdraw(self, session: ClientSession, account_id: str, amount: object) -> Decimal:
        self._owner_of(session, account_id)
        return self._bank.withdraw(account_id, amount, actor=session)

    def invest(self, session: ClientSession, account_id: str, asset_type: AssetType | str, amount: object) -> Decimal:
        self._owner_of(session, account_id)
        return self._bank.invest(account_id, asset_type, amount, actor=session)

    def divest(self, session: ClientSession, account_id: str, asset_type: AssetType | str, amount: object) -> Decimal:
        self._owner_of(session, account_id)
        return self._bank.divest(account_id, asset_type, amount, actor=session)

    def submit(
        self,
        session: ClientSession,
        queue: TransactionQueue,
        transaction_type: TransactionType | str,
        amount: object,
        currency: object,
        *,
        sender_id: str,
        recipient_id: str | None = None,
        priority: TransactionPriority | str = TransactionPriority.NORMAL,
        scheduled_at: datetime | None = None,
    ) -> Transaction:
        """Build a transaction that debits ``sender_id``, one of the client's accounts, and queue it.

        The recipient of a transfer may be anyone's account. A deposit has
        no sender and is not the client's to submit: it is the bank's
        credit. ``created_at`` is the bank's clock now; the id is new.
        """
        client = self._client(session)
        if not isinstance(queue, TransactionQueue):
            raise InvalidOperationError("queue must be a TransactionQueue instance.")
        kind = to_enum(TransactionType, transaction_type, field="transaction type")
        needs_sender, _ = Transaction.PARTIES[kind]
        if not needs_sender:
            raise InvalidOperationError(f"A {kind.value} credits an account; the bank makes it, not the client.")
        if sender_id not in client.account_ids:
            raise AccountNotFoundError(sender_id)
        transaction = Transaction(
            kind,
            amount,
            currency,
            sender_id=sender_id,
            recipient_id=recipient_id,
            priority=priority,
            scheduled_at=scheduled_at,
            created_at=self._bank.now(),
        )
        return queue.add(transaction, actor=session)

    def statement(self, session: ClientSession, account_id: str) -> list[BalanceMovement]:
        """Every movement of the client's account, in order."""
        self._owner_of(session, account_id)
        return self._bank.history.movements(account_id)

    def transactions(self, session: ClientSession) -> list[Transaction]:
        """The finished transactions sent from or to any of the client's accounts, in the order they finished."""
        client = self._client(session)
        return self._bank.history.transactions(account_ids=client.account_ids)
