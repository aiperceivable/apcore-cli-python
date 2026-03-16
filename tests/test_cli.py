"""Tests for Core Dispatcher (FE-01)."""

from unittest.mock import MagicMock

import click
import pytest

from apcore_cli.cli import LazyModuleGroup, build_module_command, collect_input, validate_module_id


def _make_mock_registry(module_ids=None, raise_on_list=False):
    """Create a mock Registry with configurable module list."""
    registry = MagicMock()
    if raise_on_list:
        registry.list.side_effect = RuntimeError("Registry error")
    else:
        registry.list.return_value = module_ids or []
    return registry


def _make_mock_executor(result=None):
    """Create a mock Executor."""
    executor = MagicMock()
    executor.call.return_value = result
    return executor


def _make_mock_module_def(module_id="math.add", description="Add two numbers."):
    """Create a mock ModuleDefinition."""
    module_def = MagicMock()
    module_def.module_id = module_id
    module_def.description = description
    module_def.input_schema = {"properties": {}, "required": []}
    module_def.annotations = None
    module_def.tags = []
    return module_def


class TestLazyModuleGroupSkeleton:
    """Task 1: LazyModuleGroup init and list_commands."""

    def test_lazy_module_group_init(self):
        registry = _make_mock_registry()
        executor = _make_mock_executor()
        group = LazyModuleGroup(
            registry=registry,
            executor=executor,
            name="apcore-cli",
        )
        assert group._registry is registry
        assert group._executor is executor
        assert group._module_cache == {}

    def test_list_commands_returns_builtins(self):
        registry = _make_mock_registry()
        executor = _make_mock_executor()
        group = LazyModuleGroup(
            registry=registry,
            executor=executor,
            name="apcore-cli",
        )
        ctx = click.Context(group)
        commands = group.list_commands(ctx)
        for builtin in ["exec", "list", "describe", "completion", "man"]:
            assert builtin in commands

    def test_list_commands_includes_modules(self):
        registry = _make_mock_registry(["math.add", "text.summarize"])
        executor = _make_mock_executor()
        group = LazyModuleGroup(
            registry=registry,
            executor=executor,
            name="apcore-cli",
        )
        ctx = click.Context(group)
        commands = group.list_commands(ctx)
        assert "math.add" in commands
        assert "text.summarize" in commands
        # Should also have builtins
        assert "exec" in commands

    def test_list_commands_registry_error(self):
        registry = _make_mock_registry(raise_on_list=True)
        executor = _make_mock_executor()
        group = LazyModuleGroup(
            registry=registry,
            executor=executor,
            name="apcore-cli",
        )
        ctx = click.Context(group)
        commands = group.list_commands(ctx)
        # Should still return builtins without crashing
        assert "exec" in commands
        assert "list" in commands


class TestGetCommandAndBuild:
    """Task 2: get_command and build_module_command."""

    def test_get_command_builtin(self):
        registry = _make_mock_registry()
        executor = _make_mock_executor()
        group = LazyModuleGroup(registry=registry, executor=executor, name="apcore-cli")
        # Register a dummy built-in
        dummy_cmd = click.Command("list", callback=lambda: None)
        group.add_command(dummy_cmd)
        ctx = click.Context(group)
        result = group.get_command(ctx, "list")
        assert result is dummy_cmd

    def test_get_command_module(self):
        module_def = _make_mock_module_def()
        registry = _make_mock_registry(["math.add"])
        registry.get_definition.return_value = module_def
        executor = _make_mock_executor()
        group = LazyModuleGroup(registry=registry, executor=executor, name="apcore-cli")
        ctx = click.Context(group)
        result = group.get_command(ctx, "math.add")
        assert result is not None
        assert result.name == "math.add"

    def test_get_command_not_found(self):
        registry = _make_mock_registry()
        registry.get_definition.return_value = None
        executor = _make_mock_executor()
        group = LazyModuleGroup(registry=registry, executor=executor, name="apcore-cli")
        ctx = click.Context(group)
        result = group.get_command(ctx, "nonexistent")
        assert result is None

    def test_get_command_caches_module(self):
        module_def = _make_mock_module_def()
        registry = _make_mock_registry(["math.add"])
        registry.get_definition.return_value = module_def
        executor = _make_mock_executor()
        group = LazyModuleGroup(registry=registry, executor=executor, name="apcore-cli")
        ctx = click.Context(group)
        first = group.get_command(ctx, "math.add")
        second = group.get_command(ctx, "math.add")
        assert first is second
        # get_definition should only be called once
        assert registry.get_definition.call_count == 1

    def test_build_module_command_creates_command(self):
        module_def = _make_mock_module_def(module_id="math.add", description="Add two numbers.")
        executor = _make_mock_executor()
        cmd = build_module_command(module_def, executor)
        assert isinstance(cmd, click.Command)
        assert cmd.name == "math.add"
        assert cmd.help == "Add two numbers."


