"""Tests for Core Dispatcher (FE-01)."""

import logging
from unittest.mock import MagicMock

import click
import pytest

from apcore_cli.cli import (
    GroupedModuleGroup,
    LazyModuleGroup,
    _LazyGroup,
    build_module_command,
    collect_input,
    validate_module_id,
)


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
        from apcore_cli import __version__

        assert "apcore-cli" in result.output
        assert __version__ in result.output

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


class TestDisplayOverlayAliasRouting:
    """Tests for CLI alias routing via metadata['display']['cli']['alias'] (§5.13)."""

    def _make_module_def_with_alias(self, module_id: str, cli_alias: str, description: str = "desc"):
        module_def = _make_mock_module_def(module_id=module_id, description=description)
        module_def.metadata = {"display": {"cli": {"alias": cli_alias}, "alias": cli_alias}}
        return module_def

    def test_list_commands_uses_cli_alias(self):
        """list_commands returns the CLI alias instead of module_id when alias is set."""
        module_def = self._make_module_def_with_alias("payment.status", "pay-status")
        registry = _make_mock_registry(["payment.status"])
        registry.get_definition.return_value = module_def
        group = LazyModuleGroup(registry=registry, executor=_make_mock_executor(), name="cli")
        ctx = click.Context(group)
        commands = group.list_commands(ctx)
        assert "pay-status" in commands
        assert "payment.status" not in commands

    def test_get_command_by_cli_alias(self):
        """get_command resolves an alias name to the correct module."""
        module_def = self._make_module_def_with_alias("payment.status", "pay-status")
        registry = _make_mock_registry(["payment.status"])
        registry.get_definition.return_value = module_def
        group = LazyModuleGroup(registry=registry, executor=_make_mock_executor(), name="cli")
        ctx = click.Context(group)
        cmd = group.get_command(ctx, "pay-status")
        assert cmd is not None
        assert cmd.name == "pay-status"

    def test_get_command_alias_uses_descriptor_cache(self):
        """After alias map is built, get_command should NOT call get_definition again."""
        module_def = self._make_module_def_with_alias("payment.status", "pay-status")
        registry = _make_mock_registry(["payment.status"])
        registry.get_definition.return_value = module_def
        group = LazyModuleGroup(registry=registry, executor=_make_mock_executor(), name="cli")
        ctx = click.Context(group)
        # First call builds alias map (calls get_definition) and caches descriptor
        group.get_command(ctx, "pay-status")
        # Second call should use module_cache — no additional get_definition calls
        group.get_command(ctx, "pay-status")
        # get_definition called once during _build_alias_map, then cached
        assert registry.get_definition.call_count == 1

    def test_get_command_fallback_to_module_id_when_no_alias(self):
        """Modules without a CLI alias are still accessible by their module_id."""
        module_def = _make_mock_module_def(module_id="math.add")
        module_def.metadata = {}
        registry = _make_mock_registry(["math.add"])
        registry.get_definition.return_value = module_def
        group = LazyModuleGroup(registry=registry, executor=_make_mock_executor(), name="cli")
        ctx = click.Context(group)
        cmd = group.get_command(ctx, "math.add")
        assert cmd is not None

    def test_build_module_command_uses_display_alias_as_name(self):
        """build_module_command uses cmd_name (alias) as the Click command name."""
        module_def = self._make_module_def_with_alias("payment.status", "pay-status", "Check payment status")
        cmd = build_module_command(module_def, _make_mock_executor(), cmd_name="pay-status")
        assert cmd.name == "pay-status"

    def test_build_module_command_uses_display_description(self):
        """build_module_command uses display.cli.description if present."""
        module_def = _make_mock_module_def(module_id="math.add", description="Original description")
        module_def.metadata = {
            "display": {
                "cli": {"alias": "add", "description": "CLI-specific description"},
                "alias": "add",
            }
        }
        cmd = build_module_command(module_def, _make_mock_executor(), cmd_name="add")
        assert cmd.help == "CLI-specific description"


# ---------------------------------------------------------------------------
# Grouped-commands feature tests (Tasks 1-6)
# ---------------------------------------------------------------------------


def _make_mock_module_def_with_display(module_id, description="desc", display=None, metadata=None):
    m = _make_mock_module_def(module_id, description)
    m.metadata = metadata or {}
    if display:
        m.metadata["display"] = display
    return m


def _make_grouped_group(module_defs, builtins=True):
    """Helper: build a GroupedModuleGroup from a list of (module_id, descriptor) pairs."""
    ids = [mid for mid, _ in module_defs]
    registry = _make_mock_registry(ids)
    # Map module_id → descriptor for get_definition
    def_map = dict(module_defs)
    registry.get_definition.side_effect = lambda mid: def_map.get(mid)
    executor = _make_mock_executor()
    group = GroupedModuleGroup(
        registry=registry,
        executor=executor,
        name="cli",
    )
    if builtins:
        for name in ["exec", "list", "describe", "completion", "man"]:
            group.add_command(click.Command(name, callback=lambda: None))
    return group


