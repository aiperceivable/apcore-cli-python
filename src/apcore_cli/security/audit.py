"""Audit logging in JSON Lines format (FE-05)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

logger = logging.getLogger("apcore_cli.security")


class AuditLogger:
    DEFAULT_PATH = Path.home() / ".apcore-cli" / "audit.jsonl"

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or self.DEFAULT_PATH
        self._ensure_directory()

    def _ensure_directory(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def log_execution(
        self,
        module_id: str,
        input_data: dict,
        status: Literal["success", "error"],
        exit_code: int,
        duration_ms: int,
    ) -> None:
        entry = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "user": self._get_user(),
            "module_id": module_id,
            "input_hash": self._hash_input(input_data),
            "status": status,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
        }
        try:
            with open(self._path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as e:
            logger.warning("Could not write audit log: %s", e)

    def _hash_input(self, input_data: dict) -> str:
        """Hash input with a random salt to prevent correlation across invocations."""
        salt = secrets.token_bytes(16)
        payload = json.dumps(input_data, sort_keys=True).encode()
        return hashlib.sha256(salt + payload).hexdigest()

    def _get_user(self) -> str:
        try:
            return os.getlogin()
        except OSError:
            pass
        try:
            import pwd

            return pwd.getpwuid(os.getuid()).pw_name
        except (ImportError, KeyError, AttributeError):
            pass
        return os.getenv("USER", os.getenv("USERNAME", "unknown"))
