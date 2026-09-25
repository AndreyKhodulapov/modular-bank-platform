# Modular Bank Platform

An object-oriented prototype of a modular banking platform. All data lives in memory; there is
no database, no external API and no third-party runtime dependency.

## Current scope

### Accounts Basic

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

### Accounts Advanced

Three subclasses of `BankAccount`; each overrides `withdraw()`,
`get_account_info()` and `__str__()`:

- `SavingsAccount` - keeps `min_balance` locked on the account and credits
  interest with `apply_monthly_interest()` at a `monthly_rate`.
- `PremiumAccount` - limits ten times higher, an `overdraft_limit` that lets the
  balance go negative and a fixed `withdrawal_fee` charged on every withdrawal.
- `InvestmentAccount` - free cash plus a `Portfolio` of virtual asset types
  (`stocks`, `bonds`, `etf`). `invest()` / `divest()` move money between cash
  and the portfolio, `withdraw()` never touches invested money, and
  `project_yearly_growth(growth_rates)` estimates one year of growth.

Every account can be frozen, unfrozen and closed (`freeze()`, `unfreeze()`,
`close()`). Closing is a settlement: the cash is paid out and returned, even
below a savings `min_balance`. It is refused while the account is in
overdraft, holds money in a portfolio or is frozen with money on it.

### Bank System

