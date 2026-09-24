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
after creation.

## Inheritance

A class reuses and extends the state and behaviour of a parent class,
forming an "is-a" relationship.

*In the project:* `BankAccount` inherits the common state and `__repr__` from
`AbstractAccount`; every custom error inherits from `BankError`, so one
`except BankError` clause catches the whole family.

## Polymorphism

Different classes respond to the same message (method call) in their own
way, so client code can work with the abstract interface instead of concrete
types.

*In the project:* any code that accepts `AbstractAccount` can call
`deposit()`, `withdraw()` and `get_account_info()` without knowing which
concrete account type it holds. Future account types (savings, credit) will
plug into the same interface.

## Abstraction

Exposing only the essential operations of a concept and hiding the details
of how they work. Abstract classes describe *what* must exist, not *how*.

*In the project:* `AbstractAccount(ABC)` declares the three abstract methods
and cannot be instantiated; `BankAccount` provides the implementation.

## SOLID

- **S - Single Responsibility:** each module owns one concern -
  `exceptions.py` (error types), `utils.py` (money normalisation),
  `owner.py` (owner data), `account.py` (account behaviour).
- **O - Open/Closed:** new account types extend `AbstractAccount` without
  modifying existing code; new currencies are added to the `Currency` enum.
- **L - Liskov Substitution:** `BankAccount` can be used anywhere an
  `AbstractAccount` is expected; it never weakens the base contract.
- **I - Interface Segregation:** the abstract interface is minimal (three
  methods); nothing forces subclasses to implement operations they do not need.
- **D - Dependency Inversion:** high-level modules depend on abstractions,
  not on concrete implementations. Not applied in the project yet: `BankAccount`
  depends on the concrete `Owner` class and `main.py` instantiates `BankAccount`
  directly.

## Domain modelling

Representing business concepts as explicit types with their own invariants,
instead of passing raw primitives around.

*In the project:* `AccountStatus` and `Currency` are enums rather than free
strings; `Owner` is a dedicated type with validation; money is `Decimal`
normalised by `to_money()` - floats are converted through `str()` to avoid
binary representation artefacts, and every value is rounded half-up to two
decimal places.

## Structured logging

Emitting log records as key-value data (not free text) so they can be
filtered, aggregated and shipped to monitoring systems.

*In the project:* not implemented yet; it will arrive together with the
audit and transaction features.

## Preparing modules for unit testing

Designing code so that each unit can be verified in isolation: small pure
functions, explicit dependencies, deterministic behaviour and precise
exceptions.

*In the project:* `to_money()` is a pure function; `BankAccount` takes its
collaborators (`Owner`, currency, status) through the constructor; errors
carry structured data (`requested`, `available`, `account_id`) that tests
assert on. Tests are split into `tests/unit/` (models and helpers in isolation)
and `tests/integration/` (end-to-end scenarios and a smoke test of the demo).
