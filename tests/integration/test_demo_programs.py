"""Smoke tests of the programs in ``src/``: each one runs as a script and prints what it should."""

import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def run_program(script: str, tmp_path: Path, log_level: str = "warning") -> subprocess.CompletedProcess[str]:
    """Run ``src/<script>`` with both logs and the reports in ``tmp_path``, out of the project's logs/ and reports/."""
    root = Path(__file__).resolve().parents[2]
    return subprocess.run(
        [sys.executable, str(root / "src" / script)],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ
        | {
            "BANK_AUDIT_LOG": str(tmp_path / "audit.jsonl"),
            "BANK_LOG_FILE": str(tmp_path / "app.jsonl"),
            "BANK_LOG_LEVEL": log_level,
            "BANK_REPORTS_DIR": str(tmp_path / "reports"),
        },
    )


def read_json_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_legacy_demo_runs_without_errors(tmp_path):
    audit_path, app_log_path = tmp_path / "audit.jsonl", tmp_path / "app.jsonl"
    completed = run_program("legacy_demo.py", tmp_path)
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
    events = read_json_lines(audit_path)
    assert f"this run added {len(events)} events" in completed.stdout
    assert sum(event["event"] == "operation_blocked" for event in events) == 2
    assert {"client_registered", "account_opened", "transaction_queued"} <= {event["event"] for event in events}
    # the terminal shows warnings and above; the file has everything, the audit events of every stage included
    assert "CRITICAL bank.audit        operation_blocked: external_transfer of 25000.00 USD" in completed.stdout
    assert "INFO     bank." not in completed.stdout
    records = read_json_lines(app_log_path)
    assert {record["logger"] for record in records} == {"bank.audit", "bank.transactions", "bank.queue"}
    assert {record["level"] for record in records} == {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    assert sum(record.get("event") == "operation_blocked" for record in records) == 2


@pytest.fixture(scope="module")
def main_run(tmp_path_factory) -> tuple[subprocess.CompletedProcess[str], Path]:
    """One run of the program shared by the tests that read its output, its logs and its reports."""
    logs = tmp_path_factory.mktemp("main")
    completed = run_program("main.py", logs)
    assert completed.returncode == 0, completed.stderr
    return completed, logs


def test_main_program_plays_the_day_and_prints_the_reports(main_run):
    output = main_run[0].stdout
    for section in (
        "1. Initialization",
        "2. Simulation",
        "3. Logging",
        "4. Client view: Sokolov Oleg",
        "5. Reports",
        "6. Export",
    ):
        assert section in output
    # the sizes the program's docstring promises
    assert "7 clients, 12 accounts" in output
    assert "40 transactions queued" in output
    # the feed shows every outcome, read from the audit log
    assert "queued     #01 salary Maria" in output
    assert "completed  #01 salary Maria" in output
    assert "cancelled  #20 typo" in output
    assert "failed     #14 to frozen            attempt 1: AccountFrozenError" in output
    assert "failed     #16 blocked client       attempt 1: ClientBlockedError" in output
    assert "retry      #31 night                attempt 1: OperationTimeRestrictedError" in output
    assert "completed  #31 night" in output
    assert "warning    #21 large                transfer of 7000.00 USD: medium risk, score 60" in output
    assert "blocked    #22 huge abroad          external_transfer of 25000.00 USD: high risk, score 90" in output
    assert "#19 short of money: failed" in output
    # the client view and the reports
    assert "PremiumAccount | Sokolov Oleg | ****" in output
    assert "| active | -1511.00 USD" in output
    bank_report = output.split("= 5. Reports =")[1].split("  Risk report")[0]
    assert "open_accounts           11" in bank_report  # the closed CNY account is not counted
    assert "total_balance           4322411.00" in bank_report
    assert "transactions            40" in bank_report
    assert "failure_rate_percent    20.5" in bank_report
    assert "tariff_fees             450.00" in bank_report
    first_place = bank_report.split("Top 3 clients\n")[1].splitlines()[1]  # the line after the column names
    assert first_place.split()[0] == "1"
    assert "Sokolov Oleg" in first_place
    assert "CNY" not in bank_report
    # the balance history starts at the beginning of the day, before the salaries
    assert "2026-09-24 00:00     3940000.00\n    2026-09-24 09:00     4352971.00" in bank_report
    risk_report = output.split("  Risk report")[1].split("= 6. Export =")[0]
    assert "suspicious            7" in risk_report
    assert "blocked_by_risk       2" in risk_report
    # the terminal shows warnings and above only
    assert "CRITICAL bank.audit        operation_blocked" in output
    assert "INFO     bank." not in output


def test_main_program_writes_both_logs(main_run):
    completed, logs = main_run
    events = read_json_lines(logs / "audit.jsonl")
    assert f"The audit log holds {len(events)} events of this run" in completed.stdout
    names = [event["event"] for event in events]
    assert names.count("transaction_queued") >= 40  # a retry comes back through the queue
    assert names.count("transaction_cancelled") == 1
    assert names.count("operation_blocked") == 2
    assert {"client_registered", "account_opened", "account_frozen", "account_closed", "client_blocked"} <= set(names)
    records = read_json_lines(logs / "app.jsonl")
    assert sum(record.get("event") == "transaction_completed" for record in records) == 31


def test_main_program_exports_the_reports_and_charts(main_run):
    completed, logs = main_run
    folder = logs / "reports"
    names = sorted(path.name for path in folder.iterdir())
    assert f"Folder: {folder}" in completed.stdout
    for name in names:
        assert f"    {name}\n" in completed.stdout
    # every file of the run shares one time stamp: <date>_<time>_<kind>[_<part>].<ext>
    stamps = {name[:19] for name in names}
    assert len(stamps) == 1
    parts = [name[20:] for name in names]
    for kind, count in (("client", 10), ("bank", 13), ("risk", 12)):
        assert f"({count} files)" in completed.stdout
        assert sum(part.startswith(f"{kind}.") or part.startswith(f"{kind}_") for part in parts) == count
        assert {f"{kind}.txt", f"{kind}.json", f"{kind}_summary.csv"} <= set(parts)
    assert {"client_balance.png", "bank_total_balance.png", "bank_top_clients.png", "risk_risk_factors.png"} <= set(
        parts
    )
    for name in names:
        if name.endswith(".png"):
            assert (folder / name).read_bytes().startswith(b"\x89PNG")

    stamp = stamps.pop()
    bank = json.loads((folder / f"{stamp}_bank.json").read_text(encoding="utf-8"))
    assert bank["sections"]["summary"]["total_balance"] == "4322411.00"
    assert [row["currency"] for row in bank["sections"]["balance_by_currency"]] == ["EUR", "KZT", "RUB", "USD"]
    client = json.loads((folder / f"{stamp}_client.json").read_text(encoding="utf-8"))
    assert client["title"] == "Client report: Sokolov Oleg"
    assert client["sections"]["summary"]["period_start"] == "2026-09-24T00:00:00"
    with (folder / f"{stamp}_risk_suspicious_operations.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 7
    assert sum(row["action"] == "blocked" for row in rows) == 2


def test_main_program_reports_invalid_settings_in_one_line(tmp_path):
    completed = run_program("main.py", tmp_path, log_level="loud")
    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr.splitlines() == [
        "Invalid settings: BANK_LOG_LEVEL must be one of debug, info, warning, error, critical; got 'loud'."
    ]
