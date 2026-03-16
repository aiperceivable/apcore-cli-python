"""End-to-end tests with real apcore modules — no mocks.

These tests create real apcore Registry + Executor with real module
implementations and invoke the CLI via CliRunner exactly as a user would.
"""

import json
import subprocess
import sys

import click
import pytest
from apcore import Executor, Registry, module
from click.testing import CliRunner

from apcore_cli.cli import LazyModuleGroup, build_module_command, set_audit_logger
from apcore_cli.discovery import register_discovery_commands
from apcore_cli.shell import register_shell_commands

# ---------------------------------------------------------------------------
# Real apcore module definitions
# ---------------------------------------------------------------------------


@module(id="math.add", description="Add two numbers", tags=["math", "core"])
def _math_add(a: int, b: int) -> dict:
    return {"sum": a + b}


@module(id="math.multiply", description="Multiply two numbers", tags=["math"])
def _math_multiply(a: int, b: int) -> dict:
    return {"product": a * b}


@module(id="text.upper", description="Uppercase a string", tags=["text", "core"])
def _text_upper(text: str) -> dict:
    return {"result": text.upper()}


@module(id="text.reverse", description="Reverse a string", tags=["text"])
def _text_reverse(text: str) -> dict:
    return {"result": text[::-1]}


@module(
    id="admin.dangerous",
    description="A module requiring approval",
    tags=["admin"],
    annotations={"requires_approval": True},
)
def _admin_dangerous(target: str) -> dict:
    return {"deleted": target}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def real_registry():
    """Create a real apcore Registry with real modules."""
    r = Registry()
    for fn in [_math_add, _math_multiply, _text_upper, _text_reverse, _admin_dangerous]:
        fm = fn.apcore_module
        r.register(fm.module_id, fm)
    return r


@pytest.fixture
def real_executor(real_registry):
    """Create a real apcore Executor."""
    return Executor(real_registry)


@pytest.fixture
def real_cli(real_registry, real_executor):
    """Build a full CLI group wired to real registry + executor."""
    set_audit_logger(None)  # Disable audit for tests

    @click.group(
        cls=LazyModuleGroup,
        registry=real_registry,
        executor=real_executor,
        name="apcore-cli",
    )
    def cli():
        pass

    register_discovery_commands(cli, real_registry)
    register_shell_commands(cli)
    return cli


# ---------------------------------------------------------------------------
# Test: Real module execution via CLI flags
# ---------------------------------------------------------------------------


