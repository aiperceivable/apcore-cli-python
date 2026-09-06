"""Tests for FE-11 system management commands after FE-13 registrar split.

Each of the six per-subcommand registrars (``register_health_command`` …
``register_config_command``) is exercised via Click's ``CliRunner`` against
a mock executor. The legacy batched :func:`register_system_commands`
wrapper retains the probe-skip contract (no-op when system modules are
unregistered); that path is covered too.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import click
import pytest
from click.testing import CliRunner

from apcore_cli.system_cmd import (
    _call_system_module,
    _check_system_approval,
    _format_health_module_tty,
    _format_health_summary_tty,
    _format_usage_summary_tty,
    _system_modules_available,
    register_config_command,
    register_disable_command,
    register_enable_command,
    register_health_command,
    register_reload_command,
    register_system_commands,
    register_usage_command,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_executor(*, return_value=None, raise_error: Exception | None = None):
    """Mock executor whose `validate` always succeeds (so the probe returns True)."""
    ex = MagicMock()
    if raise_error is not None:
        ex.call.side_effect = raise_error
    else:
        ex.call.return_value = return_value if return_value is not None else {}
    # Probe: validate must NOT raise for `_system_modules_available` to return True.
    ex.validate.return_value = MagicMock(valid=True)
    return ex


def _build_apcli_with(registrar, executor) -> click.Group:
    @click.group()
    def apcli() -> None:
        pass

    registrar(apcli, executor)
    return apcli


# ---------------------------------------------------------------------------
# Probe / helper unit tests
# ---------------------------------------------------------------------------


class TestProbeAndHelpers:
    def test_probe_returns_true_when_validate_succeeds(self):
        ex = _make_executor()
        assert _system_modules_available(ex) is True

    def test_probe_returns_false_when_validate_raises(self):
        ex = MagicMock()
        ex.validate.side_effect = RuntimeError("boom")
        assert _system_modules_available(ex) is False

    def test_probe_falls_back_to_registry_when_no_validate(self):
        ex = MagicMock(spec=["_registry"])
        ex._registry.get_definition.return_value = object()
        assert _system_modules_available(ex) is True

    def test_probe_returns_false_when_registry_has_no_module(self):
        ex = MagicMock(spec=["_registry"])
        ex._registry.get_definition.return_value = None
        assert _system_modules_available(ex) is False

    def test_probe_returns_false_with_neither_validate_nor_registry(self):
        ex = MagicMock(spec=[])
        assert _system_modules_available(ex) is False

    def test_call_system_module_delegates_to_executor(self):
        ex = MagicMock()
        ex.call.return_value = {"ok": True}
        out = _call_system_module(ex, "system.x", {"a": 1})
        ex.call.assert_called_once_with("system.x", {"a": 1})
        assert out == {"ok": True}

    def test_check_system_approval_no_op_without_registry(self):
        # Executor with no `_registry` attribute → helper silently skips.
        ex = MagicMock(spec=["call"])
        _check_system_approval(ex, "system.x", auto_approve=True)

    def test_check_system_approval_swallows_registry_errors(self):
        ex = MagicMock()
        ex._registry.get_definition.side_effect = RuntimeError("bust")
        # Must not raise — the executor-side gate is the authoritative check.
        _check_system_approval(ex, "system.x", auto_approve=True)


class TestFormatters:
    def test_health_summary_empty(self, capsys):
        _format_health_summary_tty({"summary": {}, "modules": []})
        out = capsys.readouterr().out
        assert "No modules found." in out

    def test_health_summary_non_empty(self, capsys):
        _format_health_summary_tty(
            {
                "summary": {"total_modules": 2, "healthy": 1, "error": 1},
                "modules": [
                    {"module_id": "a", "status": "healthy", "error_rate": 0.0, "top_error": None},
                    {
                        "module_id": "b",
                        "status": "error",
                        "error_rate": 0.12,
                        "top_error": {"code": "BOOM", "count": 3},
                    },
                ],
            }
        )
        out = capsys.readouterr().out
        assert "Health Overview (2 modules)" in out
        assert "BOOM" in out
        assert "1 healthy" in out

    def test_health_module_detail(self, capsys):
        _format_health_module_tty(
            {
                "module_id": "a",
                "status": "healthy",
                "total_calls": 100,
                "error_count": 5,
                "error_rate": 0.05,
                "avg_latency_ms": 12,
                "p99_latency_ms": 50,
                "recent_errors": [{"code": "E1", "count": 2, "last_occurred": "now"}],
            }
        )
        out = capsys.readouterr().out
        assert "Module: a" in out
        assert "Recent Errors" in out
        assert "E1" in out

    def test_usage_summary_empty(self, capsys):
        _format_usage_summary_tty({"modules": [], "period": "24h"})
        out = capsys.readouterr().out
        assert "No usage data for period 24h." in out

    def test_usage_summary_non_empty(self, capsys):
        _format_usage_summary_tty(
            {
                "modules": [
                    {"module_id": "a", "call_count": 10, "error_count": 1, "avg_latency_ms": 20, "trend": "up"}
                ],
                "period": "24h",
            }
        )
        out = capsys.readouterr().out
        assert "Usage Summary (last 24h)" in out
        assert "10 calls" in out


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


class TestHealthCommand:
    def test_summary_json(self):
        ex = _make_executor(return_value={"summary": {"total_modules": 0}, "modules": []})
        cli = _build_apcli_with(register_health_command, ex)
        result = CliRunner().invoke(cli, ["health", "--format", "json"])
        assert result.exit_code == 0, result.output
        assert "modules" in json.loads(result.output)
        ex.call.assert_called_once()
        args, _ = ex.call.call_args
        assert args[0] == "system.health.summary"

    def test_single_module_json(self):
        ex = _make_executor(return_value={"module_id": "x", "status": "healthy"})
        cli = _build_apcli_with(register_health_command, ex)
        result = CliRunner().invoke(cli, ["health", "x", "--format", "json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["module_id"] == "x"
        assert ex.call.call_args.args[0] == "system.health.module"

    def test_exception_produces_exit_1(self):
        ex = _make_executor(raise_error=RuntimeError("boom"))
        cli = _build_apcli_with(register_health_command, ex)
        result = CliRunner().invoke(cli, ["health"])
        assert result.exit_code == 1
        assert "boom" in result.output


# ---------------------------------------------------------------------------
# usage
# ---------------------------------------------------------------------------


class TestUsageCommand:
    def test_summary_json(self):
        ex = _make_executor(return_value={"modules": [], "period": "24h"})
        cli = _build_apcli_with(register_usage_command, ex)
        result = CliRunner().invoke(cli, ["usage", "--format", "json"])
        assert result.exit_code == 0, result.output
        assert ex.call.call_args.args[0] == "system.usage.summary"

    def test_single_module_json(self):
        ex = _make_executor(return_value={"module_id": "x", "call_count": 1})
        cli = _build_apcli_with(register_usage_command, ex)
        result = CliRunner().invoke(cli, ["usage", "x", "--format", "json"])
        assert result.exit_code == 0, result.output
        assert ex.call.call_args.args[0] == "system.usage.module"

    def test_exception_produces_exit_1(self):
        ex = _make_executor(raise_error=RuntimeError("boom"))
        cli = _build_apcli_with(register_usage_command, ex)
        result = CliRunner().invoke(cli, ["usage"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# enable / disable / reload — require --reason
# ---------------------------------------------------------------------------


class TestEnableDisableReload:
    def test_enable_happy_path_json(self):
        ex = _make_executor(return_value={"ok": True})
        cli = _build_apcli_with(register_enable_command, ex)
        result = CliRunner().invoke(
            cli, ["enable", "foo.bar", "--reason", "maintenance window", "--yes", "--format", "json"]
        )
        assert result.exit_code == 0, result.output
        args, _ = ex.call.call_args
        assert args[0] == "system.control.toggle_feature"
        assert args[1] == {"module_id": "foo.bar", "enabled": True, "reason": "maintenance window"}

    def test_enable_table_output_summary(self):
        """Invoke directly in a TTY stream so the non-JSON branch runs."""
        import contextlib
        import io
        import sys as _sys
        from unittest.mock import patch

        ex = _make_executor(return_value={"ok": True})
        cli = _build_apcli_with(register_enable_command, ex)
        # CliRunner always swaps in a non-TTY stream; patch isatty + capture
        # via a TTY-reporting buffer instead.
        buf = io.StringIO()
        buf.isatty = lambda: True  # type: ignore[method-assign]
        with patch.object(_sys, "stdout", buf), contextlib.suppress(SystemExit):
            cli.main(
                ["enable", "foo.bar", "--reason", "r", "--yes"],
                standalone_mode=False,
            )
        assert "enabled" in buf.getvalue().lower()

    def test_enable_missing_reason(self):
        ex = _make_executor()
        cli = _build_apcli_with(register_enable_command, ex)
        result = CliRunner().invoke(cli, ["enable", "foo.bar"])
        assert result.exit_code != 0
        assert "reason" in result.output.lower()

    def test_disable_happy_path(self):
        ex = _make_executor(return_value={"ok": True})
        cli = _build_apcli_with(register_disable_command, ex)
        result = CliRunner().invoke(cli, ["disable", "foo.bar", "--reason", "hotfix", "--yes", "--format", "json"])
        assert result.exit_code == 0, result.output
        args, _ = ex.call.call_args
        assert args[1]["enabled"] is False

    def test_disable_error_path(self):
        ex = _make_executor(raise_error=RuntimeError("kaput"))
        cli = _build_apcli_with(register_disable_command, ex)
        result = CliRunner().invoke(cli, ["disable", "foo.bar", "--reason", "r", "--yes"])
        assert result.exit_code == 1
        assert "kaput" in result.output

    def test_reload_renders_versions(self):
        import contextlib
        import io
        import sys as _sys
        from unittest.mock import patch

        ex = _make_executor(
            return_value={"previous_version": "1.0.0", "new_version": "1.1.0", "reload_duration_ms": 42}
        )
        cli = _build_apcli_with(register_reload_command, ex)
        buf = io.StringIO()
        buf.isatty = lambda: True  # type: ignore[method-assign]
        with patch.object(_sys, "stdout", buf), contextlib.suppress(SystemExit):
            cli.main(
                ["reload", "foo.bar", "--reason", "upgrade", "--yes"],
                standalone_mode=False,
            )
        out = buf.getvalue()
        assert "1.0.0" in out and "1.1.0" in out and "42" in out

    def test_reload_json(self):
        ex = _make_executor(return_value={"new_version": "2.0.0"})
        cli = _build_apcli_with(register_reload_command, ex)
        result = CliRunner().invoke(cli, ["reload", "foo.bar", "--reason", "r", "--yes", "--format", "json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["new_version"] == "2.0.0"


# ---------------------------------------------------------------------------
# config get / set
# ---------------------------------------------------------------------------


class TestConfigCommand:
    def test_config_get_success(self, monkeypatch):
        """`apcli config get <key>` reads from apcore.Config().get(key)."""
        from apcore_cli import system_cmd

        fake_config_cls = MagicMock()
        fake_config_cls.return_value.get.return_value = "42"
        monkeypatch.setattr(
            "apcore.Config",
            fake_config_cls,
            raising=False,
        )
        # Guard: patch inside the lazy-imported reference too.
        import sys

        apcore_mod = sys.modules.get("apcore")
        if apcore_mod is not None:
            monkeypatch.setattr(apcore_mod, "Config", fake_config_cls, raising=False)

        ex = _make_executor()
        cli = _build_apcli_with(register_config_command, ex)
        result = CliRunner().invoke(cli, ["config", "get", "some.key"])
        assert result.exit_code == 0, result.output
        assert "some.key" in result.output
        assert "42" in result.output
        _ = system_cmd  # silence unused import

    def test_config_get_error_path(self, monkeypatch):
        """Exception during Config().get propagates to exit 1."""
        bad_config = MagicMock()
        bad_config.return_value.get.side_effect = RuntimeError("nope")
        import sys

        apcore_mod = sys.modules.get("apcore")
        if apcore_mod is not None:
            monkeypatch.setattr(apcore_mod, "Config", bad_config, raising=False)
        monkeypatch.setattr("apcore.Config", bad_config, raising=False)

        ex = _make_executor()
        cli = _build_apcli_with(register_config_command, ex)
        result = CliRunner().invoke(cli, ["config", "get", "k"])
        assert result.exit_code == 1

    def test_config_set_json_value_parsed(self):
        """Values parseable as JSON are forwarded as their typed form."""
        ex = _make_executor(return_value={"old_value": None, "new_value": 42})
        cli = _build_apcli_with(register_config_command, ex)
        result = CliRunner().invoke(cli, ["config", "set", "max_workers", "42", "--reason", "bump"])
        assert result.exit_code == 0, result.output
        args, _ = ex.call.call_args
        assert args[0] == "system.control.update_config"
        assert args[1]["value"] == 42  # int, not "42"

    def test_config_set_string_fallback(self):
        """Non-JSON value is forwarded as a plain string."""
        ex = _make_executor(return_value={"old_value": "x", "new_value": "hello"})
        cli = _build_apcli_with(register_config_command, ex)
        result = CliRunner().invoke(cli, ["config", "set", "greeting", "hello", "--reason", "demo", "--format", "json"])
        assert result.exit_code == 0, result.output
        args, _ = ex.call.call_args
        assert args[1]["value"] == "hello"

    def test_config_set_error_path(self):
        ex = _make_executor(raise_error=RuntimeError("blocked"))
        cli = _build_apcli_with(register_config_command, ex)
        result = CliRunner().invoke(cli, ["config", "set", "k", "v", "--reason", "r"])
        assert result.exit_code == 1

    def test_config_set_accepts_yes_flag(self):
        """W3: config set must accept --yes/-y to bypass CLI-side approval,
        matching enable/disable/reload sibling commands."""
        ex = _make_executor(return_value={"old_value": None, "new_value": 1})
        cli = _build_apcli_with(register_config_command, ex)
        result = CliRunner().invoke(cli, ["config", "set", "k", "1", "--reason", "r", "--yes"])
        assert result.exit_code == 0, result.output

    def test_config_set_approval_gate_invoked(self):
        """W3: config set must route through _check_system_approval — verifies
        the CLI-side prompt wiring is present (sibling to enable/disable/reload)."""
        from unittest.mock import patch

        ex = _make_executor(return_value={"old_value": None, "new_value": 1})
        cli = _build_apcli_with(register_config_command, ex)
        with patch("apcore_cli.system_cmd._check_system_approval") as mock_gate:
            CliRunner().invoke(cli, ["config", "set", "k", "1", "--reason", "r", "-y"])
        mock_gate.assert_called_once()
        args, _ = mock_gate.call_args
        assert args[1] == "system.control.update_config"
        assert args[2] is True  # --yes propagated as auto_approve


class TestApprovalDenialExitsCleanly:
    """Issue system_cmd.py:267 — `_check_system_approval` was called BEFORE
    the local `try:` block in enable/disable/reload/config-set, so a denied
    or timed-out approval propagated as an unhandled Python traceback (exit
    1 under Click's `standalone_mode=True`) instead of the canonical exit 46
    every other error in these commands produces via `_exit_on_system_error`.

    Regression for moving all four `_check_system_approval` call sites
    inside their function's `try:` block.
    """

    @pytest.mark.parametrize(
        ("registrar", "args"),
        [
            (register_enable_command, ["enable", "foo.bar", "--reason", "r", "--yes"]),
            (register_disable_command, ["disable", "foo.bar", "--reason", "r", "--yes"]),
            (register_reload_command, ["reload", "foo.bar", "--reason", "r", "--yes"]),
            (register_config_command, ["config", "set", "k", "1", "--reason", "r", "--yes"]),
        ],
        ids=["enable", "disable", "reload", "config_set"],
    )
    def test_denied_approval_exits_46_not_unhandled(self, registrar, args):
        from unittest.mock import patch

        from apcore_cli.approval import ApprovalDeniedError

        ex = _make_executor(return_value={"ok": True})
        cli = _build_apcli_with(registrar, ex)
        with patch(
            "apcore_cli.system_cmd._check_system_approval",
            side_effect=ApprovalDeniedError("Approval denied."),
        ):
            result = CliRunner().invoke(cli, args)

        assert result.exit_code == 46, (result.output, result.exception)
        # The denial must short-circuit before the underlying system module runs.
        ex.call.assert_not_called()

    @pytest.mark.parametrize(
        ("registrar", "args"),
        [
            (register_enable_command, ["enable", "foo.bar", "--reason", "r", "--yes"]),
            (register_config_command, ["config", "set", "k", "1", "--reason", "r", "--yes"]),
        ],
        ids=["enable", "config_set"],
    )
    def test_timed_out_approval_exits_46_not_unhandled(self, registrar, args):
        from unittest.mock import patch

        from apcore_cli.approval import ApprovalTimeoutError

        ex = _make_executor(return_value={"ok": True})
        cli = _build_apcli_with(registrar, ex)
        with patch(
            "apcore_cli.system_cmd._check_system_approval",
            side_effect=ApprovalTimeoutError("Approval prompt timed out."),
        ):
            result = CliRunner().invoke(cli, args)

        assert result.exit_code == 46, (result.output, result.exception)
        ex.call.assert_not_called()


# ---------------------------------------------------------------------------
# Legacy batched wrapper
# ---------------------------------------------------------------------------


class TestLegacyBatchedWrapper:
    def test_register_system_commands_skips_when_probe_fails(self):
        """No system modules → wrapper is a no-op (no commands registered)."""
        ex = MagicMock()
        ex.validate.side_effect = RuntimeError("no")
        ex._registry.get_definition.return_value = None

        @click.group()
        def cli() -> None:
            pass

        register_system_commands(cli, ex)
        assert cli.commands == {}

    def test_register_system_commands_registers_all_six_when_probe_passes(self):
        ex = _make_executor()

        @click.group()
        def cli() -> None:
            pass

        register_system_commands(cli, ex)
        for name in ("health", "usage", "enable", "disable", "reload", "config"):
            assert name in cli.commands


# ---------------------------------------------------------------------------
# Backwards-compat smoke tests (kept from pre-FE-13 suite)
# ---------------------------------------------------------------------------


def test_system_cmd_module_importable():
    from apcore_cli import system_cmd

    assert system_cmd is not None


def test_system_cmd_has_register_system_commands():
    assert callable(register_system_commands)


# Ensure pytest plugin doesn't complain about unused imports.
_ = pytest


class TestHealthSummaryFourTiers:
    """The health summary line must cover all four tiers, not three.

    apcore classifies modules as healthy / degraded / error / **unknown**, and
    `unknown` ("no calls recorded yet") is the state every module in a fresh
    project is in. Iterating only the first three made the summary line
    contradict the table printed directly above it — the rows listed modules
    while the total read "no data". apcore >= 0.28.0 declares the four-tier set
    canonically in `sys-health-summary.schema.json`; the SDKs emitted `unknown`
    all along, so this was a long-standing reporting hole the schema
    correction brought into focus.
    """

    def _payload(self, summary):
        return {
            "summary": summary,
            "modules": [{"module_id": "probe.echo", "status": "unknown", "error_rate": 0.0, "top_error": None}],
        }

    def test_unknown_only_project_is_not_reported_as_no_data(self, capsys):
        from apcore_cli.system_cmd import _format_health_summary_tty

        _format_health_summary_tty(
            self._payload({"total_modules": 1, "healthy": 0, "degraded": 0, "error": 0, "unknown": 1})
        )
        out = capsys.readouterr().out

        assert "Summary: 1 unknown" in out
        assert (
            "no data" not in out
        ), "a project whose modules are all unaccounted-for must not report 'no data' while the table above lists them"

    def test_all_four_tiers_are_reported(self, capsys):
        from apcore_cli.system_cmd import _format_health_summary_tty

        _format_health_summary_tty(
            self._payload({"total_modules": 10, "healthy": 4, "degraded": 3, "error": 2, "unknown": 1})
        )
        out = capsys.readouterr().out

        assert "4 healthy" in out
        assert "3 degraded" in out
        assert "2 error" in out
        assert "1 unknown" in out

    def test_genuinely_empty_summary_still_reports_no_data(self, capsys):
        from apcore_cli.system_cmd import _format_health_summary_tty

        _format_health_summary_tty(
            self._payload({"total_modules": 0, "healthy": 0, "degraded": 0, "error": 0, "unknown": 0})
        )
        assert "no data" in capsys.readouterr().out
