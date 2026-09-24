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
business rule. `Owner` is a frozen dataclass, so its data cannot be altered
after creation. `Portfolio.holdings` returns a copy, so the allocation can only
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
`BankAccount._prepare_withdrawal()` is a template step: it runs the checks
shared by every withdrawal (status, amount, limit) so that subclasses only
implement the part that differs - how much money is actually available.

## SOLID

- **S - Single Responsibility:** each module owns one concern -
  `exceptions.py` (error types), `utils.py` (money and rate normalisation),
  `owner.py` (owner data), one module per account type, `portfolio.py`
  (asset allocation and growth projection, no knowledge of cash or accounts).
- **O - Open/Closed:** a new account type extends `BankAccount` and overrides
  `withdraw()`, `get_account_info()` and `__str__()`; the shared checks in
  `_prepare_withdrawal()` and the limit constants are reused, not modified.
  New asset types are added to the `AssetType` enum, new currencies to
  `Currency`.
- **L - Liskov Substitution:** every subclass can be used wherever an
  `AbstractAccount` is expected. Subclasses apply their own funds rules, but the
  base contract - "either debit the balance and return it, or raise a
  `BankError`" - is never broken, and the shared checks always run first and
  in the same order.
- **I - Interface Segregation:** the abstract interface is minimal (three
  methods); type-specific operations (`apply_monthly_interest`, `invest`,
  `divest`, `project_yearly_growth`) live only on the classes that need them.
- **D - Dependency Inversion:** high-level modules depend on abstractions,
  not on concrete implementations. Not applied in the project yet: `BankAccount`
  depends on the concrete `Owner` class and `main.py` instantiates the concrete
  account classes directly.

## Domain modelling

Representing business concepts as explicit types with their own invariants,
instead of passing raw primitives around.

*In the project:* `AccountStatus`, `Currency` and `AssetType` are enums rather
than free strings; `Owner` and `Portfolio` are dedicated types with validation;
money is `Decimal` normalised by `to_money()` (floats converted through
`str()` to avoid binary representation artefacts, rounded half-up to two
decimal places), while rates go through `to_rate()` and keep their precision
because `0.005` is a legitimate monthly rate. Errors carry structured data
(`requested`, `available`, `limit`, `account_id`) instead of only text.

## Structured logging

Emitting log records as key-value data (not free text) so they can be
filtered, aggregated and shipped to monitoring systems.

*In the project:* not implemented yet; it will arrive together with the
audit and transaction features.

## Preparing modules for unit testing

Designing code so that each unit can be verified in isolation: small pure
functions, explicit dependencies, deterministic behaviour and precise
exceptions.

*In the project:* `to_money()` and `to_rate()` are pure functions; accounts take
their collaborators (`Owner`, currency, status, limits, rates) through the
constructor; `Portfolio` is tested on its own without any account; there is no
clock inside the models, so `apply_monthly_interest()` is called explicitly and
tests stay deterministic. Tests are split into `tests/unit/` (one module per
model or helper) and `tests/integration/` (cross-type scenarios and a smoke
test of the demo).
