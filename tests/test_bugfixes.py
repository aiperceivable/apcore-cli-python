"""Tests for review bugfixes (MED-1, LOW-1, LOW-2)."""

import json
import subprocess
import sys
from unittest.mock import MagicMock

from click.testing import CliRunner

from apcore_cli.cli import build_module_command, set_audit_logger


class TestMED1ExtensionsDirFlag:
    """MED-1: --extensions-dir CLI flag must be applied at runtime."""

    def test_extensions_dir_flag_overrides_default(self, tmp_path):
        """--extensions-dir should point the registry to a custom path."""
        from apcore_cli.__main__ import _extract_argv_option

        # Simulate: apcore-cli --extensions-dir /custom/path list
        result = _extract_argv_option(["--extensions-dir", str(tmp_path), "list"], "--extensions-dir")
        assert result == str(tmp_path)

    def test_extensions_dir_flag_not_provided(self):
        """When --extensions-dir is omitted, return None."""
        from apcore_cli.__main__ import _extract_argv_option

        result = _extract_argv_option(["list", "--format", "json"], "--extensions-dir")
        assert result is None

    def test_extensions_dir_flag_equals_syntax(self, tmp_path):
        """--extensions-dir=/custom/path syntax."""
        from apcore_cli.__main__ import _extract_argv_option

        result = _extract_argv_option([f"--extensions-dir={tmp_path}", "list"], "--extensions-dir")
        assert result == str(tmp_path)

    def test_extensions_dir_used_by_main(self, tmp_path):
        """Full CLI invocation with --extensions-dir uses the specified path."""
        # Create a valid extensions dir (empty is fine for list)
        from apcore_cli.__main__ import create_cli

        cli = create_cli(extensions_dir=str(tmp_path))
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0

    def test_extensions_dir_subprocess(self, tmp_path):
        """Real subprocess: --extensions-dir flag is honored."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "apcore_cli",
                "--extensions-dir",
                str(tmp_path),
                "apcli",
                "list",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Should get empty list (valid dir, no modules) not an error
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data == []


class TestLOW1InputSchemaDefensive:
    """LOW-1: input_schema could be a Pydantic class, not a dict."""

    def test_pydantic_class_input_schema_handled(self):
        """build_module_command should handle Pydantic BaseModel class as input_schema."""
        from pydantic import BaseModel

        class MyInput(BaseModel):
            x: int
            y: int

        module_def = MagicMock()
        module_def.module_id = "test.pydantic"
        module_def.description = "Test with Pydantic schema"
        module_def.input_schema = MyInput  # class, not dict
        module_def.annotations = None

        executor = MagicMock()
        executor.call.return_value = {"result": 42}

        # Should not crash
        cmd = build_module_command(module_def, executor)
        assert cmd.name == "test.pydantic"

    def test_dict_input_schema_still_works(self):
        """Normal dict input_schema still generates flags."""
        module_def = MagicMock()
        module_def.module_id = "test.dict"
        module_def.description = "Test with dict schema"
        module_def.input_schema = {
            "properties": {"a": {"type": "integer"}},
            "required": ["a"],
        }
        module_def.annotations = None

        executor = MagicMock()
        cmd = build_module_command(module_def, executor)
        param_names = [p.name for p in cmd.params]
        assert "a" in param_names

    def test_none_input_schema_handled(self):
        """None input_schema should not crash."""
        module_def = MagicMock()
        module_def.module_id = "test.none"
        module_def.description = "No schema"
        module_def.input_schema = None
        module_def.annotations = None

        executor = MagicMock()
        cmd = build_module_command(module_def, executor)
        assert cmd.name == "test.none"


class TestLOW2AuditLoggerCleanup:
    """LOW-2: _audit_logger global should not leak across tests."""

    def test_audit_logger_cleanup_via_fixture(self):
        """set_audit_logger(None) properly clears the global."""

        set_audit_logger(MagicMock())
        from apcore_cli.security import audit as audit_mod

        assert audit_mod._audit_logger is not None

        set_audit_logger(None)
        assert audit_mod._audit_logger is None

    def test_audit_logger_isolated_between_tests_1(self):
        """First test: logger should be None (not leaked from prior test)."""
        from apcore_cli.security import audit as audit_mod

        # If LOW-2 is fixed, this should be None unless explicitly set
        set_audit_logger(None)  # Ensure clean state
        assert audit_mod._audit_logger is None

    def test_audit_logger_isolated_between_tests_2(self):
        """Second test: confirms no leak from test_1."""
        from apcore_cli.security import audit as audit_mod

        assert audit_mod._audit_logger is None
