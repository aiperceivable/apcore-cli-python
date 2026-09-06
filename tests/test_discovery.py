"""Tests for Discovery commands (FE-04)."""

import json
from unittest.mock import MagicMock

import click
from click.testing import CliRunner

from apcore_cli.discovery import register_discovery_commands


def _make_mock_module(module_id, description="A module.", tags=None):
    m = MagicMock()
    m.module_id = module_id
    m.canonical_id = module_id
    m.description = description
    m.tags = tags or []
    m.input_schema = None
    m.output_schema = None
    m.annotations = None
    m.metadata = {}
    return m


def _make_cli_with_modules(modules):
    """Create a CLI group with discovery commands and mock registry."""
    registry = MagicMock()
    registry.list.return_value = [m.module_id for m in modules]
    defs = {m.module_id: m for m in modules}
    registry.get_definition.side_effect = lambda mid, **kw: defs.get(mid)

    @click.group()
    def cli():
        pass

    register_discovery_commands(cli, registry)
    return cli


class TestListCommand:
    def test_list_cmd_shows_modules(self):
        modules = [
            _make_mock_module("math.add", "Add.", ["math"]),
            _make_mock_module("text.summarize", "Summarize.", ["text"]),
        ]
        cli = _make_cli_with_modules(modules)
        runner = CliRunner()
        result = runner.invoke(cli, ["list", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2
        ids = [m["id"] for m in data]
        assert "math.add" in ids
        assert "text.summarize" in ids

    def test_list_cmd_empty_registry(self):
        cli = _make_cli_with_modules([])
        runner = CliRunner()
        result = runner.invoke(cli, ["list", "--format", "table"])
        assert result.exit_code == 0
        assert "No modules found." in result.output

    def test_list_cmd_tag_filter_single(self):
        modules = [
            _make_mock_module("math.add", "Add.", ["math"]),
            _make_mock_module("text.summarize", "Summarize.", ["text"]),
        ]
        cli = _make_cli_with_modules(modules)
        runner = CliRunner()
        result = runner.invoke(cli, ["list", "--tag", "math", "--format", "json"])
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["id"] == "math.add"

    def test_list_cmd_tag_filter_and(self):
        modules = [
            _make_mock_module("math.add", "Add.", ["math", "core"]),
            _make_mock_module("math.sub", "Sub.", ["math"]),
        ]
        cli = _make_cli_with_modules(modules)
        runner = CliRunner()
        result = runner.invoke(cli, ["list", "--tag", "math", "--tag", "core", "--format", "json"])
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["id"] == "math.add"

    def test_list_cmd_tag_no_match(self):
        modules = [_make_mock_module("math.add", "Add.", ["math"])]
        cli = _make_cli_with_modules(modules)
        runner = CliRunner()
        result = runner.invoke(cli, ["list", "--tag", "nonexistent", "--format", "table"])
        assert result.exit_code == 0
        assert "No modules found matching tags: nonexistent" in result.output

    def test_list_cmd_format_json(self):
        modules = [_make_mock_module("x", "X.")]
        cli = _make_cli_with_modules(modules)
        runner = CliRunner()
        result = runner.invoke(cli, ["list", "--format", "json"])
        data = json.loads(result.output)
        assert isinstance(data, list)

    def test_list_cmd_invalid_format(self):
        cli = _make_cli_with_modules([])
        runner = CliRunner()
        result = runner.invoke(cli, ["list", "--format", "xml"])
        assert result.exit_code == 2


class TestDescribeCommand:
    def test_describe_valid_module(self):
        modules = [_make_mock_module("math.add", "Add two numbers.", ["math"])]
        cli = _make_cli_with_modules(modules)
        runner = CliRunner()
        result = runner.invoke(cli, ["describe", "math.add", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["id"] == "math.add"

    def test_describe_not_found(self):
        cli = _make_cli_with_modules([])
        runner = CliRunner()
        result = runner.invoke(cli, ["describe", "nonexistent"])
        assert result.exit_code == 44

    def test_describe_invalid_id(self):
        cli = _make_cli_with_modules([])
        runner = CliRunner()
        result = runner.invoke(cli, ["describe", "INVALID!"])
        assert result.exit_code == 2

    def test_describe_json_format(self):
        m = _make_mock_module("math.add", "Add.", ["math"])
        m.input_schema = {"type": "object"}
        cli = _make_cli_with_modules([m])
        runner = CliRunner()
        result = runner.invoke(cli, ["describe", "math.add", "--format", "json"])
        data = json.loads(result.output)
        assert "input_schema" in data

    def test_describe_no_annotations(self):
        m = _make_mock_module("simple", "Simple.")
        m.annotations = None
        cli = _make_cli_with_modules([m])
        runner = CliRunner()
        result = runner.invoke(cli, ["describe", "simple", "--format", "json"])
        data = json.loads(result.output)
        assert "annotations" not in data


class TestFormatGroupedModuleList:
    def test_grouped_output_shows_group_tables(self, capsys):
        from apcore_cli.output import format_grouped_module_list

        grouped = {
            "product": [("list", "List products", ["shop"]), ("get", "Get product", [])],
            None: [("standalone", "Standalone command", ["misc"])],
        }
        format_grouped_module_list(grouped)
        out = capsys.readouterr().out
        assert "product" in out
        assert "list" in out
        assert "standalone" in out

    def test_grouped_output_empty(self, capsys):
        from apcore_cli.output import format_grouped_module_list

        format_grouped_module_list({})
        out = capsys.readouterr().out
        assert "No modules found" in out


def _make_mock_module_with_display(module_id, description="A module.", tags=None, display=None):
    """Create a mock module with display overlay metadata."""
    m = _make_mock_module(module_id, description, tags)
    m.metadata = {"display": display} if display else {}
    return m


class TestGroupedDiscovery:
    """Tests for grouped display in list and describe commands."""

    def test_list_flat_flag(self):
        """--flat flag produces flat table output (no group headers)."""
        modules = [
            _make_mock_module_with_display(
                "product.list",
                "List products.",
                ["commerce"],
                display={"cli": {"group": "product", "alias": "list"}},
            ),
            _make_mock_module_with_display(
                "product.get",
                "Get product.",
                ["commerce"],
                display={"cli": {"group": "product", "alias": "get"}},
            ),
        ]
        cli = _make_cli_with_modules(modules)
        runner = CliRunner()
        result = runner.invoke(cli, ["list", "--flat", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2
        ids = [m["id"] for m in data]
        assert "list" in ids
        assert "get" in ids

    def test_list_default_grouped_display(self):
        """Default list (no --flat) shows group headers in table format."""
        modules = [
            _make_mock_module_with_display(
                "product.list",
                "List products.",
                ["commerce"],
                display={"cli": {"group": "product", "alias": "list"}},
            ),
            _make_mock_module_with_display(
                "product.get",
                "Get product.",
                ["commerce"],
                display={"cli": {"group": "product", "alias": "get"}},
            ),
            _make_mock_module_with_display(
                "healthcheck",
                "Health check.",
                [],
                display={"cli": {"group": "", "alias": "healthcheck"}},
            ),
        ]
        cli = _make_cli_with_modules(modules)
        runner = CliRunner()
        result = runner.invoke(cli, ["list", "--format", "table"])
        assert result.exit_code == 0
        # Group header for "product" should appear
        assert "product" in result.output
        # Commands within the group
        assert "list" in result.output
        assert "get" in result.output
        # Top-level module in "Other" section
        assert "Other" in result.output
        assert "healthcheck" in result.output

    def test_list_grouped_dot_notation_fallback(self):
        """Modules with dotted alias but no explicit group use dot-split grouping."""
        modules = [
            _make_mock_module("order.create", "Create order.", ["commerce"]),
            _make_mock_module("order.cancel", "Cancel order.", ["commerce"]),
        ]
        cli = _make_cli_with_modules(modules)
        runner = CliRunner()
        result = runner.invoke(cli, ["list", "--format", "table"])
        assert result.exit_code == 0
        # Should group under "order"
        assert "order" in result.output
        assert "create" in result.output
        assert "cancel" in result.output

    def test_list_grouped_empty_registry(self):
        """Grouped list with no modules shows empty message."""
        cli = _make_cli_with_modules([])
        runner = CliRunner()
        result = runner.invoke(cli, ["list", "--format", "table"])
        assert result.exit_code == 0
        assert "No modules found." in result.output

    def test_list_grouped_tag_filter_no_match(self):
        """Grouped list with tag filter and no matches shows tag-specific message."""
        modules = [_make_mock_module("order.create", "Create.", ["commerce"])]
        cli = _make_cli_with_modules(modules)
        runner = CliRunner()
        result = runner.invoke(cli, ["list", "--tag", "nonexistent", "--format", "table"])
        assert result.exit_code == 0
        assert "No modules found matching tags: nonexistent" in result.output

    def test_describe_group_dot_command(self):
        """describe with dotted module_id (e.g. product.list) works."""
        modules = [_make_mock_module("product.list", "List products.", ["commerce"])]
        cli = _make_cli_with_modules(modules)
        runner = CliRunner()
        result = runner.invoke(cli, ["describe", "product.list", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["id"] == "product.list"
        assert data["description"] == "List products."

    def test_describe_full_module_id(self):
        """describe with a longer canonical module_id works."""
        modules = [
            _make_mock_module("product.list_products.get", "Get product list.", ["commerce"]),
        ]
        cli = _make_cli_with_modules(modules)
        runner = CliRunner()
        result = runner.invoke(cli, ["describe", "product.list_products.get", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["id"] == "product.list_products.get"


class TestExposureListFilter:
    """Task 7: list --exposure option."""

    def _make_cli_with_exposure(self, modules, exposure_filter):
        """Create a CLI with discovery commands and an exposure filter in context."""
        registry = MagicMock()
        registry.list.return_value = [m.module_id for m in modules]
        defs = {m.module_id: m for m in modules}
        registry.get_definition.side_effect = lambda mid, **kw: defs.get(mid)

        @click.group()
        @click.pass_context
        def cli(ctx):
            ctx.ensure_object(dict)
            ctx.obj["exposure_filter"] = exposure_filter

        register_discovery_commands(cli, registry)
        return cli

    def test_list_exposure_exposed_default(self):
        from apcore_cli.exposure import ExposureFilter

        modules = [
            _make_mock_module("admin.users", "Manage users"),
            _make_mock_module("webhooks.stripe", "Stripe hooks"),
        ]
        ef = ExposureFilter(mode="include", include=["admin.*"])
        cli = self._make_cli_with_exposure(modules, ef)
        result = CliRunner().invoke(cli, ["list", "--flat", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        ids = [d["id"] for d in data]
        assert "admin.users" in ids
        assert "webhooks.stripe" not in ids

    def test_list_exposure_hidden(self):
        from apcore_cli.exposure import ExposureFilter

        modules = [
            _make_mock_module("admin.users", "Manage users"),
            _make_mock_module("webhooks.stripe", "Stripe hooks"),
        ]
        ef = ExposureFilter(mode="include", include=["admin.*"])
        cli = self._make_cli_with_exposure(modules, ef)
        result = CliRunner().invoke(cli, ["list", "--flat", "--format", "json", "--exposure", "hidden"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        ids = [d["id"] for d in data]
        assert "webhooks.stripe" in ids
        assert "admin.users" not in ids

    def test_list_exposure_all_json_format(self):
        from apcore_cli.exposure import ExposureFilter

        modules = [
            _make_mock_module("admin.users", "Manage users"),
            _make_mock_module("webhooks.stripe", "Stripe hooks"),
        ]
        ef = ExposureFilter(mode="include", include=["admin.*"])
        cli = self._make_cli_with_exposure(modules, ef)
        result = CliRunner().invoke(cli, ["list", "--flat", "--format", "json", "--exposure", "all"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2
        exposed_map = {d["id"]: d["exposed"] for d in data}
        assert exposed_map["admin.users"] is True
        assert exposed_map["webhooks.stripe"] is False

    def test_list_exposure_all_shows_column(self):
        from apcore_cli.exposure import ExposureFilter

        modules = [
            _make_mock_module("admin.users", "Manage users"),
            _make_mock_module("webhooks.stripe", "Stripe hooks"),
        ]
        ef = ExposureFilter(mode="include", include=["admin.*"])
        cli = self._make_cli_with_exposure(modules, ef)
        result = CliRunner().invoke(cli, ["list", "--flat", "--format", "table", "--exposure", "all"])
        assert result.exit_code == 0
        assert "Exposure" in result.output


class TestValidateErrBranchAuditsExecution:
    def test_validate_err_branch_audits_execution(self):
        """D11-009: Validate command Err branch must call audit log_execution."""
        from apcore_cli.discovery import register_validate_command
        from apcore_cli.security import audit as audit_module

        mock_audit = MagicMock()
        original_audit = audit_module._audit_logger
        audit_module._audit_logger = mock_audit
        try:

            @click.group()
            def apcli():
                pass

            registry = MagicMock()
            module_def = MagicMock()
            module_def.module_id = "test.mod"
            module_def.description = "A test"
            module_def.input_schema = None
            module_def.annotations = None
            registry.get_definition.return_value = module_def
            executor = MagicMock()
            executor.validate.side_effect = RuntimeError("validation failed")
            register_validate_command(apcli, registry, executor)
            runner = CliRunner()
            runner.invoke(apcli, ["validate", "test.mod"])
            assert mock_audit.log_execution.called, "audit log_execution must be called in validate Err branch"
        finally:
            audit_module._audit_logger = original_audit


class TestApcliExecAppliesSandboxFlag:
    def test_apcli_exec_applies_sandbox_flag(self):
        """D11-005: apcli exec with --sandbox flag must use sandbox execution path."""
        from unittest.mock import patch

        from apcore_cli.discovery import register_exec_command
        from apcore_cli.security.sandbox import Sandbox

        @click.group()
        def apcli():
            pass

        registry = MagicMock()
        module_def = MagicMock()
        module_def.module_id = "test.mod"
        module_def.description = "A test"
        module_def.input_schema = None
        module_def.annotations = None
        registry.get_definition.return_value = module_def
        executor = MagicMock()
        executor.call.return_value = {"ok": True}
        register_exec_command(apcli, registry, executor)
        runner = CliRunner()
        with patch.object(Sandbox, "execute", return_value={"ok": True}):
            result = runner.invoke(apcli, ["exec", "test.mod", "--sandbox"])
        assert (
            result.exit_code != 2
        ), f"--sandbox flag must be accepted by apcli exec (exit_code={result.exit_code}, output={result.output})"


class TestExecAndValidateExitCodeMapping:
    """discovery.py:483 — the ``except Exception`` handlers in ``exec`` and
    ``validate`` resolved the exit code via ``_ERROR_CODE_MAP`` (the narrow
    apcore wire-code table, keyed on a string ``e.code``) instead of the full
    ``exit_code_for_error`` (which also matches CLI exception CLASSES —
    ``AuthenticationError``, ``ConfigDecryptionError``, ``ApprovalDeniedError``,
    etc.). ``AuthenticationError`` carries no ``.code`` attribute at all, so it
    fell through to the generic exit 1 instead of its canonical 77.
    """

    def test_validate_maps_authentication_error_to_77(self):
        from apcore_cli.discovery import register_validate_command
        from apcore_cli.security.auth import AuthenticationError

        @click.group()
        def apcli():
            pass

        registry = MagicMock()
        module_def = MagicMock()
        module_def.module_id = "test.mod"
        module_def.input_schema = None
        module_def.annotations = None
        registry.get_definition.return_value = module_def
        executor = MagicMock()
        executor.validate.side_effect = AuthenticationError("no api key configured")
        register_validate_command(apcli, registry, executor)

        result = CliRunner().invoke(apcli, ["validate", "test.mod"])
        assert result.exit_code == 77, result.output

    def test_exec_maps_authentication_error_to_77(self):
        from apcore_cli.discovery import register_exec_command
        from apcore_cli.security.auth import AuthenticationError

        @click.group()
        def apcli():
            pass

        registry = MagicMock()
        module_def = MagicMock()
        module_def.module_id = "test.mod"
        module_def.description = "A test"
        module_def.input_schema = None
        module_def.annotations = None
        registry.get_definition.return_value = module_def
        executor = MagicMock()
        executor.call.side_effect = AuthenticationError("no api key configured")
        register_exec_command(apcli, registry, executor)

        result = CliRunner().invoke(apcli, ["exec", "test.mod"])
        assert result.exit_code == 77, result.output

    def test_validate_maps_config_decryption_error_to_47(self):
        from apcore_cli.discovery import register_validate_command
        from apcore_cli.security.config_encryptor import ConfigDecryptionError

        @click.group()
        def apcli():
            pass

        registry = MagicMock()
        module_def = MagicMock()
        module_def.module_id = "test.mod"
        module_def.input_schema = None
        module_def.annotations = None
        registry.get_definition.return_value = module_def
        executor = MagicMock()
        executor.validate.side_effect = ConfigDecryptionError("bad key")
        register_validate_command(apcli, registry, executor)

        result = CliRunner().invoke(apcli, ["validate", "test.mod"])
        assert result.exit_code == 47, result.output