class TestRealExecution:
    """Execute real apcore modules through the full CLI pipeline."""

    def test_math_add_via_flags(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["math.add", "--a", "5", "--b", "10"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["sum"] == 15

    def test_math_add_negative_numbers(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["math.add", "--a", "-3", "--b", "7"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["sum"] == 4

    def test_math_add_zero(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["math.add", "--a", "0", "--b", "0"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["sum"] == 0

    def test_math_multiply(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["math.multiply", "--a", "6", "--b", "7"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["product"] == 42

    def test_text_upper(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["text.upper", "--text", "hello world"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["result"] == "HELLO WORLD"

    def test_text_reverse(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["text.reverse", "--text", "abcdef"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["result"] == "fedcba"

    def test_text_upper_unicode(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["text.upper", "--text", "café"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["result"] == "CAFÉ"


# ---------------------------------------------------------------------------
# Test: STDIN piping (real JSON input)
# ---------------------------------------------------------------------------


class TestRealStdinPiping:
    """Pipe JSON via STDIN to real modules — simulates shell piping."""

    def test_stdin_json_input(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(
            real_cli,
            ["math.add", "--input", "-"],
            input='{"a": 100, "b": 200}',
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["sum"] == 300

    def test_stdin_cli_flag_overrides(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(
            real_cli,
            ["math.add", "--input", "-", "--a", "999"],
            input='{"a": 1, "b": 2}',
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["sum"] == 1001  # 999 (CLI override) + 2 (STDIN)

    def test_stdin_invalid_json(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(
            real_cli,
            ["math.add", "--input", "-"],
            input="not valid json",
        )
        assert result.exit_code == 2

    def test_stdin_array_not_object(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(
            real_cli,
            ["math.add", "--input", "-"],
            input="[1, 2, 3]",
        )
        assert result.exit_code == 2

    def test_stdin_empty(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(
            real_cli,
            ["math.add", "--input", "-"],
            input="",
        )
        # Empty STDIN → empty dict → missing required fields → validation error
        assert result.exit_code == 45


# ---------------------------------------------------------------------------
# Test: Real discovery commands
# ---------------------------------------------------------------------------


class TestRealDiscovery:
    """List and describe real modules."""

    def test_list_all_modules_json(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["list", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        ids = [m["id"] for m in data]
        assert "math.add" in ids
        assert "text.upper" in ids
        assert len(data) == 5

    def test_list_filter_by_tag(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["list", "--tag", "math", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        ids = [m["id"] for m in data]
        assert "math.add" in ids
        assert "math.multiply" in ids
        assert "text.upper" not in ids

    def test_list_filter_by_multiple_tags(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["list", "--tag", "text", "--tag", "core", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        # Only text.upper has both "text" AND "core"
        assert len(data) == 1
        assert data[0]["id"] == "text.upper"

    def test_list_filter_no_match(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["list", "--tag", "nonexistent", "--format", "table"])
        assert result.exit_code == 0
        assert "No modules found matching tags" in result.output

    def test_list_table_format(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["list", "--format", "table"])
        assert result.exit_code == 0
        assert "math.add" in result.output
        assert "text.upper" in result.output

    def test_describe_module_json(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["describe", "math.add", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["id"] == "math.add"
        assert data["description"] == "Add two numbers"
        assert "input_schema" in data

    def test_describe_module_with_annotations(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["describe", "admin.dangerous", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["id"] == "admin.dangerous"
        assert "annotations" in data
        assert data["annotations"]["requires_approval"] is True

    def test_describe_not_found(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["describe", "nonexistent.module"])
        assert result.exit_code == 44

    def test_describe_invalid_id(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["describe", "INVALID!"])
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Test: Approval gate with real module annotations
# ---------------------------------------------------------------------------


class TestRealApprovalGate:
    """Approval gate with real ModuleAnnotations objects."""

    def test_approval_required_blocked_in_non_tty(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["admin.dangerous", "--target", "my-data"])
        # CliRunner is non-TTY, no --yes → exit 46
        assert result.exit_code == 46
        assert "requires approval" in result.output.lower() or "requires approval" in (
            result.stderr if hasattr(result, "stderr") else ""
        )

    def test_approval_bypassed_with_yes(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["admin.dangerous", "--target", "my-data", "--yes"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["deleted"] == "my-data"

    def test_approval_bypassed_with_env_var(self, real_cli, monkeypatch):
        monkeypatch.setenv("APCORE_CLI_AUTO_APPROVE", "1")
        runner = CliRunner()
        result = runner.invoke(real_cli, ["admin.dangerous", "--target", "my-data"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["deleted"] == "my-data"

    def test_no_approval_normal_module(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["math.add", "--a", "1", "--b", "2"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["sum"] == 3


# ---------------------------------------------------------------------------
# Test: Auto-generated flags from schema
# ---------------------------------------------------------------------------


class TestRealSchemaFlags:
    """Verify schema-generated CLI flags match real module schemas."""

    def test_math_add_has_a_and_b_flags(self, real_cli, real_registry):
        module_def = real_registry.get_definition("math.add")
        executor = Executor(real_registry)
        cmd = build_module_command(module_def, executor)

        param_names = [p.name for p in cmd.params]
        assert "a" in param_names
        assert "b" in param_names

    def test_text_upper_has_text_flag(self, real_cli, real_registry):
        module_def = real_registry.get_definition("text.upper")
        executor = Executor(real_registry)
        cmd = build_module_command(module_def, executor)

        param_names = [p.name for p in cmd.params]
        assert "text" in param_names

    def test_help_shows_flags(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["math.add", "--help"])
        assert result.exit_code == 0
        assert "--a" in result.output
        assert "--b" in result.output

    def test_missing_required_flag(self, real_cli):
        runner = CliRunner()
        # math.add requires both a and b
        result = runner.invoke(real_cli, ["math.add", "--a", "5"])
        # Should fail with validation error (missing 'b')
        assert result.exit_code == 45

    def test_wrong_type_flag(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["math.add", "--a", "notanumber", "--b", "5"])
        # Click rejects non-integer before we even get to validation
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Test: Audit logging with real execution
# ---------------------------------------------------------------------------


class TestRealAuditLog:
    """Verify audit log entries are written on real execution."""

    def test_audit_log_written_on_success(self, real_cli, tmp_path):
        from apcore_cli.security.audit import AuditLogger

        log_path = tmp_path / "audit.jsonl"
        audit = AuditLogger(path=log_path)
        set_audit_logger(audit)

        runner = CliRunner()
        result = runner.invoke(real_cli, ["math.add", "--a", "5", "--b", "10"])
        assert result.exit_code == 0

        entries = log_path.read_text().strip().split("\n")
        entry = json.loads(entries[0])
        assert entry["module_id"] == "math.add"
        assert entry["status"] == "success"
        assert entry["exit_code"] == 0
        assert entry["duration_ms"] >= 0
        assert len(entry["input_hash"]) == 64  # SHA-256 hex

        set_audit_logger(None)


# ---------------------------------------------------------------------------
# Test: Shell completion and man pages
# ---------------------------------------------------------------------------


class TestRealShellIntegration:
    """Shell completion and man pages against real registry."""

    def test_completion_bash(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["completion", "bash"])
        assert result.exit_code == 0
        assert "apcore-cli" in result.output
        assert "exec" in result.output

    def test_completion_zsh(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["completion", "zsh"])
        assert result.exit_code == 0
        assert "compdef" in result.output

    def test_completion_fish(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["completion", "fish"])
        assert result.exit_code == 0
        assert "complete -c apcore-cli" in result.output

    def test_man_list(self, real_cli):
        runner = CliRunner()
        result = runner.invoke(real_cli, ["man", "list"])
        assert result.exit_code == 0
        assert ".TH" in result.output
        assert "EXIT CODES" in result.output


# ---------------------------------------------------------------------------
# Test: subprocess invocation (like a real user would run)
# ---------------------------------------------------------------------------


class TestSubprocessInvocation:
    """Run apcore-cli as a real subprocess — closest to real usage."""

    def test_python_m_apcore_cli_help(self, tmp_path):
        result = subprocess.run(
            [sys.executable, "-m", "apcore_cli", "--extensions-dir", str(tmp_path), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "apcore-cli" in result.stdout.lower() or "apcore" in result.stdout.lower()

    def test_python_m_apcore_cli_version(self, tmp_path):
        result = subprocess.run(
            [sys.executable, "-m", "apcore_cli", "--extensions-dir", str(tmp_path), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        from apcore_cli import __version__

        assert result.returncode == 0
        assert __version__ in result.stdout