class TestCollectInput:
    """Task 3: STDIN JSON handling."""

    def test_collect_input_no_stdin(self):
        result = collect_input(None, {"a": 5, "b": None})
        assert result == {"a": 5}

    def test_collect_input_stdin_valid_json(self, monkeypatch):
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO('{"a": 5, "b": 10}'))
        result = collect_input("-", {})
        assert result == {"a": 5, "b": 10}

    def test_collect_input_stdin_cli_overrides(self, monkeypatch):
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO('{"a": 5}'))
        result = collect_input("-", {"a": 99})
        assert result == {"a": 99}

    def test_collect_input_stdin_exceeds_limit(self, monkeypatch):
        import io

        large_data = '{"x": "' + "a" * (11 * 1024 * 1024) + '"}'
        monkeypatch.setattr("sys.stdin", io.StringIO(large_data))
        with pytest.raises(SystemExit) as exc_info:
            collect_input("-", {}, large_input=False)
        assert exc_info.value.code == 2

    def test_collect_input_stdin_large_input_allowed(self, monkeypatch):
        import io

        large_data = '{"x": "' + "a" * (11 * 1024 * 1024) + '"}'
        monkeypatch.setattr("sys.stdin", io.StringIO(large_data))
        result = collect_input("-", {}, large_input=True)
        assert "x" in result

    def test_collect_input_stdin_invalid_json(self, monkeypatch):
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
        with pytest.raises(SystemExit) as exc_info:
            collect_input("-", {})
        assert exc_info.value.code == 2

    def test_collect_input_stdin_not_object(self, monkeypatch):
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO("[1, 2, 3]"))
        with pytest.raises(SystemExit) as exc_info:
            collect_input("-", {})
        assert exc_info.value.code == 2

    def test_collect_input_stdin_empty(self, monkeypatch):
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        result = collect_input("-", {})
        assert result == {}


class TestValidateModuleId:
    """Task 4: Module ID validation."""

    def test_validate_module_id_valid(self):
        for mid in ["math.add", "text.summarize", "a", "a.b.c"]:
            validate_module_id(mid)  # Should not raise

    def test_validate_module_id_too_long(self):
        long_id = "a" * 129
        with pytest.raises(SystemExit) as exc_info:
            validate_module_id(long_id)
        assert exc_info.value.code == 2

    def test_validate_module_id_invalid_format(self):
        for mid in ["INVALID!ID", "123abc", ".leading.dot", "a..b", "a."]:
            with pytest.raises(SystemExit) as exc_info:
                validate_module_id(mid)
            assert exc_info.value.code == 2, f"Expected exit 2 for '{mid}'"

    def test_validate_module_id_max_length(self):
        max_id = "a" * 128
        validate_module_id(max_id)  # Should not raise


class TestMainEntryPoint:
    """Task 5: main() entry point and CLI integration."""

    def test_main_help_flag(self, tmp_path):
        from click.testing import CliRunner

        from apcore_cli.__main__ import create_cli

        runner = CliRunner()
        result = runner.invoke(create_cli(extensions_dir=str(tmp_path)), ["--help"])
        assert result.exit_code == 0
        assert "apcore-cli" in result.output.lower() or "apcore" in result.output.lower()

    def test_main_version_flag(self, tmp_path):
        from click.testing import CliRunner

        from apcore_cli.__main__ import create_cli

        runner = CliRunner()
        result = runner.invoke(create_cli(extensions_dir=str(tmp_path), prog_name="apcore-cli"), ["--version"])
        assert result.exit_code == 0
        assert "apcore-cli" in result.output
        assert "0.1.0" in result.output

    def test_main_extensions_dir_not_found(self):
        import pytest

        from apcore_cli.__main__ import create_cli

        with pytest.raises(SystemExit) as exc_info:
            create_cli(extensions_dir="/nonexistent/path")
        assert exc_info.value.code == 47

    def test_main_extensions_dir_valid(self, tmp_path):
        from click.testing import CliRunner

        from apcore_cli.__main__ import create_cli

        # Create a minimal extensions dir
        (tmp_path / "apcore.yaml").write_text("modules: {}\n")
        runner = CliRunner()
        result = runner.invoke(
            create_cli(extensions_dir=str(tmp_path)),
            ["--help"],
        )
        assert result.exit_code == 0

    def test_log_level_flag_takes_effect(self, tmp_path, monkeypatch):
        import logging

        from click.testing import CliRunner

        from apcore_cli.__main__ import create_cli

        original_level = logging.getLogger().level
        try:
            runner = CliRunner()
            cli = create_cli(extensions_dir=str(tmp_path), prog_name="apcore-cli")
            # Use a real subcommand — --help is an eager flag that exits before the callback runs
            result = runner.invoke(cli, ["--log-level", "DEBUG", "completion", "bash"])
            assert result.exit_code == 0
            # After invoking with --log-level DEBUG the root logger level should be DEBUG
            assert logging.getLogger().level == logging.DEBUG
        finally:
            logging.getLogger().setLevel(original_level)

    def test_apcore_logging_level_env_var(self, tmp_path, monkeypatch):
        import logging

        from click.testing import CliRunner

        monkeypatch.setenv("APCORE_LOGGING_LEVEL", "INFO")
        from apcore_cli.__main__ import create_cli

        runner = CliRunner()
        cli = create_cli(extensions_dir=str(tmp_path), prog_name="apcore-cli")
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        # INFO level means apcore logger should NOT be silenced to ERROR
        assert logging.getLogger("apcore").level != logging.ERROR

    def test_cli_logging_level_takes_priority_over_global(self, tmp_path, monkeypatch):
        import logging

        from click.testing import CliRunner

        from apcore_cli.__main__ import create_cli

        original_level = logging.getLogger().level
        try:
            # Global says ERROR, CLI-specific says DEBUG — CLI-specific must win
            monkeypatch.setenv("APCORE_LOGGING_LEVEL", "ERROR")
            monkeypatch.setenv("APCORE_CLI_LOGGING_LEVEL", "DEBUG")
            cli = create_cli(extensions_dir=str(tmp_path), prog_name="apcore-cli")
            runner = CliRunner()
            result = runner.invoke(cli, ["completion", "bash"])
            assert result.exit_code == 0
            assert logging.getLogger().level == logging.DEBUG
        finally:
            logging.getLogger().setLevel(original_level)

    def test_cli_logging_level_fallback_to_global(self, tmp_path, monkeypatch):
        import logging

        from click.testing import CliRunner

        from apcore_cli.__main__ import create_cli

        # CLI-specific not set — must fall back to global
        monkeypatch.delenv("APCORE_CLI_LOGGING_LEVEL", raising=False)
        monkeypatch.setenv("APCORE_LOGGING_LEVEL", "INFO")
        cli = create_cli(extensions_dir=str(tmp_path), prog_name="apcore-cli")
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert logging.getLogger("apcore").level != logging.ERROR


