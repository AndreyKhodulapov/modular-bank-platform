# OOP principles applied in this project

Short, interview-ready definitions of the concepts used in this project,
each followed by the place in the code where it is applied.

## Encapsulation

Bundling data with the methods that operate on it and hiding the internal
state behind a controlled interface. The object stays valid because outsiders
cannot mutate it arbitrarily.

*In the project:* `AbstractAccount` stores `_balance` and `_status` as
protected attributes and exposes them through read-only properties. The
balance can only change via `deposit()` / `withdraw()`, which enforce every
business rule. `Client` validates its personal data once and exposes it only
through read-only properties; its status changes only through `block()` /
`unblock()`, and `account_ids` returns a copy of the internal list. `Portfolio.holdings` returns a copy, so the allocation can only
change through `add()` / `remove()`, and `InvestmentAccount` never hands out
its `Portfolio` object at all.

## Inheritance

A class reuses and extends the state and behaviour of a parent class,
forming an "is-a" relationship.

*In the project:* `BankAccount` inherits the common state and `__repr__` from
`AbstractAccount`. `SavingsAccount`, `PremiumAccount` and `InvestmentAccount`
inherit validation, status checks, currency and limits from `BankAccount` and
add only their own rules; `PremiumAccount` overrides the class attributes
`MAX_DEPOSIT` / `MAX_WITHDRAWAL` to raise the limits. Every custom error
inherits from `BankError`, so one `except BankError` clause catches the whole
family.

## Polymorphism

Different classes respond to the same message (method call) in their own
way, so client code can work with the abstract interface instead of concrete
types.

*In the project:* every account type overrides `withdraw()`,
`get_account_info()` and `__str__()`. The same `withdraw(100)` call empties a
regular account, is refused by a savings account that must keep its minimum,
charges a fee and dips into overdraft on a premium account, and only spends
free cash on an investment account. The feature tour's summary and the integration
test iterate over a `list[AbstractAccount]` without checking concrete types.
Subclasses extend rather than replace behaviour: `get_account_info()` and
`__str__()` call `super()` and append their own fields. The same
polymorphism keeps `TransactionProcessor` free of account types: it only
calls `bank.withdraw()`, and each account decides how far it can be
debited (a regular account stops at zero, a premium account uses its
overdraft), so a new account type with its own rule needs no change in the
processor.

## Abstraction

Exposing only the essential operations of a concept and hiding the details
of how they work. Abstract classes describe *what* must exist, not *how*.

*In the project:* `AbstractAccount(ABC)` declares the three abstract methods
and cannot be instantiated; `BankAccount` provides the implementation.
`BankAccount._prepare_withdrawal()` runs the checks shared by every
withdrawal (status, amount, limit), so subclasses only implement the part
that differs - how much money is actually available. At the service level,
`Bank` hides three collaborators behind one set of methods: callers never
see password hashes, the night window or exchange rates.

## Composition

Building an object out of other objects it *has* ("has-a") instead of
inheriting from them ("is-a"). Parts can be replaced or tested separately.

*In the project:* `InvestmentAccount` has a `Portfolio`; `Bank` has a
`SecurityGuard` and a `CurrencyConverter` and delegates security and
conversion to them. `TransactionProcessor` has a `Bank` and a `FeePolicy`
and moves money only through the bank's public methods, so every bank rule
also applies to transactions. `Client` is deliberately not composed of a separate
personal-data object: the client is the person, so a single class holds the
data together with the client-specific rules (age check, status, account
numbers).

## SOLID

- **S - Single Responsibility:** each module owns one concern -
  `exceptions.py` (error types), `utils.py` (value normalisation),
  `client.py` (client data and status), one module per account type,
  `portfolio.py` (asset allocation and growth projection, no knowledge of cash
  or accounts). In the service layer `Bank` only coordinates, `SecurityGuard`
  owns passwords, lockout, the night window and the audit log, and
  `CurrencyConverter` owns exchange rates. For transactions, `Transaction`
  owns its data and status rules, `TransactionQueue` only orders,
  `FeePolicy` only prices, and `TransactionProcessor` executes.
