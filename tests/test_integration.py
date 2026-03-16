"""Integration tests — verify features are wired together end-to-end."""

import json
from unittest.mock import MagicMock

import click
import pytest
from click.testing import CliRunner

from apcore_cli.cli import LazyModuleGroup, build_module_command, set_audit_logger
from apcore_cli.discovery import register_discovery_commands
from apcore_cli.shell import register_shell_commands


def _make_module_def(
    module_id="math.add",
    description="Add two numbers.",
    input_schema=None,
    annotations=None,
    tags=None,
):
    m = MagicMock()
    m.module_id = module_id
    m.canonical_id = module_id
    m.description = description
    m.input_schema = input_schema or {
        "properties": {
            "a": {"type": "integer", "description": "First number"},
            "b": {"type": "integer", "description": "Second number"},
        },
        "required": ["a", "b"],
    }
    m.output_schema = None
    m.annotations = annotations
    m.tags = tags or ["math"]
    m.metadata = {}
    return m


def _make_cli_group(modules, executor_result=None):
    """Build a full CLI group with discovery and shell commands wired in."""
    registry = MagicMock()
    registry.list.return_value = [m.module_id for m in modules]
    defs = {m.module_id: m for m in modules}
    registry.get_definition.side_effect = lambda mid, **kw: defs.get(mid)

    executor = MagicMock()
    executor.call.return_value = executor_result

    @click.group(
        cls=LazyModuleGroup,
        registry=registry,
        executor=executor,
        name="apcore-cli",
    )
    def cli():
        pass

    register_discovery_commands(cli, registry)
    register_shell_commands(cli)
    return cli, executor


class TestSchemaToFlagsIntegration:
    """HIGH-2 fix: build_module_command generates flags from input_schema."""

    def test_module_command_has_schema_flags(self):
        module_def = _make_module_def()
        executor = MagicMock()
        executor.call.return_value = {"sum": 15}
        cmd = build_module_command(module_def, executor)

        param_names = [p.name for p in cmd.params]
        assert "a" in param_names
        assert "b" in param_names

    def test_module_command_has_builtin_flags(self):
        module_def = _make_module_def()
        executor = MagicMock()
        cmd = build_module_command(module_def, executor)

        param_names = [p.name for p in cmd.params]
        assert "input" in param_names
        assert "yes" in param_names
        assert "large_input" in param_names
        assert "format" in param_names

    def test_exec_with_schema_flags(self):
        module_def = _make_module_def()
        executor = MagicMock()
        executor.call.return_value = {"sum": 15}
        cmd = build_module_command(module_def, executor)

        runner = CliRunner()
        result = runner.invoke(cmd, ["--a", "5", "--b", "10"])
        assert result.exit_code == 0
        # Executor should have been called with merged input
        call_args = executor.call.call_args
        assert call_args[0][0] == "math.add"
        inputs = call_args[0][1]
        assert inputs["a"] == 5
        assert inputs["b"] == 10

    def test_exec_with_stdin_input(self):
        module_def = _make_module_def()
        executor = MagicMock()
        executor.call.return_value = {"sum": 15}
        cmd = build_module_command(module_def, executor)

        runner = CliRunner()
        result = runner.invoke(cmd, ["--input", "-"], input='{"a": 5, "b": 10}')
        assert result.exit_code == 0
        call_args = executor.call.call_args
        inputs = call_args[0][1]
        assert inputs["a"] == 5
        assert inputs["b"] == 10

    def test_exec_stdin_cli_override(self):
        module_def = _make_module_def()
        executor = MagicMock()
        executor.call.return_value = {"sum": 99}
        cmd = build_module_command(module_def, executor)

        runner = CliRunner()
        result = runner.invoke(cmd, ["--input", "-", "--a", "99"], input='{"a": 5, "b": 10}')
        assert result.exit_code == 0
        inputs = executor.call.call_args[0][1]
        assert inputs["a"] == 99  # CLI overrides STDIN
        assert inputs["b"] == 10

    def test_builtin_name_collision_exits_2(self):
        # A schema property named 'format' collides with the built-in --format option
        module_def = _make_module_def(
            input_schema={
                "properties": {"format": {"type": "string"}},
                "required": [],
            }
        )
        executor = MagicMock()
        with pytest.raises(SystemExit) as exc_info:
            build_module_command(module_def, executor)
        assert exc_info.value.code == 2

    def test_exec_result_table_format(self):
        module_def = _make_module_def()
        executor = MagicMock()
        executor.call.return_value = {"sum": 42}
        cmd = build_module_command(module_def, executor)
        runner = CliRunner()
        result = runner.invoke(cmd, ["--a", "20", "--b", "22", "--format", "table"])
        assert result.exit_code == 0
        # Table format renders key/value rows
        assert "sum" in result.output
        assert "42" in result.output


