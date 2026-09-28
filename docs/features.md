# Features

What the platform does, module by module: the account types, the bank and
its security rules, client sessions, transactions, the history, audit and
risk control, and the reports. How to install and run it is in the
[README](../README.md); the design behind it is in
[OOP principles](oop_principles.md).

- [Accounts Basic](#accounts-basic)
- [Accounts Advanced](#accounts-advanced)
- [Bank System](#bank-system)
- [Client sessions](#client-sessions)
- [Transactions](#transactions)
- [Transaction history](#transaction-history)
- [Audit and Risk](#audit-and-risk)
- [Bank reports](#bank-reports)
- [Report builder](#report-builder)

## Accounts Basic

- `AbstractAccount` - abstract base with a unique id, owner (a `Client`), protected balance,
  status and the abstract operations `deposit`, `withdraw`, `get_account_info`.
- `BankAccount` - concrete account with input validation, status enforcement,
  automatic UUID4 generation, a `currency` attribute (RUB, USD, EUR, KZT, CNY)
  and per-operation limits `MAX_DEPOSIT` / `MAX_WITHDRAWAL`.
- `Client` - the account holder: validated personal data (full name, birth
  date, email, phone) behind read-only properties, a UUID4 `client_id`, an
  `active` / `blocked` status, the list of account numbers and an age check
  (at least 18 years old). Clients are equal when their ids are equal.
- Domain exceptions: `AccountFrozenError`, `AccountClosedError`,
  `InvalidOperationError`, `InsufficientFundsError`, `LimitExceededError`
  (all derive from `BankError`).
- Money is handled as `decimal.Decimal` rounded half-up to two decimal places;
  rates are `Decimal` fractions (`0.10` means 10%) within `[-1, 1]`.

## Accounts Advanced

Three subclasses of `BankAccount`; each overrides `withdraw()`,
`get_account_info()` and `__str__()`:

- `SavingsAccount` - keeps `min_balance` locked on the account and earns
  interest at a `monthly_rate`, credited by `Bank.apply_monthly_interest()`.
- `PremiumAccount` - limits ten times higher, an `overdraft_limit` that lets the
  balance go negative and a fixed `withdrawal_fee` charged on every withdrawal.
- `InvestmentAccount` - free cash plus a `Portfolio` of virtual asset types
  (`stocks`, `bonds`, `etf`). `Bank.invest()` / `Bank.divest()` move money
  between cash and the portfolio, `withdraw()` never touches invested money, and
  `project_yearly_growth(growth_rates)` estimates one year of growth.

Every account can be frozen, unfrozen and closed (`freeze()`, `unfreeze()`,
`close()`). Closing is a settlement: the cash is paid out and returned, even
below a savings `min_balance`. It is refused while the account is in
overdraft, holds money in a portfolio or is frozen with money on it. An
account is created active or frozen, never closed: only `close()` closes it,
so money never ends up on an account that refuses every operation.

## Bank System

- `Bank` - the entry point to the platform. It registers clients with a
  password, checking their age by the bank's clock, opens accounts of a
  registered type (`basic`, `savings`, `premium`, `investment`), closes,
  freezes and unfreezes them, runs deposits and withdrawals and the
  operations of the account types (`apply_monthly_interest()`, `invest()`,
  `divest()`), searches accounts by client, status, currency, type and
  balance range, and reports `get_total_balance()` and
  `get_clients_ranking()` in roubles. Every balance change it makes goes to
  the [transaction history](#transaction-history).
- `SecurityGuard` - stores salted password hashes, opens a 30-minute
  `ClientSession` on a successful login, blocks a client after three failed
  logins in a row (closing their sessions), forbids operations between 00:00
  and 05:00 and keeps a log of suspicious activities.
- `CurrencyConverter` - converts amounts into roubles using fixed reference
  rates (replaceable by passing another rate table).
- Domain exceptions: `ClientNotFoundError`, `AccountNotFoundError`,
  `AuthenticationError` (carries `attempts_left`), `ClientBlockedError`,
  `InvalidSessionError` and its `SessionExpiredError`,
  `OperationTimeRestrictedError` (all derive from `BankError`).

Security rules applied by the bank:

| Rule | Behaviour |
| --- | --- |
| Login lockout | 3 wrong passwords in a row block the client and end their sessions; `unblock_client()` restores access, the client logs in again |
| Blocked client | cannot open or close accounts, move money out or send transactions; deposits and transfers to them still arrive, since anyone can trigger the lockout |
| Night window 00:00-05:00 | open, close, unfreeze, deposit, withdraw, invest, divest and unblock are refused; login, freeze, monthly interest and queries are allowed |
| Suspicious activity log | failed logins, blocking, attempts by a blocked client or for an unknown id, night attempts, operations on frozen or closed accounts, amounts of 500 000 RUB and more, use of an expired session; kept in the audit log as `security` events. A refused credit (a night one, or one to a frozen or closed account) is recorded on the account, not on its owner, who did not act, unless the owner made it through their session |

`Bank.invest()` and `Bank.divest()` are client operations with the same
checks as a deposit or a withdrawal. `Bank.apply_monthly_interest()` is the
bank's own operation: neither the night window nor a blocked client stops it,
while a frozen or closed account earns no interest. The bank keeps the
calendar: interest is paid once a month, on the day of the month the account
was opened (the last day of a shorter month), and an earlier call is refused
with the date of the next one. It is the only money the bank creates itself,
so it goes to the audit log as `interest_credited` and a large one is
recorded as suspicious. The account types keep the rules (the rate, the
portfolio), the bank owns the operations: `_apply_monthly_interest()`,
`_invest()`, `_divest()` and `_refund()` of the models are internal, called
by the bank only.

## Client sessions

A login opens a session, and a client acts on their own accounts only
through it; the bank's own API stays open to the back office.

- `Bank.authenticate_client(client_id, password)` returns a
  `ClientSession` (`session_id`, `client_id`, `issued_at`, `expires_at` and
  a secret `token`). It lives 30 minutes by the bank's clock, counted from
  the login and never extended. Each login opens a new session and keeps
  the others, one per device. `SecurityGuard` keeps only a hash of the
  token and recognises a session exactly as it was issued.
- A session ends with `Bank.logout(session)`, when it expires (its use is
  then recorded as `expired_session`), or when the client is blocked;
  unblocking does not bring it back, the client logs in again. Logging out
  after the session expired is not suspicious: a session not used since it
  expired is closed all the same and `client_logged_out` says `expired`
  (one used after it expired was already closed by that use, so the logout
  is refused as `InvalidSessionError`).
- `ClientPortal(bank)` - what a logged-in client does: `accounts()`,
  `open_account()`, `close_account()`, `freeze_account()`, `withdraw()`,
  `invest()`, `divest()`, `submit()` (builds a transaction and queues it),
  `statement()` and `transactions()`. Each method resolves the session to
  its client first (`InvalidSessionError`, `SessionExpiredError`), then
  checks that the account is the client's: someone else's account is
  reported as `AccountNotFoundError`, the same as a missing one, so the
  portal does not confirm other clients' account numbers. A transaction is
  accepted when its sender is the client's account; the recipient of a
  transfer may be anyone's.
- The portal never credits an account. It has no `deposit()`, an account
  opened through it is empty (`initial_balance` is refused) and a `deposit`
  transaction is not accepted: money the bank does not hold yet comes in
  through the bank itself (`Bank.deposit()`, an account the bank opens with
  money), and a client moves it between accounts by transfers. The terms
  of an account are refused as well (`overdraft_limit`, `withdrawal_fee`,
  `monthly_rate`): an overdraft and interest are money the bank promises.
  A premium account with an overdraft, a savings account with interest or
  with a `min_balance` is therefore opened by the bank.
- The portal builds a transaction itself: `submit(session, queue, type,
  amount, currency, sender_id=..., recipient_id=..., priority=...,
  scheduled_at=...)` stamps `created_at` by the bank's clock and gives it a
  new id, so risk control scores the moment the bank saw the request, not
  a moment the client wrote in; a `scheduled_at` earlier than that moment
  is refused by the transaction itself.
- The portal hands out snapshots (`get_account_info()`), amounts and history
  records, never the account objects, whose money methods would move money
  past the bank and the session.
- The bank's rules do not change: the night window, a frozen account and
  the amount review apply to a client with a session as to anyone. The bank
  checks only that a session given as `actor=` belongs to the account's
  owner, and names it in the audit log (see [Audit and Risk](#audit-and-risk))
  and in the balance movements of the history (`session_id`).
- What stays with the back office: `Bank` does not require a session
  (`actor=` is optional, for the calls made on a client's behalf). The
  processor runs a submitted transaction later, on the bank's behalf, when
  the session may be over; monthly interest, refunds and the reports are
  the bank's own work. Lifting a freeze is the bank's decision too: a
  client may freeze their own account, but `unfreeze_account()` takes no
  session, since the account does not remember who froze it and a client
  could otherwise lift a freeze the bank imposed. The night window applies
  to it, as to unblocking; a night attempt is recorded on the account, not
  on the client, a blocked owner does not stop it, and a refusal by the
  account (closed, not frozen) is not recorded as suspicious.

```python
session = bank.authenticate_client(client.client_id, "secret-2026")
portal = ClientPortal(bank)
portal.withdraw(session, account_id, 1_000)  # the client's own account
portal.withdraw(session, maria_account_id, 1_000)  # AccountNotFoundError
bank.logout(session)
portal.accounts(session)  # InvalidSessionError
```

## Transactions

- `Transaction` - a request to move money: id, type (`deposit`,
  `withdrawal`, `transfer`, `external_transfer`), amount, currency, fee,
  sender and recipient, priority, status, failure reason, attempts, the
  amounts actually debited and credited, and the timestamps `created_at`,
  `requested_at` (`scheduled_at` as the client gave it, else `created_at`;
  never earlier than `created_at`, and a retry never moves it),
  `scheduled_at`, `updated_at`, `finished_at`. The status follows a strict
  state machine:

  ```
  PENDING -> PROCESSING -> COMPLETED | FAILED
  PROCESSING -> PENDING      (retry scheduled)
  PENDING -> CANCELLED
  ```

- `TransactionQueue` - adds transactions, hands them out by priority
  (`urgent`, `high`, `normal`, `low`; first in, first out within one
  priority), holds delayed ones until their `scheduled_at` and cancels the
  ones still waiting. Two heaps keep a delayed urgent transaction from
  blocking the ready ones. Given an `audit_log`, it records every
  transaction it takes in (a retry coming back included) and every
  cancellation. `add()` records first, so a transaction whose event cannot
  be written is not queued; retries come back through `requeue()`, which
  queues them first, so a failing audit write cannot drop one.
- `TransactionProcessor` - executes transactions through `Bank`, so the
  night window, blocked clients, limits and the suspicious activity log
  apply. It converts amounts into each account's currency, charges fees,
  retries temporary failures and keeps an error log (`errors`) of every
  failed attempt. `process_queue()` runs everything that is due and returns
  a `ProcessingReport` (completed, failed, rescheduled). The queue should
  share the bank's clock and audit log
  (`TransactionQueue(clock=bank.now, audit_log=bank.audit_log)`): delays
  and retries are then measured by the same time, and the queue's events
  go to the bank's journal. Without them the queue uses the wall clock and
  records nothing. The programs pass both; the tests pass the clock and add
  the journal where they check it.
- `FeePolicy` - the tariff: external transfers pay 1% of the amount, at
  least 50 and at most 5 000 RUB (converted into the sender's currency);
  everything else is free. Pass another policy to change the tariff.
- Domain exceptions: `TransactionNotFoundError`,
  `InvalidTransactionStateError`.

Processing rules:

| Rule | Behaviour |
| --- | --- |
| Frozen or closed account | the transaction fails at once; no money moves |
| Risk control | after both accounts are checked and before money moves the bank screens the transaction; a high risk fails it at once with `RiskBlockedError`, no money moves |
| Negative balance | decided by the account type itself through `withdraw()`: a regular account never goes below zero, a premium account may use its overdraft |
| External transfer fee | charged with the debit, in the sender's currency; the premium account's own withdrawal fee comes on top |
| Currency conversion | the amount is converted into the sender's and the recipient's currency through the base currency |
| Atomic transfer | both accounts are checked and both amounts converted before any money moves; if anything fails after the debit and the recipient was not credited (the deposit limit, an audit write that failed), the debit is put back with `bank.refund()`, which no client rule or limit can refuse. A credit that went through is never taken back, even if the bank raised after it: the history, written before the audit log, tells whether the recipient got the money. `refund()` puts back only a debit recorded on that account under a transaction still in progress, each debit once and no more than was debited, so it cannot make money for a completed or made-up transaction, while a retry that debits again can still be rolled back. The debit itself was a real bank operation, so a large one stays in the suspicious activity log even after it is put back; the history keeps both the debit and its refund |
| Retries | up to 3 attempts: a transaction refused in the night window comes back when the window ends (05:00), one short of money after an exponential delay (5, 10 minutes by default); other bank errors fail at once; an unexpected error fails the transaction, is logged and re-raised |

## Transaction history

`TransactionHistory` is the bank's record of what happened to the money,
kept in memory next to the accounts (`bank.history`, or injected with
`Bank(history=...)`). It holds two things:

- **Transactions** - each one once, when it reaches its final status after
  the last attempt: `completed` or `failed`. A cancelled transaction never
  ran, so it stays in the queue and the audit log only. A transaction
  refused because another one holds its id enters as `failed` too
  (`record_duplicate()`), without taking the id: the history and the audit
  log count the same failures, and a client sees the refused submission.
  `transactions(account_ids=..., status=..., transaction_type=..., since=...,
  until=...)` returns them in the order they finished; `account_ids` matches
  both outgoing and incoming ones, and the time range applies to
  `finished_at`.
- **Balance movements** - one `BalanceMovement` per change of a balance made
  through the bank: the moment, the account, the kind (`opening`, `deposit`,
  `withdrawal`, `refund`, `payout`, `interest`, `investment`, `divestment`),
  the signed change in the account's currency, the balance and the total
  value (`total_value_after`: cash plus portfolio) right after it, the
  transaction id (`None` for an operation outside a transaction) and the
  client session it came through (`session_id`; `None` for the bank's own
  operations and the transactions the processor runs).
  `movements(account_id, kind=..., transaction_id=..., since=..., until=...)`
  returns them in order.

`Bank` is the only writer of movements: an account opened with money,
`deposit()`, `withdraw()` (both take an optional `transaction_id`), the
payout on `close_account()`, `refund()`, which puts back the debit of a
rolled-back transfer, `apply_monthly_interest()`, `invest()` and `divest()`.
Moving money into a portfolio lowers the balance but keeps the total value,
so the balance charts of the reports, drawn from `total_value_after`, show
no loss, and the statement of a client report prints both columns. The
change is measured as the balance after minus the balance before, so a
premium account's own withdrawal fee is part of the withdrawal. A refused
operation leaves no movement. As a result the movements of every account add
up to its balance, unless `deposit()`, `withdraw()` or `close()` of the
account itself are called past the bank: they are the account's own
interface (the first two are the abstract methods of `AbstractAccount`) and
stay public.

```python
for movement in bank.history.movements(account.account_id):
    print(movement)  # 09-24 14:00 3c50a706 withdrawal     -3010.00     -2010.00 RUB
```

## Audit and Risk

- `AuditLog` - one append-only journal for the whole bank. Every
  `AuditEvent` is immutable and structured: timestamp, level (`INFO`,
  `WARNING`, `ERROR`, `CRITICAL`), category (`security`, `transaction`,
  `risk`, `account`, `client`), event name, message, client, account and transaction ids and a
  `details` mapping. Events are kept in memory and, when a `file_path` is
  given, appended to a JSON Lines file as soon as they are recorded;
  `AuditLog.load_events()` reads a file back. `filter()` combines a minimum or
  exact level, category, event, client, account, transaction and a time
  range. Every event is also passed to the application log (see
  [Logs](../README.md#logs)).
- Who writes to it:

  | Writer | Category | Events | Level |
  | --- | --- | --- | --- |
  | `SecurityGuard` | `security` | every suspicious activity (named after `SuspicionReason`) | `WARNING`; a blocked client `CRITICAL` |
  | `Bank` | `client` | `client_registered`, `client_logged_in`, `client_logged_out` (with `details.session_id`; `details.expired` for a late logout), `client_unblocked` | `INFO` |
  | `Bank` | `account` | `account_opened`, `account_frozen`, `account_unfrozen`, `account_closed`, `interest_credited` | `INFO` |
  | `Bank.screen()` | `risk` | `risk_assessed`, `operation_blocked` | `INFO` / `WARNING` for a medium risk / `CRITICAL` |
  | `TransactionQueue` | `transaction` | `transaction_queued`, `transaction_cancelled` | `INFO` |
  | `TransactionProcessor` | `transaction` | `transaction_completed`; `transaction_failed` (`details.will_retry` tells a retry from a final failure) | `INFO`; `ERROR`, an unexpected error `CRITICAL` |

  Life-cycle events are recorded once the change is made; a refused change
  appears only as a `security` event. An operation a client makes through
  their session (`ClientPortal`, or a bank method given `actor=`) names it
  in `details.session_id` of every event it records, `transaction_queued`
  included; the processor's events have none, since it runs the queue on
  the bank's behalf. Transaction events name both parties
  in `details` (`sender_id`, `recipient_id`). The queue does not know the
  clients, so its events carry the initiating account without a client id.
  `bank.suspicious_activities` is a view of the `security` events.
- `RiskAnalyzer` - scores a transaction with independent rules and turns
  the score into a level. Rules and thresholds are constructor arguments;
  a new rule is a new `RiskRule` subclass.

  | Rule | Fires when | Score |
  | --- | --- | --- |
  | `large_amount` | the amount is at least 500 000 RUB / at least 2 000 000 RUB | 40 / 70 |
  | `high_frequency` | the client's 5th transaction requested within 10 minutes (by `requested_at`; a retry is not a new transaction) | 30 |
  | `new_recipient` | a transfer to an account opened less than 7 days ago, or to a recipient the sender has never paid before (the client's own accounts included) | 20 |
  | `night_operation` | the transaction was requested between 22:00 and 06:00 (`requested_at`: the moment it was created, or the moment the client scheduled it for; not the moment the bank runs or retries it) | 20 |

  Levels: `low` below 40, `medium` from 40, `high` from 70.
- Blocking: before any money moves, the processor calls
  `bank.screen(transaction)`. The hard rules come first (the night window
  00:00-05:00, a blocked client); then the transaction is scored. `low`
  and `medium` go on (`medium` is logged as a warning); `high` is refused
  with `RiskBlockedError`, which is not retried, so the transaction fails
  at once. Direct `bank.deposit()` / `bank.withdraw()` calls are back-office
  operations and are not scored.
- `AuditReport` - reports built from the audit log and the analyzer:
  - `suspicious_operations(min_level="medium")` - risky transactions (the
    latest assessment of each) and the security events;
  - `client_risk_profile(client_id)` - transactions by level, blocked
    ones, maximum and average score, most frequent factors, security events
    and failed attempts, and the client's overall level (the highest one);
  - `error_statistics()` - events by level, failed attempts by error
    type, retried and final failures, the failure rate and how many
    transactions risk control blocked.
- Domain exception: `RiskBlockedError` (carries the score and the factors).

## Bank reports

`BankReport(bank)` builds reports from the bank's current state; it keeps
nothing of its own. Every amount is in roubles, converted at the bank's
rates. Each report is an immutable dataclass (its mappings are read-only)
whose `str()` is the printed form:

- `transaction_statistics()` - transactions by status and by type; the
  volume, average and largest completed transaction; the tariff fees
  collected (charged in the sender's currency, converted); how many
  transactions risk control blocked and the failure rate (the share of
  the finished transactions, cancelled ones never ran). Completed and
  failed transactions come from the history, cancelled ones from the
  bank's audit log, so they are counted only when the queue is given that
  log (`TransactionQueue(clock=bank.now, audit_log=bank.audit_log)`). The
  tariff fees are those of `FeePolicy`; a premium account's own withdrawal
  fee is a term of the account and stays inside the debited amount.
- `top_clients(limit=3)` - the richest clients by the total value of their
  accounts.
- `total_balance()` - everything the bank holds, in roubles and by
  currency, and how many open (active or frozen) accounts hold it; closed
  accounts are left out.

```python
report = BankReport(bank)
print(report.transaction_statistics())
# Transactions: 41 (completed 31, failed 9, cancelled 1)
#   by type: deposit 6, withdrawal 7, transfer 25, external_transfer 2
#   failure rate 22.5% of 40 finished, blocked by risk control 2
#   volume 1839899.00 RUB, average 59351.58 RUB, largest 7000.00 USD (transfer)
#   tariff fees collected 450.00 RUB
```

## Report builder

`ReportBuilder(bank, output_dir)` (package `reporting`) turns the numbers
of `BankReport`, `AuditReport` and the transaction history into three
reports, writes them in three formats and draws their charts:

| Report | Sections | Charts |
| --- | --- | --- |
| `client_report(client_id, since=None, until=None)` | summary, accounts (as they are now), transactions and statement for the period, risk profile | pie: assets by account; bar: transactions by status; line: balance of each account |
| `bank_report(top=3, since=None, until=None)` | summary, balance by currency, accounts by type, transactions by status and by type, top clients, total balance over the period | pie: balance by currency; bar: transactions by type, top clients; line: total balance |
| `risk_report(min_level="medium")` | summary, assessments by risk level, risk factors, suspicious operations, clients by risk, failed attempts by error type, security events | pie: assessments by risk level; bar: risk factors, failed attempts by error type |

A `Report` is a list of sections, each either named values or a table,
so every format works with any report:

- `to_text(report)` - aligned tables for people; UUIDs are cut to 8
  characters, numbers are aligned to the right;
- `export_to_text(report)` - the same text in a `.txt` file;
- `export_to_json(report)` - one JSON document, sections by name; an amount
  is a string (`"150000.00"`), never a float, dates are ISO 8601;
- `export_to_csv(report)` - one CSV file per section, since a CSV file holds
  a single table; a section of named values has the columns `key,value`;
  text that starts like a spreadsheet formula (`=`, `+`, `-`, `@`) gets a
  leading `'`, so a spreadsheet shows it instead of running it;
- `save_charts(report)` - one PNG image per chart; a chart with nothing to
  draw (a client without transactions) is skipped.

Every amount on a chart is in the base currency, so accounts in different
currencies share one axis. A balance line is drawn in steps (a balance
does not change between operations), starts with the balance at `since`
and reaches the end of the period. It shows the cash on the accounts
(an investment portfolio is not a balance movement) converted at today's
rates. A chart has at most eight lines, one colour each: a client with
more accounts gets the seven largest and one line for the sum of the rest
(`Other 2 accounts`). A pie shows only positive parts: an
overdraft is named under the chart instead of being a slice. Charts are
described as data in the report (`PieChart`, `BarChart`, `LineChart`) and
drawn by `ChartRenderer`, so the tests check what a chart shows without
drawing it. Money stays `Decimal`; it becomes `float` only to place a mark
on the picture, and every label shows the exact value.

Files are named by the moment of the call and the kind of report:
`2026-09-26_14-30-05_bank.json`, `2026-09-26_14-30-05_bank_top_clients.csv`,
`2026-09-26_14-30-05_bank_top_clients.png`.
All files of one call share the name, and a name already taken gets `-2`,
`-3`, so nothing is overwritten. The folder is created on the first save;
the programs use `reports/` in the project root (ignored by git, see
`BANK_REPORTS_DIR` in the [README](../README.md#logs)).

```python
builder = ReportBuilder(bank, "reports")
report = builder.client_report(client.client_id, since=datetime(2026, 9, 24))
print(builder.to_text(report))
builder.export_to_json(report)  # reports/2026-09-26_14-30-05_client.json
builder.export_to_csv(report)  # reports/2026-09-26_14-30-05_client_summary.csv, ..._client_accounts.csv, ...
builder.save_charts(report)  # reports/2026-09-26_14-30-05_client_assets.png, ..._client_balance.png, ...
```