- **O - Open/Closed:** a new account type extends `BankAccount` and overrides
  `withdraw()`, `get_account_info()` and `__str__()`; the shared checks in
  `_prepare_withdrawal()` and the limit constants are reused, not modified.
  New asset types are added to the `AssetType` enum, new currencies to
  `Currency`. The bank offers a new account type after one new entry in
  `Bank.ACCOUNT_TYPES`; `open_account()` and `search_accounts()` stay
  unchanged.
- **L - Liskov Substitution:** every subclass can be used wherever an
  `AbstractAccount` is expected. Subclasses apply their own funds rules, but the
  base contract - "either debit the balance and return it, or raise a
  `BankError`" - is never broken, and the shared checks always run first and
  in the same order.
- **I - Interface Segregation:** the abstract interface is minimal (three
  methods); type-specific operations (`apply_monthly_interest`, `invest`,
  `divest`, `project_yearly_growth`) live only on the classes that need them.
- **D - Dependency Inversion:** high-level modules depend on abstractions,
  not on concrete implementations. Partly applied: `SecurityGuard` depends on
  an abstract clock (any zero-argument callable returning a `datetime`), not on
  the system time, and `Bank` receives its `SecurityGuard` and
  `CurrencyConverter` from outside. These two are still concrete classes, not
  interfaces - a deliberate simplification while each has one implementation.

## Domain modelling

Representing business concepts as explicit types with their own invariants,
instead of passing raw primitives around.

*In the project:* `AccountStatus`, `ClientStatus`, `Currency`, `AssetType`,
`TransactionType`, `TransactionStatus`, `TransactionPriority` and
`SuspicionReason` are enums rather
than free strings; `Client` and `Portfolio` are dedicated types with validation;
money is `Decimal` normalised by `to_money()` (floats converted through
`str()` to avoid binary representation artefacts, rounded half-up to two
decimal places), while rates go through `to_rate()` and keep their precision
because `0.005` is a legitimate monthly rate. Errors carry structured data
(`requested`, `available`, `limit`, `account_id`) instead of only text.

The model distinguishes *entities* from *values*. `Client` and the accounts
are entities: they have an identity (`client_id`, `account_id`) that stays the
same while their state changes, so two clients with identical personal data
are still different clients (`__eq__` / `__hash__` compare ids only). Money
amounts and enum members are values: two equal amounts are interchangeable.

## Design patterns

- **Facade** - one simple interface in front of a subsystem.
  `Bank` (`src/services/bank.py`) is the only entry point to clients,
  accounts, `SecurityGuard` and `CurrencyConverter`; its methods combine
  several steps (night check, client status, amount review, the account
  operation, audit) into one call such as `bank.withdraw(account_id, amount)`.
  `ReportBuilder` is a facade over the reports, the exporters and the chart
  renderer.
- **Registry** - a well-known object that stores objects by key so they can be
  found later. `Bank._clients` and `Bank._accounts` store clients and accounts
  by id and back `get_client()`, `get_account()` and `search_accounts()`;
  `SecurityGuard._passwords` stores password hashes by client id.
  `Bank.ACCOUNT_TYPES` is a registry of classes: account type name -> class.
- **Simple Factory** - creating objects of a class chosen at runtime.
  `Bank.open_account(client_id, "savings", ...)` looks the class up in
  `ACCOUNT_TYPES` and instantiates it, so callers never name concrete classes.
- **Template Method** - a base class fixes the algorithm and subclasses
  override one step. `AbstractAccount.close()` always runs the same checks and
  pays out the cash; it calls the `total_value` step to find anything held
  besides cash: the base returns the balance, while `InvestmentAccount` adds
  the portfolio, so an account with invested money cannot be closed.
- **Dependency Injection** - an object receives its collaborators instead of
  creating them. `SecurityGuard(clock=...)`, `TransactionQueue(clock=...)`,
  `SecurityGuard(audit_log=...)`,
  `Bank(security=..., converter=..., risk_analyzer=...)` and
  `TransactionProcessor(bank, fee_policy=...)`; the defaults (`datetime.now`, reference
  rates, the default risk rules) are used only when nothing is passed.