class TestExecCallback:
    """Task 6: Module execution callback."""

    def test_exec_module_success(self):
        from click.testing import CliRunner

        module_def = _make_mock_module_def()
        executor = _make_mock_executor(result={"sum": 15})
        cmd = build_module_command(module_def, executor)
        runner = CliRunner()
        result = runner.invoke(cmd, [])
        assert result.exit_code == 0
        assert "15" in result.output

    def test_exec_module_not_found(self):
        from apcore.errors import ModuleNotFoundError as ApModNotFound
        from click.testing import CliRunner

        module_def = _make_mock_module_def()
        executor = _make_mock_executor()
        executor.call.side_effect = ApModNotFound(module_id="math.add")
        cmd = build_module_command(module_def, executor)
        runner = CliRunner()
        result = runner.invoke(cmd, [])
        assert result.exit_code == 44

    def test_exec_module_validation_error(self):
        from apcore.errors import SchemaValidationError
        from click.testing import CliRunner

        module_def = _make_mock_module_def()
        executor = _make_mock_executor()
        executor.call.side_effect = SchemaValidationError(message="missing field 'a'")
        cmd = build_module_command(module_def, executor)
        runner = CliRunner()
        result = runner.invoke(cmd, [])
        assert result.exit_code == 45

    def test_exec_module_execution_error(self):
        from apcore.errors import ModuleExecuteError
        from click.testing import CliRunner

        module_def = _make_mock_module_def()
        executor = _make_mock_executor()
        executor.call.side_effect = ModuleExecuteError(module_id="math.add", message="division by zero")
        cmd = build_module_command(module_def, executor)
        runner = CliRunner()
        result = runner.invoke(cmd, [])
        assert result.exit_code == 1

    def test_exec_module_disabled(self):
        from apcore.errors import ModuleDisabledError
        from click.testing import CliRunner

        module_def = _make_mock_module_def()
        executor = _make_mock_executor()
        executor.call.side_effect = ModuleDisabledError(module_id="math.add")
        cmd = build_module_command(module_def, executor)
        runner = CliRunner()
        result = runner.invoke(cmd, [])
        assert result.exit_code == 44

    def test_exec_acl_denied(self):
        from apcore.errors import ACLDeniedError
        from click.testing import CliRunner

        module_def = _make_mock_module_def()
        executor = _make_mock_executor()
        executor.call.side_effect = ACLDeniedError(caller_id="user", target_id="math.add")
        cmd = build_module_command(module_def, executor)
        runner = CliRunner()
        result = runner.invoke(cmd, [])
        assert result.exit_code == 77

    def test_exec_keyboard_interrupt(self):
        from click.testing import CliRunner

        module_def = _make_mock_module_def()
        executor = _make_mock_executor()
        executor.call.side_effect = KeyboardInterrupt()
        cmd = build_module_command(module_def, executor)
        runner = CliRunner()
        result = runner.invoke(cmd, [])
        assert result.exit_code == 130
