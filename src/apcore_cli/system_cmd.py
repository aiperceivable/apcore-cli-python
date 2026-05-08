"""System management commands — health, usage, enable, disable, reload, config (FE-11, FE-13).

FE-13 §4.9 split the batched ``register_system_commands`` into six
per-subcommand registrars (``register_health_command`` … ``register_config_command``).
The per-command shape is required for apcli include/exclude filtering —
the factory dispatcher decides per-entry whether to attach to the
``apcli`` group.

The legacy :func:`register_system_commands` remains as a thin wrapper for
pre-v0.7 call sites; it probes the executor for ``system.*`` modules and
skips attachment when they are absent, preserving the old "no-op if system
modules unavailable" behavior.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, NoReturn

import click

import apcore_cli.cli as _cli_module
from apcore_cli.approval import check_approval
from apcore_cli.exit_codes import exit_code_for_error
from apcore_cli.output import format_exec_result, resolve_format

logger = logging.getLogger("apcore_cli.system_cmd")


def _call_system_module(executor: Any, module_id: str, inputs: dict[str, Any]) -> Any:
    """Call a system module and return the result."""
    return executor.call(module_id, inputs)


def _exit_on_system_error(e: Exception) -> NoReturn:
    """Print user-facing error and exit with the canonical exit code for `e`.

    Cross-SDK parity (audit D11-B-002, 2026-05-08): use the canonical
    error→exit-code mapping (44 module-not-found, 46 approval-denied,
    47 config-invalid, 77 ACL-denied, etc.) instead of collapsing every
    error to exit 1. Mirrors Rust ``cli::map_module_error_to_exit_code``
    and TS ``exitCodeForError``.
    """
    click.echo(f"Error: {e}", err=True)
    sys.exit(exit_code_for_error(e))


def _check_system_approval(executor: Any, module_id: str, auto_approve: bool) -> None:
    """Check approval for system commands that have requires_approval=True."""
    module_def = _try_get_module_def(executor, module_id)
    if module_def is not None:
        # Let check_approval's own exits (sys.exit(46), ApprovalDeniedError, etc.)
        # propagate to the caller — only the registry-lookup step is guarded above.
        check_approval(module_def, auto_approve)


def _try_get_module_def(executor: Any, module_id: str) -> Any:
    """Best-effort registry lookup via executor._registry (apcore private attr).

    Centralises the private-attribute access so the fragility surface is one
    function rather than scattered across system_cmd and strategy. Falls back
    to None on any AttributeError so callers gracefully degrade.
    """
    try:
        if hasattr(executor, "_registry"):
            return executor._registry.get_definition(module_id)
    except Exception as e:
        logger.warning("executor._registry.get_definition(%r) failed: %s", module_id, e)
    return None


def _system_modules_available(executor: Any) -> bool:
    """Probe the executor for ``system.health.summary`` without executing it."""
    try:
        if hasattr(executor, "validate"):
            executor.validate("system.health.summary", {})
            return True
        return _try_get_module_def(executor, "system.health.summary") is not None
    except Exception as e:
        logger.warning("System module probe failed (system commands will be unavailable): %s", e)
        return False


def _format_health_summary_tty(result: dict[str, Any]) -> None:
    """Render health summary as a TTY table."""
    summary = result.get("summary", {})
    modules = result.get("modules", [])

    if not modules:
        click.echo("No modules found.")
        return

    click.echo(f"Health Overview ({summary.get('total_modules', len(modules))} modules)\n")
    click.echo(f"  {'Module':<28} {'Status':<12} {'Error Rate':<12} Top Error")
    click.echo(f"  {'-' * 28} {'-' * 12} {'-' * 12} {'-' * 20}")
    for m in modules:
        top = m.get("top_error")
        top_str = f"{top['code']} ({top.get('count', '?')})" if top else "—"
        rate = f"{m.get('error_rate', 0) * 100:.1f}%"
        click.echo(f"  {m['module_id']:<28} {m['status']:<12} {rate:<12} {top_str}")

    parts = []
    for key in ("healthy", "degraded", "error"):
        count = summary.get(key, 0)
        if count:
            parts.append(f"{count} {key}")
    click.echo(f"\nSummary: {', '.join(parts) or 'no data'}")


def _format_health_module_tty(result: dict[str, Any]) -> None:
    """Render single-module health detail."""
    click.echo(f"Module: {result.get('module_id', '?')}")
    click.echo(f"Status: {result.get('status', 'unknown')}")
    total = result.get("total_calls", 0)
    errors = result.get("error_count", 0)
    rate = result.get("error_rate", 0)
    avg = result.get("avg_latency_ms", 0)
    p99 = result.get("p99_latency_ms", 0)
    click.echo(f"Calls: {total:,} total | {errors:,} errors | {rate * 100:.1f}% error rate")
    click.echo(f"Latency: {avg:.0f}ms avg | {p99:.0f}ms p99")

    recent = result.get("recent_errors", [])
    if recent:
        click.echo(f"\nRecent Errors (top {len(recent)}):")
        for e in recent:
            count = e.get("count", "?")
            last = e.get("last_occurred", "?")
            click.echo(f"  {e.get('code', '?'):<24} x{count}  (last: {last})")


def _format_usage_summary_tty(result: dict[str, Any]) -> None:
    """Render usage summary as a TTY table."""
    modules = result.get("modules", [])
    period = result.get("period", "?")

    if not modules:
        click.echo(f"No usage data for period {period}.")
        return

    click.echo(f"Usage Summary (last {period})\n")
    click.echo(f"  {'Module':<24} {'Calls':>8} {'Errors':>8} {'Avg Latency':>12} {'Trend':<10}")
    click.echo(f"  {'-' * 24} {'-' * 8} {'-' * 8} {'-' * 12} {'-' * 10}")
    for m in modules:
        avg = f"{m.get('avg_latency_ms', 0):.0f}ms"
        click.echo(
            f"  {m['module_id']:<24} {m.get('call_count', 0):>8,} "
            f"{m.get('error_count', 0):>8,} {avg:>12} {m.get('trend', ''):>10}"
        )

    total_calls = result.get("total_calls", sum(m.get("call_count", 0) for m in modules))
    total_errors = result.get("total_errors", sum(m.get("error_count", 0) for m in modules))
    click.echo(f"\nTotal: {total_calls:,} calls | {total_errors:,} errors")


# ---------------------------------------------------------------------------
# Per-subcommand registrars (FE-13 §4.9)
# ---------------------------------------------------------------------------


def register_health_command(apcli_group: click.Group, executor: Any) -> None:
    """Register the ``health`` subcommand on the given group."""

    @apcli_group.command("health")
    @click.argument("module_id", required=False)
    @click.option("--threshold", type=float, default=0.01, help="Error rate threshold (default: 0.01).")
    @click.option("--all", "include_all", is_flag=True, default=False, help="Include healthy modules.")
    @click.option("--errors", type=int, default=10, help="Max recent errors (module detail only).")
    @click.option(
        "--format", "output_format", type=click.Choice(["table", "json", "csv", "yaml", "jsonl"]), default=None
    )
    def health_cmd(
        module_id: str | None,
        threshold: float,
        include_all: bool,
        errors: int,
        output_format: str | None,
    ) -> None:
        """Show module health status. Optionally specify a module ID for details."""
        fmt = resolve_format(output_format)
        try:
            if module_id:
                result = _call_system_module(
                    executor,
                    "system.health.module",
                    {"module_id": module_id, "error_limit": errors},
                )
                if fmt == "json" or not sys.stdout.isatty():
                    click.echo(json.dumps(result, indent=2, default=str))
                else:
                    _format_health_module_tty(result)
            else:
                result = _call_system_module(
                    executor,
                    "system.health.summary",
                    {"error_rate_threshold": threshold, "include_healthy": include_all},
                )
                if fmt == "json" or not sys.stdout.isatty():
                    click.echo(json.dumps(result, indent=2, default=str))
                else:
                    _format_health_summary_tty(result)
        except Exception as e:
            _exit_on_system_error(e)

    _ = health_cmd


def register_usage_command(apcli_group: click.Group, executor: Any) -> None:
    """Register the ``usage`` subcommand on the given group."""

    @apcli_group.command("usage")
    @click.argument("module_id", required=False)
    @click.option("--period", default="24h", help="Time window: 1h, 24h, 7d, 30d (default: 24h).")
    @click.option(
        "--format", "output_format", type=click.Choice(["table", "json", "csv", "yaml", "jsonl"]), default=None
    )
    def usage_cmd(module_id: str | None, period: str, output_format: str | None) -> None:
        """Show module usage statistics. Optionally specify a module ID for details."""
        fmt = resolve_format(output_format)
        try:
            if module_id:
                result = _call_system_module(
                    executor,
                    "system.usage.module",
                    {"module_id": module_id, "period": period},
                )
            else:
                result = _call_system_module(
                    executor,
                    "system.usage.summary",
                    {"period": period},
                )
            if fmt == "json" or not sys.stdout.isatty():
                click.echo(json.dumps(result, indent=2, default=str))
            elif module_id:
                format_exec_result(result, fmt)
            else:
                _format_usage_summary_tty(result)
        except Exception as e:
            _exit_on_system_error(e)

    _ = usage_cmd


def register_enable_command(apcli_group: click.Group, executor: Any) -> None:
    """Register the ``enable`` subcommand on the given group."""

    @apcli_group.command("enable")
    @click.argument("module_id")
    @click.option("--reason", required=True, help="Reason for enabling (recorded in module audit trail).")
    @click.option("--yes", "-y", is_flag=True, default=False, help="Skip approval prompt.")
    @click.option(
        "--format", "output_format", type=click.Choice(["table", "json", "csv", "yaml", "jsonl"]), default=None
    )
    def enable_cmd(module_id: str, reason: str, yes: bool, output_format: str | None) -> None:
        """Enable a disabled module at runtime."""
        import time

        _check_system_approval(executor, "system.control.toggle_feature", yes)
        fmt = resolve_format(output_format)
        audit_start = time.monotonic()
        try:
            result = _call_system_module(
                executor,
                "system.control.toggle_feature",
                {"module_id": module_id, "enabled": True, "reason": reason},
            )
            duration_ms = int((time.monotonic() - audit_start) * 1000)
            _al = _cli_module._audit_logger
            if _al is not None:
                _al.log_execution(
                    "system.control.toggle_feature",
                    {"module_id": module_id, "enabled": True},
                    "success",
                    0,
                    duration_ms,
                )
            if fmt == "json" or not sys.stdout.isatty():
                click.echo(json.dumps(result, indent=2, default=str))
            else:
                click.echo(f"Module '{module_id}' enabled.\n  Reason: {reason}")
        except Exception as e:
            duration_ms = int((time.monotonic() - audit_start) * 1000)
            _al = _cli_module._audit_logger
            if _al is not None:
                _al.log_execution(
                    "system.control.toggle_feature",
                    {"module_id": module_id, "enabled": True},
                    "error",
                    exit_code_for_error(e),
                    duration_ms,
                )
            _exit_on_system_error(e)

    _ = enable_cmd


def register_disable_command(apcli_group: click.Group, executor: Any) -> None:
    """Register the ``disable`` subcommand on the given group."""

    @apcli_group.command("disable")
    @click.argument("module_id")
    @click.option("--reason", required=True, help="Reason for disabling (recorded in module audit trail).")
    @click.option("--yes", "-y", is_flag=True, default=False, help="Skip approval prompt.")
    @click.option(
        "--format", "output_format", type=click.Choice(["table", "json", "csv", "yaml", "jsonl"]), default=None
    )
    def disable_cmd(module_id: str, reason: str, yes: bool, output_format: str | None) -> None:
        """Disable a module at runtime (calls are rejected until re-enabled)."""
        import time

        _check_system_approval(executor, "system.control.toggle_feature", yes)
        fmt = resolve_format(output_format)
        audit_start = time.monotonic()
        try:
            result = _call_system_module(
                executor,
                "system.control.toggle_feature",
                {"module_id": module_id, "enabled": False, "reason": reason},
            )
            duration_ms = int((time.monotonic() - audit_start) * 1000)
            _al = _cli_module._audit_logger
            if _al is not None:
                _al.log_execution(
                    "system.control.toggle_feature",
                    {"module_id": module_id, "enabled": False},
                    "success",
                    0,
                    duration_ms,
                )
            if fmt == "json" or not sys.stdout.isatty():
                click.echo(json.dumps(result, indent=2, default=str))
            else:
                click.echo(f"Module '{module_id}' disabled.\n  Reason: {reason}")
        except Exception as e:
            duration_ms = int((time.monotonic() - audit_start) * 1000)
            _al = _cli_module._audit_logger
            if _al is not None:
                _al.log_execution(
                    "system.control.toggle_feature",
                    {"module_id": module_id, "enabled": False},
                    "error",
                    exit_code_for_error(e),
                    duration_ms,
                )
            _exit_on_system_error(e)

    _ = disable_cmd


def register_reload_command(apcli_group: click.Group, executor: Any) -> None:
    """Register the ``reload`` subcommand on the given group."""

    @apcli_group.command("reload")
    @click.argument("module_id")
    @click.option("--reason", required=True, help="Reason for reload (recorded in module audit trail).")
    @click.option("--yes", "-y", is_flag=True, default=False, help="Skip approval prompt.")
    @click.option(
        "--format", "output_format", type=click.Choice(["table", "json", "csv", "yaml", "jsonl"]), default=None
    )
    def reload_cmd(module_id: str, reason: str, yes: bool, output_format: str | None) -> None:
        """Hot-reload a module from disk."""
        import time

        _check_system_approval(executor, "system.control.reload_module", yes)
        fmt = resolve_format(output_format)
        audit_start = time.monotonic()
        try:
            result = _call_system_module(
                executor,
                "system.control.reload_module",
                {"module_id": module_id, "reason": reason},
            )
            duration_ms = int((time.monotonic() - audit_start) * 1000)
            _al = _cli_module._audit_logger
            if _al is not None:
                _al.log_execution("system.control.reload_module", {"module_id": module_id}, "success", 0, duration_ms)
            if fmt == "json" or not sys.stdout.isatty():
                click.echo(json.dumps(result, indent=2, default=str))
            else:
                prev = result.get("previous_version", "?")
                new = result.get("new_version", "?")
                dur = result.get("reload_duration_ms", "?")
                click.echo(f"Module '{module_id}' reloaded.")
                click.echo(f"  Version: {prev} -> {new}")
                click.echo(f"  Duration: {dur}ms")
        except Exception as e:
            duration_ms = int((time.monotonic() - audit_start) * 1000)
            _al = _cli_module._audit_logger
            if _al is not None:
                _al.log_execution(
                    "system.control.reload_module",
                    {"module_id": module_id},
                    "error",
                    exit_code_for_error(e),
                    duration_ms,
                )
            _exit_on_system_error(e)

    _ = reload_cmd


def register_config_command(apcli_group: click.Group, executor: Any) -> None:
    """Register the ``config`` subgroup (``get`` / ``set``) on the given group."""

    @apcli_group.group("config")
    def config_group() -> None:
        """Read or update runtime configuration."""

    @config_group.command("get")
    @click.argument("key")
    def config_get_cmd(key: str) -> None:
        """Read a configuration value by dot-path key."""
        try:
            from apcore import Config

            value = Config().get(key)
            click.echo(f"{key} = {value!r}")
        except Exception as e:
            _exit_on_system_error(e)

    @config_group.command("set")
    @click.argument("key")
    @click.argument("value")
    @click.option("--reason", required=True, help="Reason for config change (recorded in module audit trail).")
    @click.option("-y", "--yes", "auto_approve", is_flag=True, default=False, help="Auto-approve.")
    @click.option(
        "--format", "output_format", type=click.Choice(["table", "json", "csv", "yaml", "jsonl"]), default=None
    )
    def config_set_cmd(key: str, value: str, reason: str, auto_approve: bool, output_format: str | None) -> None:
        """Update a runtime configuration value (requires approval)."""
        import time

        _check_system_approval(executor, "system.control.update_config", auto_approve)
        fmt = resolve_format(output_format)
        try:
            parsed_value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            parsed_value = value

        audit_start = time.monotonic()
        try:
            result = _call_system_module(
                executor,
                "system.control.update_config",
                {"key": key, "value": parsed_value, "reason": reason},
            )
            duration_ms = int((time.monotonic() - audit_start) * 1000)
            _al = _cli_module._audit_logger
            if _al is not None:
                _al.log_execution("system.control.update_config", {"key": key}, "success", 0, duration_ms)
            if fmt == "json" or not sys.stdout.isatty():
                click.echo(json.dumps(result, indent=2, default=str))
            else:
                old = result.get("old_value", "?")
                new = result.get("new_value", "?")
                click.echo(f"Config updated: {key}")
                click.echo(f"  {old!r} -> {new!r}")
                click.echo(f"  Reason: {reason}")
        except Exception as e:
            duration_ms = int((time.monotonic() - audit_start) * 1000)
            _al = _cli_module._audit_logger
            if _al is not None:
                _al.log_execution(
                    "system.control.update_config", {"key": key}, "error", exit_code_for_error(e), duration_ms
                )
            _exit_on_system_error(e)

    _ = config_get_cmd
    _ = config_set_cmd


# ---------------------------------------------------------------------------
# Back-compat batched registrar (pre-v0.7 call sites)
# ---------------------------------------------------------------------------


def register_system_commands(cli: click.Group, executor: Any) -> None:
    """Legacy wrapper — registers all six system subcommands on the given group.

    Probes the executor for ``system.*`` modules and silently skips
    registration if they are unavailable, matching the pre-FE-13 behavior.
    Post-FE-13 canonical wiring attaches each subcommand individually to
    the ``apcli`` group via the :mod:`apcore_cli.factory` dispatcher.
    """
    if not _system_modules_available(executor):
        logger.debug("System modules not available; skipping system command registration.")
        return

    register_health_command(cli, executor)
    register_usage_command(cli, executor)
    register_enable_command(cli, executor)
    register_disable_command(cli, executor)
    register_reload_command(cli, executor)
    register_config_command(cli, executor)
