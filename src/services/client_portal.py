"""The client portal: what a logged-in client does with their own accounts, through a session."""

from decimal import Decimal
from typing import Any

from exceptions import AccountNotFoundError, InvalidOperationError
from models.client import Client
from models.enums import AssetType
from models.transaction import Transaction
from services.bank import Bank
from services.session import ClientSession
from services.transaction_history import BalanceMovement
from services.transaction_queue import TransactionQueue


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

    A transaction submitted here only enters the queue: the processor runs
    it later on the bank's behalf, when the session may be long gone.
    """

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
        client = self._client(session)
        account = self._bank.open_account(client.client_id, account_type, actor=session, **params)
        return account.get_account_info()

    def close_account(self, session: ClientSession, account_id: str) -> Decimal:
        """Close the client's account and return the cash paid out to them."""
        self._owner_of(session, account_id)
        return self._bank.close_account(account_id, actor=session)

    def freeze_account(self, session: ClientSession, account_id: str) -> dict[str, Any]:
        self._owner_of(session, account_id)
        return self._bank.freeze_account(account_id, actor=session).get_account_info()

    def unfreeze_account(self, session: ClientSession, account_id: str) -> dict[str, Any]:
        self._owner_of(session, account_id)
        return self._bank.unfreeze_account(account_id, actor=session).get_account_info()

    def deposit(self, session: ClientSession, account_id: str, amount: object) -> Decimal:
        self._owner_of(session, account_id)
        return self._bank.deposit(account_id, amount, actor=session)

    def withdraw(self, session: ClientSession, account_id: str, amount: object) -> Decimal:
        self._owner_of(session, account_id)
        return self._bank.withdraw(account_id, amount, actor=session)

    def invest(self, session: ClientSession, account_id: str, asset_type: AssetType | str, amount: object) -> Decimal:
        self._owner_of(session, account_id)
        return self._bank.invest(account_id, asset_type, amount, actor=session)

    def divest(self, session: ClientSession, account_id: str, asset_type: AssetType | str, amount: object) -> Decimal:
        self._owner_of(session, account_id)
        return self._bank.divest(account_id, asset_type, amount, actor=session)

    def submit(self, session: ClientSession, transaction: Transaction, queue: TransactionQueue) -> Transaction:
        """Queue a transaction on behalf of one of the client's accounts.

        The initiating account (the sender, or the recipient of a deposit)
        must be the client's; the recipient of a transfer may be anyone's.
        """
        client = self._client(session)
        if not isinstance(transaction, Transaction):
            raise InvalidOperationError("transaction must be a Transaction instance.")
        if not isinstance(queue, TransactionQueue):
            raise InvalidOperationError("queue must be a TransactionQueue instance.")
        if transaction.initiator_id not in client.account_ids:
            raise AccountNotFoundError(transaction.initiator_id)
        return queue.add(transaction, actor=session)

    def statement(self, session: ClientSession, account_id: str) -> list[BalanceMovement]:
        """Every movement of the client's account, in order."""
        self._owner_of(session, account_id)
        return self._bank.history.movements(account_id)

    def transactions(self, session: ClientSession) -> list[Transaction]:
        """The finished transactions sent from or to any of the client's accounts, in the order they finished."""
        client = self._client(session)
        return self._bank.history.transactions(account_ids=client.account_ids)