class TestResolveGroup:
    """Tests for GroupedModuleGroup._resolve_group."""

    def test_resolve_group_explicit_group(self):
        desc = _make_mock_module_def_with_display("x.y", display={"cli": {"group": "mygrp", "alias": "cmd1"}})
        assert GroupedModuleGroup._resolve_group("x.y", desc) == ("mygrp", "cmd1")

    def test_resolve_group_explicit_group_no_alias(self):
        desc = _make_mock_module_def_with_display("x.y", display={"cli": {"group": "mygrp"}})
        assert GroupedModuleGroup._resolve_group("x.y", desc) == ("mygrp", "x.y")

    def test_resolve_group_opt_out_empty_string(self):
        desc = _make_mock_module_def_with_display("math.add", display={"cli": {"group": "", "alias": "add"}})
        assert GroupedModuleGroup._resolve_group("math.add", desc) == (None, "add")

    def test_resolve_group_auto_from_alias_dot(self):
        desc = _make_mock_module_def_with_display("payment.status", display={"cli": {"alias": "pay.status"}})
        assert GroupedModuleGroup._resolve_group("payment.status", desc) == ("pay", "status")

    def test_resolve_group_auto_from_module_id_dot(self):
        desc = _make_mock_module_def_with_display("math.add")
        assert GroupedModuleGroup._resolve_group("math.add", desc) == ("math", "add")

    def test_resolve_group_no_dot_top_level(self):
        desc = _make_mock_module_def_with_display("status")
        assert GroupedModuleGroup._resolve_group("status", desc) == (None, "status")

    def test_resolve_group_multi_dot_first_only(self):
        desc = _make_mock_module_def_with_display("a.b.c")
        assert GroupedModuleGroup._resolve_group("a.b.c", desc) == ("a", "b.c")

    def test_resolve_group_empty_module_id_warns(self, caplog):
        desc = _make_mock_module_def_with_display("")
        with caplog.at_level(logging.WARNING):
            result = GroupedModuleGroup._resolve_group("", desc)
        assert result == (None, "")
        assert "Empty module_id" in caplog.text

    def test_resolve_group_none_metadata(self):
        """_resolve_group handles descriptor with metadata=None."""
        desc = _make_mock_module_def("user.create")
        desc.metadata = None
        group, cmd = GroupedModuleGroup._resolve_group("user.create", desc)
        assert group == "user"
        assert cmd == "create"


class TestBuildGroupMap:
    """Tests for GroupedModuleGroup._build_group_map."""

    def test_build_group_map_three_groups(self):
        defs = [
            ("math.add", _make_mock_module_def_with_display("math.add")),
            ("math.sub", _make_mock_module_def_with_display("math.sub")),
            ("text.upper", _make_mock_module_def_with_display("text.upper")),
            ("io.read", _make_mock_module_def_with_display("io.read")),
        ]
        group = _make_grouped_group(defs, builtins=False)
        group._build_group_map()
        assert "math" in group._group_map
        assert "text" in group._group_map
        assert "io" in group._group_map
        assert len(group._group_map["math"]) == 2

    def test_build_group_map_idempotent(self):
        defs = [("math.add", _make_mock_module_def_with_display("math.add"))]
        group = _make_grouped_group(defs, builtins=False)
        group._build_group_map()
        first_map = dict(group._group_map)
        group._build_group_map()  # second call — should be no-op
        assert group._group_map == first_map

    def test_build_group_map_builtin_collision_warns(self, caplog):
        # Module whose group name collides with a builtin
        desc = _make_mock_module_def_with_display("list.items", display={"cli": {"group": "list", "alias": "items"}})
        defs = [("list.items", desc)]
        group = _make_grouped_group(defs, builtins=False)
        with caplog.at_level(logging.WARNING):
            group._build_group_map()
        assert "collides" in caplog.text

    def test_build_group_map_failure_allows_retry(self):
        registry = _make_mock_registry(["math.add"])
        registry.get_definition.return_value = _make_mock_module_def_with_display("math.add")
        executor = _make_mock_executor()
        group = GroupedModuleGroup(registry=registry, executor=executor, name="cli")
        # Force _build_alias_map to raise
        group._build_alias_map = MagicMock(side_effect=RuntimeError("boom"))
        group._build_group_map()
        assert not group._group_map_built  # flag not set on failure
        # Fix the problem
        group._build_alias_map = MagicMock()
        group._build_group_map()  # should retry now
        assert group._group_map_built

    def test_build_group_map_invalid_group_name_falls_back(self, caplog):
        """Invalid group names from display.cli.group are treated as top-level."""
        import logging

        desc = _make_mock_module_def_with_display(
            "my.mod",
            display={"cli": {"group": "INVALID!", "alias": "cmd"}},
        )
        defs = [("my.mod", desc)]
        group = _make_grouped_group(defs, builtins=False)
        with caplog.at_level(logging.WARNING, logger="apcore_cli.cli"):
            group._build_group_map()
        assert "INVALID!" not in group._group_map
        assert "cmd" in group._top_level_modules
        assert "not shell-safe" in caplog.text

    def test_build_group_map_with_display_overlay_group(self):
        desc = _make_mock_module_def_with_display(
            "payment.check_status",
            display={"cli": {"group": "billing", "alias": "status"}},
        )
        defs = [("payment.check_status", desc)]
        group = _make_grouped_group(defs, builtins=False)
        group._build_group_map()
        assert "billing" in group._group_map
        assert "status" in group._group_map["billing"]


