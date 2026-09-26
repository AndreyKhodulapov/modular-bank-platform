import logging
from pathlib import Path

import pytest

from exceptions import InvalidOperationError
from settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_LOGS = PROJECT_ROOT / "logs"


def test_defaults_point_to_the_project_folders():
    settings = Settings.from_env({})
    assert settings.audit_log_path == PROJECT_LOGS / "audit.jsonl"
    assert settings.log_file_path == PROJECT_LOGS / "app.jsonl"
    assert settings.console_log_level == logging.WARNING
    assert settings.reports_dir == PROJECT_ROOT / "reports"


def test_environment_overrides_the_defaults():
    settings = Settings.from_env(
        {
            "BANK_AUDIT_LOG": "~/bank/audit.jsonl",
            "BANK_LOG_FILE": " /tmp/app.jsonl ",
            "BANK_LOG_LEVEL": "info",
            "BANK_REPORTS_DIR": "~/bank/reports",
        }
    )
    assert settings.audit_log_path == Path.home() / "bank" / "audit.jsonl"
    assert settings.log_file_path == Path("/tmp/app.jsonl")
    assert settings.console_log_level == logging.INFO
    assert settings.reports_dir == Path.home() / "bank" / "reports"


def test_blank_variables_count_as_not_set():
    settings = Settings.from_env({"BANK_AUDIT_LOG": "  ", "BANK_LOG_LEVEL": ""})
    assert (settings.audit_log_path, settings.console_log_level) == (PROJECT_LOGS / "audit.jsonl", logging.WARNING)


def test_unknown_log_level_is_refused_at_start():
    with pytest.raises(InvalidOperationError, match="BANK_LOG_LEVEL must be one of debug, info"):
        Settings.from_env({"BANK_LOG_LEVEL": "verbose"})