class TestApprovalIntegration:
    """HIGH-2 fix: approval gate called before execution."""

    def test_approval_required_with_yes_flag(self):
        module_def = _make_module_def(annotations={"requires_approval": True})
        executor = MagicMock()
        executor.call.return_value = {"result": "ok"}
        cmd = build_module_command(module_def, executor)

        runner = CliRunner()
        result = runner.invoke(cmd, ["--a", "1", "--b", "2", "--yes"])
        assert result.exit_code == 0
        executor.call.assert_called_once()

    def test_approval_required_no_tty_no_bypass(self):
        module_def = _make_module_def(annotations={"requires_approval": True})
        executor = MagicMock()
        cmd = build_module_command(module_def, executor)

        runner = CliRunner()
        result = runner.invoke(cmd, ["--a", "1", "--b", "2"])
        # Non-TTY (CliRunner), no --yes → exit 46
        assert result.exit_code == 46
        executor.call.assert_not_called()


class TestAuditLogIntegration:
    """HIGH-2 fix: audit logger called on exec."""

    def test_audit_log_on_success(self, tmp_path):
        from apcore_cli.security.audit import AuditLogger

        log_path = tmp_path / "audit.jsonl"
        audit = AuditLogger(path=log_path)
        set_audit_logger(audit)
        try:
            module_def = _make_module_def(input_schema={"properties": {}, "required": []})
            executor = MagicMock()
            executor.call.return_value = {"sum": 15}
            cmd = build_module_command(module_def, executor)

            runner = CliRunner()
            result = runner.invoke(cmd, [])
            assert result.exit_code == 0

            entries = log_path.read_text().strip().split("\n")
            entry = json.loads(entries[-1])
            assert entry["module_id"] == "math.add"
            assert entry["status"] == "success"
            assert entry["exit_code"] == 0
            assert entry["duration_ms"] >= 0
        finally:
            set_audit_logger(None)

    def test_audit_log_on_error(self, tmp_path):
        from apcore.errors import ModuleExecuteError

        from apcore_cli.security.audit import AuditLogger

        log_path = tmp_path / "audit.jsonl"
        audit = AuditLogger(path=log_path)
        set_audit_logger(audit)
        try:
            module_def = _make_module_def(input_schema={"properties": {}, "required": []})
            executor = MagicMock()
            executor.call.side_effect = ModuleExecuteError(module_id="math.add")
            cmd = build_module_command(module_def, executor)

            runner = CliRunner()
            result = runner.invoke(cmd, [])
            assert result.exit_code == 1

            entries = log_path.read_text().strip().split("\n")
            entry = json.loads(entries[-1])
            assert entry["status"] == "error"
        finally:
            set_audit_logger(None)


class TestDiscoveryWiring:
    """HIGH-1 fix: discovery commands use real implementation."""

    def test_list_via_full_cli(self):
        modules = [
            _make_module_def("math.add", "Add.", tags=["math"]),
            _make_module_def("text.upper", "Uppercase.", tags=["text"]),
        ]
        cli, _ = _make_cli_group(modules)
        runner = CliRunner()
        result = runner.invoke(cli, ["list", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2

    def test_list_tag_filter_via_full_cli(self):
        modules = [
            _make_module_def("math.add", "Add.", tags=["math"]),
            _make_module_def("text.upper", "Uppercase.", tags=["text"]),
        ]
        cli, _ = _make_cli_group(modules)
        runner = CliRunner()
        result = runner.invoke(cli, ["list", "--tag", "math", "--format", "json"])
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["id"] == "math.add"

    def test_describe_via_full_cli(self):
        modules = [_make_module_def("math.add", "Add two numbers.")]
        cli, _ = _make_cli_group(modules)
        runner = CliRunner()
        result = runner.invoke(cli, ["describe", "math.add", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["id"] == "math.add"


class TestShellWiring:
    """HIGH-1 fix: shell commands registered on real CLI."""

    def test_completion_via_full_cli(self):
        cli, _ = _make_cli_group([])
        runner = CliRunner()
        result = runner.invoke(cli, ["completion", "bash"])
        assert result.exit_code == 0
        assert "complete -F _apcore_cli apcore-cli" in result.output

    def test_man_via_full_cli(self):
        cli, _ = _make_cli_group([])
        runner = CliRunner()
        result = runner.invoke(cli, ["man", "list"])
        assert result.exit_code == 0
        assert ".TH" in result.output


class TestConfigResolverWiring:
    """MED-2 fix: create_cli uses ConfigResolver."""

    def test_create_cli_uses_config_resolver(self, tmp_path):
        import pytest

        from apcore_cli.__main__ import create_cli

        # When no extensions_dir override and default dir doesn't exist,
        # create_cli should exit 47 via ConfigResolver default path
        with pytest.raises(SystemExit) as exc_info:
            create_cli()
        assert exc_info.value.code == 47

    def test_create_cli_override_bypasses_config(self, tmp_path):
        import pytest

        from apcore_cli.__main__ import create_cli

        with pytest.raises(SystemExit) as exc_info:
            create_cli(extensions_dir="/nonexistent")
        assert exc_info.value.code == 47
