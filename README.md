# Modular Bank Platform

An object-oriented prototype of a modular banking platform. All data lives in memory; there is
no database and no external API; the only third-party runtime dependency is
matplotlib, which draws the report charts.

## Setup

Python 3.12 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Run the program

```bash
python src/main.py
```

The program plays one day of a small bank on a clock it moves by hand, so
every run prints the same story:

1. **Initialization** - the bank, 7 clients and 12 accounts of every type
   in five currencies. Six clients opened their accounts a month ago,
   Sofia opens hers on the day.
2. **Simulation** - 40 transactions go through the priority queue in rounds
   from 09:00 to 08:00 the next morning. Most of them are ordinary
   (salaries, rent, cash, conversions, a payment abroad with a fee, a
   premium overdraft). Some fail: a frozen account, a closed account, a
   client blocked after three wrong passwords, the withdrawal limit, an
   unknown account, missing money after three attempts; one transfer is
   cancelled. Some are suspicious: a large transfer is let through with a
   warning, quick transfers in a row raise the risk, a huge payment abroad
   and a large transfer late in the evening are blocked, a night transfer
   waits in the queue until the night window ends. After every round a feed
   shows what the audit log recorded: `queued`, `completed`, `retry`,
   `failed`, `warning`, `blocked`, `cancelled`. The last round is the back
   office of the bank: monthly interest, money moved into a portfolio and
   back (both recorded in the history), a salary sent twice under one id
   (refused by the queue and by a processor before any money moves) and an
   attempt to open an account that is already closed (refused by the bank
   and by the model).
3. **Logging** - the events of the audit log by name, and the life cycle
   of six transactions as the journal holds it.