- **Value Object** - an immutable object defined by its values, not identity.
  `AuditEvent`, `RiskAssessment`, `RiskFactor`, `SuspiciousActivity` and
  `TransactionErrorRecord` (frozen dataclasses), money as `Decimal` and the
  enum members.
- **Strategy** - an interchangeable algorithm behind a fixed interface.
  `FeePolicy.calculate()` prices a transaction; `TransactionProcessor(bank,
  fee_policy=...)` accepts any tariff without changing its own code. Each
  `RiskRule` is a strategy too: `RiskAnalyzer` runs a list of them and only
  adds up their scores. `ReportExporter` (text, JSON, CSV) is a strategy for
  the format of a report.
- **State machine** - an object whose allowed actions depend on its state.
  `Transaction` keeps a table of allowed status transitions and refuses any
  other move with `InvalidTransactionStateError`.
- **Fake (test double)** - a simplified working replacement of a dependency.
  `ManualClock` (`src/utils.py`) is a clock whose time is set by hand; the demo
  and the tests use it to step into the night window.

## Security basics

- **Passwords are never stored.** `SecurityGuard` keeps a PBKDF2-HMAC-SHA256
  hash with a random 16-byte salt per client, so equal passwords give
  different hashes and a leaked store cannot be reversed cheaply. Hashes are
  compared with `hmac.compare_digest()`, which takes the same time wherever the
  first differing byte is, so timing does not leak information.
- **Brute force is limited.** The third wrong password in a row blocks the
  client; a successful login resets the counter. The counter is kept by
  `Client` next to the status, so unblocking resets it by whatever path it
  happens and the two can never disagree.
- **Risky time is closed.** Between 00:00 and 05:00 the bank refuses
  operations that move money or give access back; freezing (a protective
  action) and logins stay available.
- **Suspicious actions are recorded, not only refused.** Failed logins,
  blocking, attempts by a blocked client, night attempts, operations on
  frozen or closed accounts and amounts of at least 500 000 RUB go to an
  append-only log.

## Transaction processing

- **State machine.** A transaction's status can only follow
  `PENDING -> PROCESSING -> COMPLETED | FAILED`, `PROCESSING -> PENDING`
  (retry) and `PENDING -> CANCELLED`. Final statuses have no way out, so a
  completed transaction can never be executed again or cancelled.
- **Idempotency.** Running the same operation twice must not double its
  effect. `start()` is allowed only from `PENDING`, so a transaction that
  was already processed is rejected instead of moving money a second time.
- **Priority queue on a heap.** `heapq` gives O(log n) insertion and
  removal of the most urgent item. The key is `(-priority, sequence)`: the
  sequence number keeps first-in-first-out order within a priority and
  makes the order deterministic. Delayed transactions wait in a second heap
  keyed by time, so a future urgent item never blocks ready ones.
- **Lazy deletion.** Removing an arbitrary element from a heap is O(n);
  instead, `cancel()` only marks the transaction and forgets its entry, and
  stale entries are skipped when they reach the top.
- **Atomicity and compensation.** A transfer has two steps (debit, credit)
  and must not stop halfway. Both accounts are checked and both amounts
  converted before money moves; if the bank still refuses the credit, a
  compensating operation returns the debit. The compensation is
  `bank.refund()`, which skips the checks of a client operation: a rollback
  must not be refused by a deposit limit or the night window, and must not
  be reviewed as a new client operation. It still goes through the bank, so
  the history records it next to the debit it cancels. This is the idea
  behind the Saga pattern for operations that span several services, where
  one database transaction is not available.
- **Retries with exponential backoff.** Only temporary errors are retried
  (the night window ends, money may arrive); permanent ones (a frozen
  account, bad input) fail at once, because retrying them only adds load.
  Each retry waits twice as long as the previous one, and `max_attempts`
  bounds the total.
- **Money and currencies.** Amounts stay `Decimal`; conversion between two
  foreign currencies goes through the base currency (a cross rate) and is
  rounded once, at the end, so rounding errors do not accumulate.