class TestGroupedModuleGroupRouting:
    """Tests for GroupedModuleGroup.list_commands and get_command."""

    def test_list_commands_shows_groups_and_top_level(self):
        defs = [
            ("math.add", _make_mock_module_def_with_display("math.add")),
            ("status", _make_mock_module_def_with_display("status")),
        ]
        group = _make_grouped_group(defs)
        ctx = click.Context(group)
        commands = group.list_commands(ctx)
        assert "math" in commands  # group
        assert "status" in commands  # top-level
        assert "exec" in commands  # builtin

    def test_get_command_returns_lazy_group(self):
        defs = [("math.add", _make_mock_module_def_with_display("math.add"))]
        group = _make_grouped_group(defs)
        ctx = click.Context(group)
        result = group.get_command(ctx, "math")
        assert isinstance(result, click.Group)
        assert isinstance(result, _LazyGroup)

    def test_get_command_returns_top_level_command(self):
        defs = [("status", _make_mock_module_def_with_display("status"))]
        group = _make_grouped_group(defs)
        ctx = click.Context(group)
        result = group.get_command(ctx, "status")
        assert isinstance(result, click.Command)
        assert not isinstance(result, click.Group)

    def test_get_command_returns_builtin(self):
        defs = []
        group = _make_grouped_group(defs)
        ctx = click.Context(group)
        result = group.get_command(ctx, "exec")
        assert result is not None
        assert result.name == "exec"

    def test_get_command_unknown_returns_none(self):
        defs = []
        group = _make_grouped_group(defs)
        ctx = click.Context(group)
        result = group.get_command(ctx, "nonexistent")
        assert result is None

    def test_get_command_caches_lazy_group(self):
        defs = [("math.add", _make_mock_module_def_with_display("math.add"))]
        group = _make_grouped_group(defs)
        ctx = click.Context(group)
        first = group.get_command(ctx, "math")
        second = group.get_command(ctx, "math")
        assert first is second


class TestLazyGroupInner:
    """Tests for _LazyGroup."""

    def _make_lazy_group(self):
        d1 = _make_mock_module_def_with_display("math.add")
        d2 = _make_mock_module_def_with_display("math.sub")
        members = {
            "add": ("math.add", d1),
            "sub": ("math.sub", d2),
        }
        return _LazyGroup(
            members=members,
            executor=_make_mock_executor(),
            name="math",
        )

    def test_lazy_group_list_commands(self):
        grp = self._make_lazy_group()
        ctx = click.Context(grp)
        assert grp.list_commands(ctx) == ["add", "sub"]

    def test_lazy_group_get_command(self):
        grp = self._make_lazy_group()
        ctx = click.Context(grp)
        cmd = grp.get_command(ctx, "add")
        assert cmd is not None
        assert cmd.name == "add"

    def test_lazy_group_get_command_not_found(self):
        grp = self._make_lazy_group()
        ctx = click.Context(grp)
        assert grp.get_command(ctx, "nonexistent") is None

    def test_lazy_group_caches_commands(self):
        grp = self._make_lazy_group()
        ctx = click.Context(grp)
        first = grp.get_command(ctx, "add")
        second = grp.get_command(ctx, "add")
        assert first is second


