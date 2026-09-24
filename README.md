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
  `get_clients_ranking()` in roubles.
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
| Suspicious activity log | failed logins, blocking, attempts by a blocked client or for an unknown id, night attempts, operations on frozen or closed accounts, amounts of 500 000 RUB and more |

Known limitations:

- `invest()`, `divest()` and `apply_monthly_interest()` are not part of
  `Bank` and are called on the account itself, so the night window,
  blocking and the suspicious activity log do not cover them. The bank demo
  invests on Oleg's account this way.

## Project structure

```
modular-bank-platform/
├── README.md
├── pyproject.toml          # pytest and ruff configuration
├── requirements.txt        # runtime dependencies (none, stdlib only)
├── requirements-dev.txt    # pytest, ruff
├── src/
│   ├── main.py             # demonstration script, one function per stage
│   ├── exceptions.py       # custom exception hierarchy
│   ├── utils.py            # value normalisation helpers, ManualClock
│   ├── services/
│   │   ├── bank.py         # Bank (facade over clients, accounts, security)
│   │   ├── security.py     # SecurityGuard, SuspiciousActivity, SuspicionReason
│   │   └── currency.py     # CurrencyConverter, reference rates to RUB
│   └── models/
│       ├── account.py             # AbstractAccount, BankAccount
│       ├── savings_account.py     # SavingsAccount
│       ├── premium_account.py     # PremiumAccount
│       ├── investment_account.py  # InvestmentAccount
│       ├── portfolio.py           # Portfolio
│       ├── enums.py               # AccountStatus, ClientStatus, Currency, AssetType
│       └── client.py              # Client
├── tests/
│   ├── conftest.py         # shared fixtures
│   ├── unit/               # one module per model, service or helper
│   └── integration/        # account and bank scenarios, demo smoke test
└── docs/
    └── oop_principles.md   # interview-style notes on OOP, patterns, security
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

A final summary treats all created accounts through the common interface.

## Run the tests and linter

```bash
pytest            # unit + integration tests
ruff check .      # PEP 8 / import order / modern syntax checks
ruff format .     # auto-format
```