4. **Client view** - Oleg logs in and acts through the client portal with
   his session (see [Client sessions](docs/features.md#client-sessions)): he sees his
   accounts, takes 1 000 EUR in cash, is refused Maria's account as one
   that is not found, reads a statement of each account (the balance
   movements, with the balance and the total value after each) and his
   transactions. The bank shows his suspicious operations and his risk
   profile. He logs out, the session is refused from then on, and the
   program lists the audit events and the balance movements that name the
   session.
5. **Reports** - the bank report (totals, balances by currency and account
   type, transactions by status and type, the top three clients, the total
   balance from the start of the day) and the risk report (assessments by
   level, risk factors, suspicious operations, clients by risk, failures by
   error type, security events), printed as text (see
   [Report builder](docs/features.md#report-builder)).
6. **Export** - Oleg's client report for the day, the bank report and the
   risk report are saved to `reports/`: each as a `.txt` and a `.json`
   file, one `.csv` file per section and one `.png` image per chart, 35
   files in all. The program lists the folder and the files. They share
   the time the reports were built, e.g. `2026-09-26_14-30-05_client.json`,
   `2026-09-26_14-30-05_bank_top_clients.csv`,
   `2026-09-26_14-30-05_bank_total_balance.png`, so the files of one run
   stay together and a new run never overwrites an old one.

Warnings and errors of the application log appear in the terminal between
the program's lines; the full log goes to `logs/app.jsonl` (see
[Logs](#logs)). An invalid setting stops the program with one line on
stderr, for example
`Invalid settings: BANK_LOG_LEVEL must be one of debug, info, warning, error, critical; got 'loud'.`

## Run the feature tour

```bash
python src/legacy_demo.py
```

The tour shows the platform feature by feature, from the accounts up to
risk control; it runs one stage per feature set and prints a banner before each:

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
the tour's lines; the full log goes to `logs/app.jsonl` (see below).

## Logs

Both programs write two logs into `logs/` in the project root; the folder is
created on the first run and is ignored by git. Both files are JSON Lines
(one JSON object per line) and are only appended to, so each run adds to
them (delete a file to start over).

| File | What it holds | Who reads it |
| --- | --- | --- |
| `logs/audit.jsonl` | the audit log: every event of the program's bank (the feature tour: of stage 5), complete and never rewritten | auditors, `AuditLog.load_events()`, the audit reports |
| `logs/app.jsonl` | the application log: every audit event of every stage plus technical `DEBUG` traces (a transaction attempt started, a delayed transaction became due) and infrastructure errors such as a failed audit write | developers, log tools |

The terminal shows the same application log as readable lines, from
`WARNING` up by default, mixed in order with the program's own output:

```
09-24 13:00:00 CRITICAL bank.audit        operation_blocked: external_transfer of 25000.00 USD: high risk, score 90 (large_amount, new_recipient) category=risk client_id=7db525f8 ...
```

Every application log record carries two moments: `logged_at` - when it
was written, by the wall clock - and `event_time` - when it happened by the
bank's clock, which the programs move by hand (into the night, a day later).

Environment variables (read once at start by `Settings.from_env()`):

| Variable | Meaning | Default |
| --- | --- | --- |
| `BANK_AUDIT_LOG` | the audit log file | `logs/audit.jsonl` |
| `BANK_LOG_FILE` | the application log file | `logs/app.jsonl` |
| `BANK_LOG_LEVEL` | the lowest level shown in the terminal: `debug`, `info`, `warning`, `error`, `critical`; the file always gets everything from `DEBUG` | `warning` |
| `BANK_REPORTS_DIR` | the folder the reports and charts are saved to | `reports/` |

```bash
BANK_LOG_LEVEL=info python src/main.py                        # show every business event in the terminal
BANK_LOG_LEVEL=error python src/main.py                       # a quieter terminal: errors and blocked operations only
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
back into `AuditEvent` objects. The tests never write to `logs/`: the
smoke tests of the programs point both variables to a temporary folder.

## Run the tests and linter

```bash
pytest            # unit + integration tests
ruff check .      # PEP 8 / import order / modern syntax checks
ruff format .     # auto-format
```

## Run with Docker

The image holds the code, the tests and every dependency, so nothing but
Docker is needed on the machine. Build it once from the repository root:

```bash
docker build -t modular-bank-platform .
```

The default command plays the program. The logs and the reports are
written under `/app` inside the container; mount the two folders to keep
them on the host:

```bash
docker run --rm -v "$PWD/logs:/app/logs" -v "$PWD/reports:/app/reports" modular-bank-platform
```

Any other command runs in the same image:

```bash
docker run --rm modular-bank-platform python src/legacy_demo.py   # the feature tour
docker run --rm modular-bank-platform pytest                       # the test suite
docker run --rm -e BANK_LOG_LEVEL=info modular-bank-platform       # every business event in the terminal
```

## Architecture

Three layers, each importing only from the ones below it:

- `models/` - the domain: accounts of four types, the client, the
  transaction with its status machine, the portfolio. They validate their
  own state and know nothing of clocks, logs or other services.
- `services/` - the bank as a facade over the models, with security and
  sessions, currency conversion, the transaction queue and processor, the
  transaction history, the audit log, risk control and the report
  calculations. Money moves only through `Bank`, and every movement is
  recorded in the history; `ClientPortal` checks a client's session and
  delegates to the bank.
- `reporting/` - the report model, the text, JSON and CSV exporters and the
  chart renderer; it reads the services and writes files.

`main.py` and `legacy_demo.py` sit on top and only assemble these parts.
The feature reference, module by module, is in
[docs/features.md](docs/features.md); the design principles and patterns
behind the code are in [docs/oop_principles.md](docs/oop_principles.md).

```
modular-bank-platform/
├── README.md
├── Dockerfile              # the program, the tour and the tests in one image
├── .dockerignore
├── pyproject.toml          # pytest and ruff configuration
├── requirements.txt        # runtime dependencies (matplotlib)
├── requirements-dev.txt    # pytest, ruff
├── src/
│   ├── main.py             # the program: one day of the bank, from salaries to reports
│   ├── legacy_demo.py      # feature tour, one function per stage
│   ├── settings.py         # Settings read from environment variables
│   ├── logging_setup.py    # application logging: console and JSON Lines formatters
│   ├── exceptions.py       # custom exception hierarchy
│   ├── utils.py            # value normalisation helpers, ManualClock
│   ├── services/
│   │   ├── bank.py         # Bank (facade over clients, accounts, security)
│   │   ├── security.py     # SecurityGuard, SuspiciousActivity, SuspicionReason
│   │   ├── session.py      # ClientSession, SessionStore
│   │   ├── client_portal.py          # ClientPortal: a client's own operations through a session
│   │   ├── currency.py     # CurrencyConverter, reference rates to RUB
│   │   ├── fees.py         # FeePolicy
│   │   ├── transaction_queue.py      # TransactionQueue
│   │   ├── transaction_processor.py  # TransactionProcessor, error log, report
│   │   ├── transaction_history.py    # TransactionHistory, BalanceMovement, MovementKind
│   │   ├── audit_log.py    # AuditLog, AuditEvent, AuditLevel, AuditCategory
│   │   ├── risk.py         # RiskAnalyzer, risk rules, RiskAssessment, RiskLevel
│   │   ├── audit_report.py # AuditReport and its three reports
│   │   └── bank_report.py  # BankReport: transaction statistics, top clients, total balance
│   ├── reporting/
│   │   ├── report.py       # Report, ReportKind, sections, charts as data (PieChart, BarChart, LineChart)
│   │   ├── exporters.py    # ReportExporter and its formats: text, JSON, CSV
│   │   ├── charts.py       # ChartRenderer: pie, bar and line charts as PNG (matplotlib)
│   │   └── builder.py      # ReportBuilder: client, bank and risk reports, export to files
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
│   └── integration/        # account, bank, transaction and risk scenarios, smoke tests of the programs
├── docs/
│   ├── features.md         # the feature reference: models, bank, sessions, transactions, audit, reports
│   └── oop_principles.md   # interview-style notes on OOP, patterns, security
├── reports/                # saved reports, created on the first save, ignored by git
└── logs/                   # created by the programs, ignored by git
    ├── audit.jsonl         # the audit log, one JSON event per line
    └── app.jsonl           # the application log, one JSON record per line
```