## Transaction history

- **State, not a log.** The history is a separate store owned by the bank,
  not a query over the logs. A log describes what the system did and may be
  sampled, rotated or lost; the audit log is evidence, but it records
  events, not balances. Questions like "what did this account look like on
  Monday" need the bank's own record, written in the same step as the change
  of the balance. Real systems keep it as a ledger table in the database;
  the logs point at it by transaction id.
- **One writer.** Only `Bank` records movements, in the same methods that
  change balances, so a movement cannot be forgotten or written twice, and
  a refused operation leaves none. The processor passes the transaction id
  through `deposit()` / `withdraw()` / `refund()` and adds the finished
  transaction itself - once, after its last attempt.
- **The actual change.** A movement stores the balance after minus the
  balance before, not the requested amount, so fees charged by the account
  (the premium withdrawal fee) are not lost. The invariant "the movements of
  an account add up to its balance" is checked by the integration tests.
- **`balance_after`.** Each movement keeps the balance right after it, so a
  balance chart or a statement for any period is a plain filter, without
  replaying every earlier movement (a running balance, as on a bank
  statement).
- **Immutability.** Movements are frozen dataclasses and the getters return
  copies; a finished transaction has no status transitions left, so what is
  in the history cannot change.

## Structured logging

Emitting log records as key-value data (not free text) so they can be
filtered, aggregated and shipped to monitoring systems.

*In the project:* every `AuditEvent` has fields - time, level, category,
event name, client, account and transaction ids and a `details` mapping -
so `AuditLog.filter()` selects by any of them and `AuditReport` counts them.
The application log does the same with the standard `logging` module: the
fields travel in `extra={"fields": {...}}` (one key, so they never clash with
`LogRecord` attributes), a message stays constant (`attempt started`) while
the values go to fields, and a formatter decides the output - JSON Lines in
the file, `key=value` lines in the terminal. JSON Lines means one JSON object
per line: easy to append, to stream and to load into log tools (ELK, Loki,
`jq`).

- **Libraries do not configure logging.** The services only call
  `logging.getLogger("bank.<component>")`; handlers, formats and levels are
  chosen once by the program (`configure_logging()` in `main.py`). Until then
  a `NullHandler` on `bank` drops the records quietly, so the services work
  the same in tests, in a script or inside another application.
- **A named hierarchy.** Loggers live under `bank` (`bank.audit`,
  `bank.transactions`, `bank.queue`), so one call configures all of them and
  a single component can be turned up or down.
- **Levels per handler.** The logger passes everything any handler wants;
  each handler keeps its own threshold - the file stores `DEBUG` and up, the
  terminal shows `WARNING` and up.
- **Two moments.** `logged_at` is when the line was written (wall clock);
  `event_time` is when the event happened by the bank's clock. They differ
  whenever time is simulated or events are logged late, and mixing them up
  makes a night operation look like it happened at lunch.