class TestGroupedHelpDisplay:
    """Tests for GroupedModuleGroup.format_help output."""

    def _get_help_text(self, defs):
        group = _make_grouped_group(defs)
        ctx = click.Context(group)
        formatter = click.HelpFormatter()
        group.format_help(ctx, formatter)
        return formatter.getvalue()

    def test_root_help_shows_groups_section(self):
        defs = [("math.add", _make_mock_module_def_with_display("math.add"))]
        text = self._get_help_text(defs)
        assert "Groups:" in text

    def test_root_help_shows_group_with_count(self):
        defs = [
            ("math.add", _make_mock_module_def_with_display("math.add")),
            ("math.sub", _make_mock_module_def_with_display("math.sub")),
        ]
        text = self._get_help_text(defs)
        assert "(2 commands)" in text

    def test_root_help_shows_top_level_modules(self):
        defs = [("status", _make_mock_module_def_with_display("status", description="Check status"))]
        text = self._get_help_text(defs)
        assert "Modules:" in text
        assert "status" in text

    def test_root_help_shows_builtin_commands(self):
        defs = []
        text = self._get_help_text(defs)
        assert "Commands:" in text
        assert "exec" in text

    def test_group_help_shows_commands(self):
        d1 = _make_mock_module_def_with_display("math.add")
        d2 = _make_mock_module_def_with_display("math.sub")
        members = {"add": ("math.add", d1), "sub": ("math.sub", d2)}
        grp = _LazyGroup(members=members, executor=_make_mock_executor(), name="math")
        ctx = click.Context(grp)
        formatter = click.HelpFormatter()
        grp.format_help(ctx, formatter)
        text = formatter.getvalue()
        assert "add" in text
        assert "sub" in text


class TestCreateCliGrouped:
    """Test that create_cli uses GroupedModuleGroup."""

    def test_create_cli_uses_grouped_module_group(self, tmp_path):
        from apcore_cli.__main__ import create_cli

        cli = create_cli(extensions_dir=str(tmp_path), prog_name="test-cli")
        assert isinstance(cli, GroupedModuleGroup)


class TestGroupedE2E:
    """End-to-end integration tests for grouped command invocation."""

    def _make_e2e_group(self):
        """Build a GroupedModuleGroup with product (2), health (1), standalone (1)."""
        defs = [
            ("product.list", _make_mock_module_def_with_display("product.list", "List products")),
            ("product.get", _make_mock_module_def_with_display("product.get", "Get product")),
            ("health.check", _make_mock_module_def_with_display("health.check", "Run health check")),
            ("standalone", _make_mock_module_def_with_display("standalone", "Standalone cmd")),
        ]
        return _make_grouped_group(defs)

    def test_grouped_invocation_product_get(self):
        from click.testing import CliRunner

        group = self._make_e2e_group()
        result = CliRunner().invoke(group, ["product", "get"])
        assert result.exit_code == 0

    def test_single_command_group_works(self):
        from click.testing import CliRunner

        group = self._make_e2e_group()
        result = CliRunner().invoke(group, ["health", "check"])
        assert result.exit_code == 0

    def test_top_level_module_works(self):
        from click.testing import CliRunner

        group = self._make_e2e_group()
        result = CliRunner().invoke(group, ["standalone"])
        assert result.exit_code == 0

    def test_unknown_group_exits_2(self):
        from click.testing import CliRunner

        group = self._make_e2e_group()
        result = CliRunner().invoke(group, ["nonexistent"])
        assert result.exit_code == 2

    def test_unknown_command_in_group_exits_2(self):
        from click.testing import CliRunner

        group = self._make_e2e_group()
        result = CliRunner().invoke(group, ["product", "nonexistent"])
        assert result.exit_code == 2


class TestVerboseHelp:
    """Tests for --verbose help flag controlling built-in option visibility."""

    def test_builtin_options_hidden_by_default(self):
        """Built-in options are hidden from help by default."""
        from apcore_cli import cli as cli_mod

        cli_mod._verbose_help = False
        try:
            module_def = _make_mock_module_def()
            cmd = build_module_command(module_def, _make_mock_executor())
            hidden_names = [p.name for p in cmd.params if getattr(p, "hidden", False)]
            assert "input" in hidden_names
            assert "yes" in hidden_names
            assert "large_input" in hidden_names
            assert "format" in hidden_names
            assert "sandbox" in hidden_names
        finally:
            cli_mod._verbose_help = False

    def test_builtin_options_shown_when_verbose(self):
        """Built-in options are visible when verbose help is enabled."""
        from apcore_cli import cli as cli_mod

        cli_mod._verbose_help = True
        try:
            module_def = _make_mock_module_def()
            cmd = build_module_command(module_def, _make_mock_executor())
            hidden_names = [p.name for p in cmd.params if getattr(p, "hidden", False)]
            assert "input" not in hidden_names
            assert "yes" not in hidden_names
            assert "large_input" not in hidden_names
            assert "format" not in hidden_names
            # sandbox is always hidden (not yet implemented)
            assert "sandbox" in hidden_names
        finally:
            cli_mod._verbose_help = False

    def test_set_verbose_help_function(self):
        """set_verbose_help correctly sets the module-level flag."""
        from apcore_cli import cli as cli_mod
        from apcore_cli.cli import set_verbose_help

        original = cli_mod._verbose_help
        try:
            set_verbose_help(True)
            assert cli_mod._verbose_help is True
            set_verbose_help(False)
            assert cli_mod._verbose_help is False
        finally:
            cli_mod._verbose_help = original
