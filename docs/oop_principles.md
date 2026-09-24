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
free cash on an investment account. The demo's summary and the integration
test iterate over a `list[AbstractAccount]` without checking concrete types.
Subclasses extend rather than replace behaviour: `get_account_info()` and
`__str__()` call `super()` and append their own fields.

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
conversion to them. `Client` is deliberately not composed of a separate
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
  `CurrencyConverter` owns exchange rates.
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

*In the project:* `AccountStatus`, `ClientStatus`, `Currency`, `AssetType` and
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
  creating them. `SecurityGuard(clock=...)` and
  `Bank(security=..., converter=...)`; the defaults (`datetime.now`, reference
  rates) are used only when nothing is passed.
- **Value Object** - an immutable object defined by its values, not identity.
  `SuspiciousActivity` (a frozen dataclass), money as `Decimal` and the enum
  members.
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

## Structured logging

Emitting log records as key-value data (not free text) so they can be
filtered, aggregated and shipped to monitoring systems.

*In the project:* the suspicious activity log already stores structured
records - `SuspiciousActivity` has a timestamp, a `SuspicionReason` enum and
the client and account ids - so it can be filtered by field. The records are
kept in memory and are not yet sent through the `logging` module.

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
(one module per model, service or helper) and `tests/integration/` (cross-type
and bank scenarios and a smoke test of the demo).
