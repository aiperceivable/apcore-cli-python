"""Tests for AuditLogger (FE-05)."""

import json
import logging
import os
from pathlib import Path
from unittest.mock import patch

from apcore_cli.security.audit import AuditLogger


class TestAuditLogger:
    def test_log_execution_success(self, tmp_path):
        log_path = tmp_path / "audit.jsonl"
        logger = AuditLogger(path=log_path)
        logger.log_execution("math.add", {"a": 5}, "success", 0, 42)
        lines = log_path.read_text().strip().split("\n")
        entry = json.loads(lines[0])
        assert entry["module_id"] == "math.add"
        assert entry["status"] == "success"
        assert entry["exit_code"] == 0
        assert entry["duration_ms"] == 42

    def test_log_execution_error(self, tmp_path):
        log_path = tmp_path / "audit.jsonl"
        logger = AuditLogger(path=log_path)
        logger.log_execution("math.add", {}, "error", 1, 100)
        entry = json.loads(log_path.read_text().strip())
        assert entry["status"] == "error"
        assert entry["exit_code"] == 1

    def test_log_creates_directory(self, tmp_path):
        log_path = tmp_path / "subdir" / "deep" / "audit.jsonl"
        AuditLogger(path=log_path)
        assert log_path.parent.exists()

    def test_log_input_hash(self, tmp_path):
        log_path = tmp_path / "audit.jsonl"
        logger = AuditLogger(path=log_path)
        logger.log_execution("mod", {"a": 1}, "success", 0, 10)
        logger.log_execution("mod", {"a": 1}, "success", 0, 10)
        lines = log_path.read_text().strip().split("\n")
        hash1 = json.loads(lines[0])["input_hash"]
        hash2 = json.loads(lines[1])["input_hash"]
        # Each invocation uses a random salt — same input must NOT produce the same hash
        assert hash1 != hash2
        # Hash must be a valid SHA-256 hex digest (64 chars)
        assert len(hash1) == 64
        assert all(c in "0123456789abcdef" for c in hash1)

    def test_log_write_failure_warns(self, tmp_path, caplog):
        log_path = Path("/nonexistent/readonly/audit.jsonl")
        logger = AuditLogger.__new__(AuditLogger)
        logger._path = log_path
        logger._write_failure_warned = False
        with caplog.at_level(logging.WARNING, logger="apcore_cli.security"):
            logger.log_execution("mod", {}, "success", 0, 10)
        assert "Could not write audit log" in caplog.text

    def test_log_write_failure_warns_only_once(self, tmp_path, caplog):
        """D11-010: write-failure warning is deduplicated per logger instance,
        matching TS ``writeFailureWarned`` flag for cross-SDK parity."""
        log_path = Path("/nonexistent/readonly/audit.jsonl")
        logger = AuditLogger.__new__(AuditLogger)
        logger._path = log_path
        logger._write_failure_warned = False
        with caplog.at_level(logging.WARNING, logger="apcore_cli.security"):
            logger.log_execution("mod", {}, "success", 0, 10)
            logger.log_execution("mod", {}, "success", 0, 10)
            logger.log_execution("mod", {}, "success", 0, 10)
        write_failure_warnings = [
            rec for rec in caplog.records if "Could not write audit log" in rec.getMessage()
        ]
        assert len(write_failure_warnings) == 1, (
            f"expected exactly 1 write-failure warning, got {len(write_failure_warnings)}"
        )

    def test_get_user_fallback_pwd(self, monkeypatch):
        # When getlogin() fails, should fall back to pwd.getpwuid()
        import pwd as _pwd

        audit_logger = AuditLogger.__new__(AuditLogger)
        with patch("os.getlogin", side_effect=OSError):
            result = audit_logger._get_user()
        # pwd.getpwuid should return the real username on Unix
        expected = _pwd.getpwuid(os.getuid()).pw_name
        assert result == expected

    def test_get_user_fallback_env(self, monkeypatch):
        # When both getlogin() and pwd fail, fall back to USER env var
        import pwd as _pwd

        monkeypatch.setenv("USER", "testuser")
        audit_logger = AuditLogger.__new__(AuditLogger)
        with (
            patch("os.getlogin", side_effect=OSError),
            patch.object(_pwd, "getpwuid", side_effect=KeyError),
        ):
            result = audit_logger._get_user()
        assert result == "testuser"

    def test_get_user_fallback_logname(self, monkeypatch):
        """D11-008: LOGNAME is the third env-var fallback (USER -> LOGNAME ->
        USERNAME -> 'unknown'), per security.md canonical chain."""
        import pwd as _pwd

        monkeypatch.delenv("USER", raising=False)
        monkeypatch.delenv("USERNAME", raising=False)
        monkeypatch.setenv("LOGNAME", "ci-runner")
        audit_logger = AuditLogger.__new__(AuditLogger)
        with (
            patch("os.getlogin", side_effect=OSError),
            patch.object(_pwd, "getpwuid", side_effect=KeyError),
        ):
            result = audit_logger._get_user()
        assert result == "ci-runner"

    def test_get_user_fallback_username_last(self, monkeypatch):
        """D11-008: USERNAME is the final env-var fallback before 'unknown'."""
        import pwd as _pwd

        monkeypatch.delenv("USER", raising=False)
        monkeypatch.delenv("LOGNAME", raising=False)
        monkeypatch.setenv("USERNAME", "winuser")
        audit_logger = AuditLogger.__new__(AuditLogger)
        with (
            patch("os.getlogin", side_effect=OSError),
            patch.object(_pwd, "getpwuid", side_effect=KeyError),
        ):
            result = audit_logger._get_user()
        assert result == "winuser"

    def test_get_user_fallback_unknown_when_all_unset(self, monkeypatch):
        """D11-008: When all env vars are unset, returns the literal 'unknown'."""
        import pwd as _pwd

        monkeypatch.delenv("USER", raising=False)
        monkeypatch.delenv("LOGNAME", raising=False)
        monkeypatch.delenv("USERNAME", raising=False)
        audit_logger = AuditLogger.__new__(AuditLogger)
        with (
            patch("os.getlogin", side_effect=OSError),
            patch.object(_pwd, "getpwuid", side_effect=KeyError),
        ):
            result = audit_logger._get_user()
        assert result == "unknown"

    def test_log_entry_format(self, tmp_path):
        log_path = tmp_path / "audit.jsonl"
        logger = AuditLogger(path=log_path)
        logger.log_execution("math.add", {"a": 1}, "success", 0, 42)
        entry = json.loads(log_path.read_text().strip())
        expected_keys = {
            "timestamp",
            "user",
            "module_id",
            "input_hash",
            "status",
            "exit_code",
            "duration_ms",
        }
        assert set(entry.keys()) == expected_keys

    def test_cli_error_path_records_real_duration_ms(self):
        """D11-006: Error-path audit call via CLI must pass duration_ms > 0 (not hardcoded 0)."""
        import time
        from unittest.mock import MagicMock

        from click.testing import CliRunner

        from apcore_cli.cli import build_module_command, set_audit_logger

        mock_audit = MagicMock()
        set_audit_logger(mock_audit)
        module_def = MagicMock()
        module_def.module_id = "test.module"
        module_def.description = "A test module"
        module_def.input_schema = {"properties": {}, "required": []}
        module_def.annotations = None
        module_def.tags = []
        executor = MagicMock()

        def slow_raise(*args, **kwargs):
            time.sleep(0.01)
            raise RuntimeError("simulated error")

        executor.call.side_effect = slow_raise
        cmd = build_module_command(module_def, executor)
        runner = CliRunner()
        runner.invoke(cmd, [])
        set_audit_logger(None)

        assert mock_audit.log_execution.called, "log_execution must be called on error"
        call_args = mock_audit.log_execution.call_args
        positional = call_args[0]
        duration_ms = positional[4] if len(positional) >= 5 else call_args[1].get("duration_ms", 0)
        assert duration_ms > 0, f"duration_ms must be > 0 on error path, got {duration_ms}"
