"""Smoke tests of the programs in ``src/``: each one runs as a script and prints what it should."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_legacy_demo_runs_without_errors(tmp_path):
    root = Path(__file__).resolve().parents[2]
    # both files go to a temporary folder, so the test run stays out of the project's logs/
    audit_path, app_log_path = tmp_path / "audit.jsonl", tmp_path / "app.jsonl"
    completed = subprocess.run(
        [sys.executable, str(root / "src" / "legacy_demo.py")],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ
        | {"BANK_AUDIT_LOG": str(audit_path), "BANK_LOG_FILE": str(app_log_path), "BANK_LOG_LEVEL": "warning"},
    )
    assert completed.returncode == 0, completed.stderr
    assert "STAGE 1: Accounts Basic" in completed.stdout
    assert "STAGE 2: Accounts Advanced" in completed.stdout
    assert "[rejected] deposit 100 USD: AccountFrozenError" in completed.stdout
    assert "[ok]       withdraw 300 RUB" in completed.stdout
    assert "[ok]       apply monthly interest: interest 150.00" in completed.stdout
    assert "[ok]       withdraw 2_500 USD (goes into overdraft): balance -1505.00" in completed.stdout
    assert "project yearly growth (stocks 10%, bonds 4%, etf 7%): growth 500.00" in completed.stdout
    assert "STAGE 3: Bank System" in completed.stdout
    assert "[rejected] register a 16-year-old client: InvalidOperationError" in completed.stdout
    assert "[rejected] Oleg, wrong password #3: ClientBlockedError" in completed.stdout
    assert "[rejected] withdraw 5_000 KZT: OperationTimeRestrictedError" in completed.stdout
    assert "(min_balance does not hold money back): payout 300000.00" in completed.stdout
    assert "total balance: 1502100.00 RUB" in completed.stdout
    assert "STAGE 4: Transactions" in completed.stdout
    assert "[ok]       cancel 'typo': status cancelled" in completed.stdout
    assert "[failed] to frozen: AccountFrozenError" in completed.stdout
    assert "night      completed attempts 3" in completed.stdout
    assert "fees collected: 180.00 RUB" in completed.stdout
    assert "STAGE 5: Audit and Risk" in completed.stdout
    assert f"file: {audit_path}" in completed.stdout
    assert "[failed   ] huge abroad      high   score  90" in completed.stdout
    events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert f"this run added {len(events)} events" in completed.stdout
    assert sum(event["event"] == "operation_blocked" for event in events) == 2
    assert {"client_registered", "account_opened", "transaction_queued"} <= {event["event"] for event in events}
    # the terminal shows warnings and above; the file has everything, the audit events of every stage included
    assert "CRITICAL bank.audit        operation_blocked: external_transfer of 25000.00 USD" in completed.stdout
    assert "INFO     bank." not in completed.stdout
    records = [json.loads(line) for line in app_log_path.read_text(encoding="utf-8").splitlines()]
    assert {record["logger"] for record in records} == {"bank.audit", "bank.transactions", "bank.queue"}
    assert {record["level"] for record in records} == {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    assert sum(record.get("event") == "operation_blocked" for record in records) == 2