- `Bank` - the entry point to the platform. It registers clients with a
  password, opens accounts of a registered type (`basic`, `savings`,
  `premium`, `investment`), closes, freezes and unfreezes them, runs deposits
  and withdrawals, searches accounts by client, status, currency, type and
  balance range, and reports `get_total_balance()` and
  `get_clients_ranking()` in roubles. Every balance change it makes goes to
  the [transaction history](#transaction-history).
- `SecurityGuard` - stores salted password hashes, blocks a client after three
  failed logins in a row, forbids operations between 00:00 and 05:00 and keeps
  a log of suspicious activities.
- `CurrencyConverter` - converts amounts into roubles using fixed reference
  rates (replaceable by passing another rate table).
- Domain exceptions: `ClientNotFoundError`, `AccountNotFoundError`,
  `AuthenticationError` (carries `attempts_left`), `ClientBlockedError`,
  `OperationTimeRestrictedError` (all derive from `BankError`).

Security rules applied by the bank:

| Rule | Behaviour |
| --- | --- |
| Login lockout | 3 wrong passwords in a row block the client; `unblock_client()` restores access |
| Blocked client | cannot open, close or unfreeze accounts or move money |
| Night window 00:00-05:00 | open, close, unfreeze, deposit, withdraw and unblock are refused; login, freeze and queries are allowed |
| Suspicious activity log | failed logins, blocking, attempts by a blocked client or for an unknown id, night attempts, operations on frozen or closed accounts, amounts of 500 000 RUB and more; kept in the audit log as `security` events |

Known limitations:

- `invest()`, `divest()` and `apply_monthly_interest()` are not part of
  `Bank` and are called on the account itself, so the night window,
  blocking and the suspicious activity log do not cover them, and the
  transaction history has no movement for them. The bank demo invests on
  Oleg's account this way.

### Transactions

- `Transaction` - a request to move money: id, type (`deposit`,
  `withdrawal`, `transfer`, `external_transfer`), amount, currency, fee,
  sender and recipient, priority, status, failure reason, attempts, the
  amounts actually debited and credited, and the timestamps `created_at`,
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
  cancellation.
- `TransactionProcessor` - executes transactions through `Bank`, so the
  night window, blocked clients, limits and the suspicious activity log
  apply. It converts amounts into each account's currency, charges fees,
  retries temporary failures and keeps an error log (`errors`) of every
  failed attempt. `process_queue()` runs everything that is due and returns
  a `ProcessingReport` (completed, failed, rescheduled). The queue should
  share the bank's clock (`TransactionQueue(clock=bank.now)`), so that
  delays and retries are measured by the same time; on its own the queue
  uses the wall clock, as does `Transaction` for a `created_at` that is not
  passed in. The demo and the tests pass both explicitly.
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
| Atomic transfer | both accounts are checked and both amounts converted before any money moves; if the bank still refuses the credit after the debit (a blocked owner, the deposit limit), the debit is put back with `bank.refund()`, which no bank rule or limit can refuse. The debit itself was a real bank operation, so a large one stays in the suspicious activity log even after it is put back; the history keeps both the debit and its refund |
| Retries | the night window and insufficient funds are retried up to 3 attempts with an exponential delay (5, 10 minutes by default); other bank errors fail at once; an unexpected error fails the transaction, is logged and re-raised |

### Transaction history

`TransactionHistory` is the bank's record of what happened to the money,
kept in memory next to the accounts (`bank.history`, or injected with
`Bank(history=...)`). It holds two things:

- **Transactions** - each one once, when it reaches its final status after
  the last attempt: `completed` or `failed`. A cancelled transaction never
  ran, so it stays in the queue and the audit log only.
  `transactions(account_ids=..., status=..., transaction_type=..., since=...,
  until=...)` returns them in the order they finished; `account_ids` matches
  both outgoing and incoming ones, and the time range applies to
  `finished_at`.
- **Balance movements** - one `BalanceMovement` per change of a balance made
  through the bank: the moment, the account, the kind (`opening`, `deposit`,
  `withdrawal`, `refund`, `payout`), the signed change in the account's
  currency, the balance right after it and the transaction id (`None` for a
  back-office operation). `movements(account_id, since=..., until=...)`
  returns them in order.

`Bank` is the only writer of movements: an account opened with money,
`deposit()`, `withdraw()` (both take an optional `transaction_id`), the
payout on `close_account()` and `refund()`, which puts back the debit of a
rolled-back transfer. The change is measured as the balance after minus the
balance before, so a premium account's own withdrawal fee is part of the
withdrawal. A refused operation leaves no movement. As a result the
movements of every account add up to its balance, except for the
operations past the bank listed in the known limitations above.

```python
for movement in bank.history.movements(account.account_id):
    print(movement)  # 09-24 14:00 3c50a706 withdrawal     -3010.00     -2010.00 RUB
```

### Audit and Risk

- `AuditLog` - one append-only journal for the whole bank. Every
  `AuditEvent` is immutable and structured: timestamp, level (`INFO`,
  `WARNING`, `ERROR`, `CRITICAL`), category (`security`, `transaction`,
  `risk`, `account`, `client`), event name, message, client, account and transaction ids and a
  `details` mapping. Events are kept in memory and, when a `file_path` is
  given, appended to a JSON Lines file as soon as they are recorded;
  `AuditLog.load_events()` reads a file back. `filter()` combines a minimum or
  exact level, category, event, client, account, transaction and a time
  range. Every event is also passed to the application log (see
  [Logs](#logs)).
- Who writes to it:

  | Writer | Category | Events | Level |
  | --- | --- | --- | --- |
  | `SecurityGuard` | `security` | every suspicious activity (named after `SuspicionReason`) | `WARNING`; a blocked client `CRITICAL` |
  | `Bank` | `client` | `client_registered`, `client_unblocked` | `INFO` |
  | `Bank` | `account` | `account_opened`, `account_frozen`, `account_unfrozen`, `account_closed` | `INFO` |
  | `Bank.screen()` | `risk` | `risk_assessed`, `operation_blocked` | `INFO` / `WARNING` for a medium risk / `CRITICAL` |
  | `TransactionQueue` | `transaction` | `transaction_queued`, `transaction_cancelled` | `INFO` |
  | `TransactionProcessor` | `transaction` | `transaction_completed`; `transaction_failed` (`details.will_retry` tells a retry from a final failure) | `INFO`; `ERROR`, an unexpected error `CRITICAL` |

  Life-cycle events are recorded once the change is made; a refused change
  appears only as a `security` event. Transaction events name both parties
  in `details` (`sender_id`, `recipient_id`). The queue does not know the
  clients, so its events carry the initiating account without a client id.
  `bank.suspicious_activities` is a view of the `security` events.
- `RiskAnalyzer` - scores a transaction with independent rules and turns
  the score into a level. Rules and thresholds are constructor arguments;
  a new rule is a new `RiskRule` subclass.

  | Rule | Fires when | Score |
  | --- | --- | --- |
  | `large_amount` | the amount is at least 500 000 RUB / at least 2 000 000 RUB | 40 / 70 |
  | `high_frequency` | the client's 5th transaction within 10 minutes (a retry is not a new transaction) | 30 |
  | `new_recipient` | a transfer to an account opened less than 7 days ago, or to a recipient the sender has never paid before (the client's own accounts included) | 20 |
  | `night_operation` | between 22:00 and 06:00 | 20 |

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

## Project structure

```
modular-bank-platform/
├── README.md
├── pyproject.toml          # pytest and ruff configuration
├── requirements.txt        # runtime dependencies (none, stdlib only)
├── requirements-dev.txt    # pytest, ruff
├── src/
│   ├── main.py             # demonstration script, one function per stage
│   ├── settings.py         # Settings read from environment variables
│   ├── logging_setup.py    # application logging: console and JSON Lines formatters
│   ├── exceptions.py       # custom exception hierarchy
│   ├── utils.py            # value normalisation helpers, ManualClock
│   ├── services/
│   │   ├── bank.py         # Bank (facade over clients, accounts, security)
│   │   ├── security.py     # SecurityGuard, SuspiciousActivity, SuspicionReason
│   │   ├── currency.py     # CurrencyConverter, reference rates to RUB
│   │   ├── fees.py         # FeePolicy
│   │   ├── transaction_queue.py      # TransactionQueue
│   │   ├── transaction_processor.py  # TransactionProcessor, error log, report
│   │   ├── transaction_history.py    # TransactionHistory, BalanceMovement, MovementKind
│   │   ├── audit_log.py    # AuditLog, AuditEvent, AuditLevel, AuditCategory
│   │   ├── risk.py         # RiskAnalyzer, risk rules, RiskAssessment, RiskLevel
│   │   └── audit_report.py # AuditReport and its three reports
│   └── models/
│       ├── account.py             # AbstractAccount, BankAccount
│       ├── savings_account.py     # SavingsAccount
│       ├── premium_account.py     # PremiumAccount
│       ├── investment_account.py  # InvestmentAccount
│       ├── portfolio.py           # Portfolio
│       ├── transaction.py         # Transaction and its status machine
│       ├── enums.py               # account, client, currency, asset and transaction enums
│       └── client.py              # Client
├── tests/
│   ├── conftest.py         # shared fixtures
│   ├── unit/               # one module per model, service or helper
│   └── integration/        # account, bank, transaction and risk scenarios, demo smoke test
├── docs/
│   └── oop_principles.md   # interview-style notes on OOP, patterns, security
└── logs/                   # created by the demo, ignored by git
    ├── audit.jsonl         # the audit log, one JSON event per line
    └── app.jsonl           # the application log, one JSON record per line
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Run the demo

```bash
python src/main.py
```

The script runs one stage per feature set and prints a banner before each:

1. **Accounts Basic** - an active and a frozen regular account, rejected
   operations on the frozen one, valid deposit and withdrawal, validation and
   limit errors.
2. **Accounts Advanced** - savings accounts with interest and a minimum
   balance, premium accounts with overdraft and fee, investment accounts with
   a portfolio and a yearly growth projection.
3. **Bank System** - three clients (a minor is refused), accounts of every
   type, a successful login and a lockout after three wrong passwords,
   freezing, the night window, closing, searches, totals and the ranking in
   roubles, and the suspicious activity log.
4. **Transactions** - ten transactions in the queue: priorities, a delayed
   top-up, a cancelled transfer, a transfer into a frozen account, an
   external transfer with a fee, a premium overdraft, a regular account that
   runs short and succeeds on retry, and a night transfer that completes in
   the morning; then the results, collected fees and the error log.
5. **Audit and Risk** - ordinary transactions (a salary, transfers to a
   known recipient, cash, a small payment abroad) pass with a low risk;
   suspicious ones - a large transfer to an account opened today, a huge
   payment abroad, six quick transfers in a row, a large transfer late in
   the evening, a night transfer - are scored, allowed with a warning or
   blocked. The audit log is appended to `logs/audit.jsonl`, filtered, and
   summarised in the three reports.

A final summary treats all created accounts through the common interface.
Warnings and errors of the application log appear in the terminal between
the demo's lines; the full log goes to `logs/app.jsonl` (see below).

## Logs

The demo writes two logs into `logs/` in the project root; the folder is
created on the first run and is ignored by git. Both files are JSON Lines
(one JSON object per line) and are only appended to, so each run adds to
them (delete a file to start over).

| File | What it holds | Who reads it |
| --- | --- | --- |
| `logs/audit.jsonl` | the audit log: security, transaction and risk events of stage 5, complete and never rewritten | auditors, `AuditLog.load_events()`, the audit reports |
| `logs/app.jsonl` | the application log: every audit event of every stage plus technical `DEBUG` traces (a transaction attempt started, a delayed transaction became due) and infrastructure errors such as a failed audit write | developers, log tools |

The terminal shows the same application log as readable lines, from
`WARNING` up by default, mixed in order with the demo's own output:

```
09-24 13:00:00 CRITICAL bank.audit        operation_blocked: external_transfer of 25000.00 USD: high risk, score 90 (large_amount, new_recipient) category=risk client_id=7db525f8 ...
```

Every application log record carries two moments: `logged_at` - when it
was written, by the wall clock - and `event_time` - when it happened by the
bank's clock, which the demo moves by hand (into the night, a day later).

Environment variables (read once at start by `Settings.from_env()`):

| Variable | Meaning | Default |
| --- | --- | --- |
| `BANK_AUDIT_LOG` | the audit log file | `logs/audit.jsonl` |
| `BANK_LOG_FILE` | the application log file | `logs/app.jsonl` |
| `BANK_LOG_LEVEL` | the lowest level shown in the terminal: `debug`, `info`, `warning`, `error`, `critical`; the file always gets everything from `DEBUG` | `warning` |

```bash
BANK_LOG_LEVEL=info python src/main.py                        # show every business event in the terminal
BANK_AUDIT_LOG=/tmp/bank/audit.jsonl python src/main.py       # write the audit log somewhere else
```

Every line is one record, so standard tools work on the files:

```bash
tail -n 5 logs/audit.jsonl                                           # the latest audit events
grep '"level": "CRITICAL"' logs/audit.jsonl                          # blocked operations and clients
jq -c 'select(.category == "risk") | [.timestamp, .message]' logs/audit.jsonl
jq -c 'select(.logger == "bank.transactions") | [.event_time, .transaction_id, .attempt]' logs/app.jsonl
```

In code, `AuditLog.load_events("logs/audit.jsonl")` reads the audit log
back into `AuditEvent` objects. The tests never write to `logs/`: the demo
smoke test points both variables to a temporary folder.

## Run the tests and linter

```bash
pytest            # unit + integration tests
ruff check .      # PEP 8 / import order / modern syntax checks
ruff format .     # auto-format
```