- **Configuration from the environment.** Paths and the terminal level come
  from environment variables, read and checked once by `Settings.from_env()`
  into a frozen dataclass that is passed on (the twelve-factor "config in the
  environment" rule plus dependency injection). A wrong value fails at start
  with a clear message. Business rules - thresholds, the night window,
  rates - are not settings: they are the bank's policy and stay constructor
  arguments. The standard library is enough for three variables; a library
  such as `pydantic-settings` pays off with nested configuration, `.env`
  files or an HTTP layer.

## Audit logging

- **Severity levels.** `INFO` - normal business events; `WARNING` -
  something unusual that did not stop the operation; `ERROR` - an operation
  failed; `CRITICAL` - the system protected itself (a client blocked, an
  operation refused by risk control) or a defect. `AuditLevel` reuses the
  numbers of Python's `logging` levels, and as an `IntEnum` it compares by
  value, so "warnings and above" is `level >= WARNING`.
- **Append-only and immutable.** An audit trail is evidence: entries are
  never edited or deleted. Events are frozen dataclasses with read-only
  `details`, the getters return copies, and the file is only appended to.
- **Write-through to a file.** Each event is written the moment it is
  recorded, so a crash loses nothing that was already logged. Memory is for
  fast queries in the running process; the file is the durable record.
- **One journal, many writers.** Security, the bank (client and account
  life cycle), the queue, the processor and risk control share one injected
  `AuditLog`, so a client's whole story is in one place, in time order.
  `suspicious_activities` is a filtered view of the same journal, so the
  security side needs no store of its own.
- **What is worth auditing.** Changes of state that someone may have to
  answer for: an account opened, frozen or closed, a client unblocked, a
  transaction accepted, executed, refused or cancelled. A change is recorded
  after it is made, so the journal never claims what did not happen; a
  refused attempt is recorded as a security event instead. The price is the
  opposite gap: if the write fails, the change is already made (an account
  closed, a client registered) and the caller gets the error, with nothing
  to undo it. Here the error at least stops further work, and money
  movements are in the history before the journal is written; a real system
  closes the gap by storing the change and its event in one database
  transaction (the transactional outbox pattern). Event names are enums
  (`AccountEvent`, `ClientEvent`, `TransactionEvent`, `RiskEvent`), so
  writers and reports cannot drift apart on a typo.
- **Audit log vs application log.** The application log (`logging`) is
  for developers and can be sampled or rotated away; the audit log is a
  business record of who did what and when, kept complete. They also fail
  differently: `logging` swallows a handler error (it prints it and goes
  on), which is right for diagnostics and wrong for evidence, so the audit
  log writes its own file and a failed write stops the operation. The two
  are joined in one direction: every recorded audit event is copied to the
  `bank.audit` logger, so the application log shows business events in
  order with the technical ones, and one source feeds both.

## Risk analysis

- **Rule-based scoring.** Each rule checks one signal and adds points;
  the sum maps to a level through thresholds. It is transparent (the
  factors explain every decision), easy to tune and needs no training data.
  The weights are chosen so that one signal alone is at most `medium`,
  while a combination (a large amount + a new recipient + night) is
  `high`, and a very large amount (2 000 000 RUB and more) is `high` on
  its own.
- **Open/Closed principle.** A new check is a new `RiskRule` subclass
  passed to `RiskAnalyzer`; the analyzer itself is not changed.
- **Record vs block.** Hard rules (the night ban, a blocked client) refuse
  an operation outright and run first. The risk score is softer: `low`
  goes through, `medium` goes through but is logged as a warning for a
  review, `high` is refused. A blocked transaction fails without retry:
  trying it again would get the same score.
- **State for behavioural rules.** Frequency and "new recipient" depend on
  history, so the analyzer keeps a small `RiskHistory`: when each
  transaction was first seen (a retry does not count as a new operation)
  and which sender -> recipient pairs already completed a transfer. The
  bank remembers when each account was opened, so the models stay free of
  clocks.
- **Rules vs machine learning.** Real anti-fraud systems combine rules
  with ML models trained on labelled fraud (gradient boosting, anomaly
  detection). Rules stay for regulatory limits and explainability; a model
  catches patterns nobody wrote a rule for. Here the ML part would simply be
  another `RiskRule` that returns a model's score.

## Reporting

- **Reports are read-only views.** `BankReport` and `AuditReport` keep no
  state of their own: every call reads the bank, the history and the audit
  log as they are now, so a report can never drift from the data. This is
  the read side of command/query separation: operations change the state,
  reports only query it.
- **Data first, format second.** A report returns an immutable dataclass
  (`TransactionStatistics`, `ClientRanking`, `BalanceSummary`) and `str()`
  is just one way to show it. The same object can be exported to JSON or
  CSV or drawn as a chart without computing anything again (Single
  Responsibility: computing and presenting are separate jobs). `frozen=True`
  alone would still let the dicts inside change, so they are wrapped in
  read-only `MappingProxyType` views.
- **One currency for totals.** Amounts in different currencies cannot be
  added, so every total is converted into the base currency at the bank's
  rates; a tariff fee is converted from the sender's currency, in which it
  was charged.
- **Each fact from its source.** Finished transactions come from the
  history, cancellations from the audit log (a cancelled transaction never
  ran, so the history does not have it), blocked ones from the risk
  analyzer. Nothing is counted twice.
- **Compute in services, lay out in `reporting`.** `ReportBuilder` only
  picks numbers from `BankReport`, `AuditReport` and the history and puts
  them into a `Report`: named values (`KeyValueSection`) and tables
  (`TableSection`). Dependencies go one way: reporting -> services -> models.
- **Facade, not a GoF Builder.** `ReportBuilder` gives one entry point with
  a factory method per report (`client_report()`, `bank_report()`,
  `risk_report()`) and one method per output (`export_to_json()`,
  `export_to_csv()`, `save_charts()`). A GoF Builder assembles one complex
  object step by step (`.add_section().add_chart().build()`); here the
  caller should not know the steps at all.
- **Strategy for formats.** `ReportExporter` has one method, `render(report)`;
  `TextExporter`, `JsonExporter` and `CsvExporter` implement it. An exporter
  knows the report model, never a particular report, so a new report needs
  no new export code and a new format touches no report (Open/Closed).
- **Charts are data, drawing is separate.** A report holds `PieChart`,
  `BarChart` and `LineChart` objects (labels and values); `ChartRenderer`
  turns them into PNG. Tests check what a chart shows without drawing it,
  and a different renderer could replace matplotlib.
- **Money stays exact in files.** JSON has no decimal type, and a float
  would turn `0.1 + 0.2` into `0.30000000000000004`, so a `Decimal` is
  written as a string (`"4322411.00"`); dates are ISO 8601, enums their
  value. Money becomes `float` only to place a mark on a chart.
- **CSV is one table per file.** Sections have different columns, and a
  CSV file has one header, so each section gets its own file
  (`..._bank_top_clients.csv`); any spreadsheet or `pandas.read_csv` opens it
  as is. Text from users that starts with `=`, `+`, `-` or `@` gets a
  leading `'`: otherwise a spreadsheet runs a client named `=HYPERLINK(...)`
  as a formula (CSV injection). Numbers are not escaped, `-1511.00` stays a
  number.
- **matplotlib without pyplot.** `pyplot` keeps global state (the current
  figure) and may open a window; `matplotlib.figure.Figure` is a plain
  object, so a chart is built and saved by ordinary code: no hidden current
  figure, no window, no `plt.close()` to forget.
- **Only direct dependencies are declared.** `requirements.txt` lists
  matplotlib, not numpy: the project never imports numpy, it comes with
  matplotlib, and declaring it would pin a version the project does not
  need.
- **Files never overwrite each other.** A file name holds the time of the
  call and the report kind (`2026-09-26_14-30-05_bank.json`); a taken name
  gets `-2`, and files are opened in mode `"x"`, which fails instead of
  overwriting. Checking a name and then creating the file is a race
  (another process can come in between), so the check alone is not
  trusted: when `"x"` fails midway, the files already written are removed
  and the whole set moves to the next name.

## Preparing modules for unit testing

Designing code so that each unit can be verified in isolation: small pure
functions, explicit dependencies, deterministic behaviour and precise
exceptions.

*In the project:* `to_money()` and `to_rate()` are pure functions; accounts take
their collaborators (`Client`, currency, status, limits, rates) through the
constructor; `Portfolio` is tested on its own without any account; there is no
clock inside the models, so `apply_monthly_interest()` is called explicitly and
tests stay deterministic; `Client` takes an optional `today` for the age check,
so the 18th-birthday boundary is tested on fixed dates. Services receive their
dependencies: tests build `SecurityGuard(clock=ManualClock(...))` and move the
clock to 00:00, 04:59:59 or 05:00 to check the night window exactly, and pass
their own rates to `CurrencyConverter`. Tests are split into `tests/unit/`
(one module per model, service or helper) and `tests/integration/` (cross-type,
bank, transaction and risk scenarios and smoke tests of both programs). The audit
file is tested in pytest's `tmp_path`, and the analyzer is tested apart from
the real rules with a stub rule that always returns a fixed score.
