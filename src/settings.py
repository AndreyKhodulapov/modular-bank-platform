"""Run-time settings read from environment variables.

Only what depends on where and how the program runs lives here: file paths
and the log level. Business rules - thresholds, the night window, rates,
retries - are the bank's policy and stay constructor arguments.

- ``BANK_AUDIT_LOG`` - the audit log file, ``logs/audit.jsonl`` by default;
- ``BANK_LOG_FILE`` - the application log file, ``logs/app.jsonl`` by default;
- ``BANK_LOG_LEVEL`` - the lowest level shown in the terminal, ``WARNING``
  by default; the file always gets everything from ``DEBUG`` up.

The default paths are in the project root, whatever the current directory.
A variable that is set but blank counts as not set.
"""

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from exceptions import InvalidOperationError


@dataclass(frozen=True)
class Settings:
    audit_log_path: Path
    log_file_path: Path
    console_log_level: int

    LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        """Read the settings from ``environ`` (the process environment by default) and check them."""
        env = os.environ if environ is None else environ
        logs = Path(__file__).resolve().parent.parent / "logs"
        return cls(
            audit_log_path=cls._path(env, "BANK_AUDIT_LOG", logs / "audit.jsonl"),
            log_file_path=cls._path(env, "BANK_LOG_FILE", logs / "app.jsonl"),
            console_log_level=cls._level(env, "BANK_LOG_LEVEL", "WARNING"),
        )

    @staticmethod
    def _path(env: Mapping[str, str], name: str, default: Path) -> Path:
        value = env.get(name, "").strip()
        return Path(value).expanduser() if value else default

    @classmethod
    def _level(cls, env: Mapping[str, str], name: str, default: str) -> int:
        value = env.get(name, "").strip().upper() or default
        if value not in cls.LOG_LEVELS:
            allowed = ", ".join(level.lower() for level in cls.LOG_LEVELS)
            raise InvalidOperationError(f"{name} must be one of {allowed}; got {env[name]!r}.")
        return logging.getLevelNamesMapping()[value]
