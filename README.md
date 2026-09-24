# Modular Bank Platform

An object-oriented prototype of a modular banking platform. All data lives in memory; there is
no database, no external API and no third-party runtime dependency.

## Current scope

- `AbstractAccount` – abstract base with a unique id, owner, protected balance,
  status and the abstract operations `deposit`, `withdraw`, `get_account_info`.
- `BankAccount` – concrete account with input validation, status enforcement,
  automatic UUID4 generation and a `currency` attribute (RUB, USD, EUR, KZT, CNY).
- `Owner` – immutable, validated owner data with `full_name` and `to_dict()`.
- Domain exceptions: `AccountFrozenError`, `AccountClosedError`,
  `InvalidOperationError`, `InsufficientFundsError` (all derive from `BankError`).
- Money is handled as `decimal.Decimal` rounded half-up to two decimal places.

## Project structure

```
modular-bank-platform/
├── README.md
├── pyproject.toml          # pytest and ruff configuration
├── requirements.txt        # runtime dependencies (none, stdlib only)
├── requirements-dev.txt    # pytest, ruff
├── src/
│   ├── main.py             # demonstration script
│   ├── exceptions.py       # custom exception hierarchy
│   ├── utils.py            # money conversion helper
│   └── models/
│       ├── account.py      # AbstractAccount, BankAccount
│       ├── enums.py        # AccountStatus, Currency
│       └── owner.py        # Owner
├── tests/
│   ├── conftest.py         # shared fixtures
│   ├── unit/               # models and helpers in isolation
│   └── integration/        # end-to-end scenarios, demo script smoke test
└── docs/
    └── oop_principles.md   # interview-style notes on the OOP concepts used
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

The script creates an active and a frozen account, shows that operations on
the frozen account are rejected, performs a valid deposit and withdrawal, and
prints the account summary.

## Run the tests and linter

```bash
pytest            # unit + integration tests
ruff check .      # PEP 8 / import order / modern syntax checks
ruff format .     # auto-format
```
