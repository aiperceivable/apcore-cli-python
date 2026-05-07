"""Discovery commands — list, describe, exec, validate (FE-04, FE-11, FE-13).

FE-13 §4.9 split the batched ``register_discovery_commands`` into four
per-subcommand registrars (``register_list_command``,
``register_describe_command``, ``register_exec_command``,
``register_validate_command``). The per-command shape is required for
apcli include/exclude filtering — the factory dispatcher decides at
registration time whether each subcommand should be attached to the
``apcli`` group or skipped.

The legacy :func:`register_discovery_commands` remains as a thin wrapper
so pre-v0.7 test fixtures and call sites keep working.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

import click

import apcore_cli.cli as _cli_module
from apcore_cli.cli import (
    _first_failed_exit_code,
    collect_input,
    format_preflight_result,
    validate_module_id,
)
from apcore_cli.display_helpers import get_cli_display_fields
from apcore_cli.output import (
    format_exec_result,
    format_grouped_module_list,
    format_module_detail,
    format_module_list,
    resolve_format,
)

logger = logging.getLogger("apcore_cli.discovery")

_TAG_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


def _validate_tag(tag: str) -> None:
    """Validate tag format."""
    if not _TAG_PATTERN.match(tag):
        click.echo(
            f"Error: Invalid tag format: '{tag}'. Tags must match [a-z][a-z0-9_-]*.",
            err=True,
        )
        sys.exit(2)


def _resolve_group_for_display(descriptor: Any) -> tuple[str | None, str]:
    """Resolve group name and command name for display — delegates to GroupedModuleGroup."""
    from apcore_cli.cli import GroupedModuleGroup

    module_id = getattr(descriptor, "module_id", "") or ""
    return GroupedModuleGroup._resolve_group(module_id, descriptor)


# ---------------------------------------------------------------------------
# Per-subcommand registrars (FE-13 §4.9)
# ---------------------------------------------------------------------------


def register_list_command(
    apcli_group: click.Group,
    registry: Any,
    exposure_filter: Any | None = None,
) -> None:
    """Register the ``list`` subcommand on the given group.

    Accepts the apcli Click group (post-FE-13 canonical attachment point)
    or any other Click group for back-compat test fixtures.
    """
    from apcore_cli.exposure import ExposureFilter

    if exposure_filter is None:
        exposure_filter = ExposureFilter()

    @apcli_group.command("list")
    @click.option("--tag", multiple=True, help="Filter modules by tag (AND logic). Repeatable.")
    @click.option("--flat", is_flag=True, default=False, help="Show flat list (no grouping).")
    @click.option(
        "--format",
        "output_format",
        type=click.Choice(["table", "json", "csv", "yaml", "jsonl"]),
        default=None,
        help="Output format. Default: table (TTY) or json (non-TTY).",
    )
    @click.option("--search", "-s", default=None, help="Filter by substring match on ID and description.")
    @click.option(
        "--status",
        type=click.Choice(["enabled", "disabled", "all"]),
        default="enabled",
        help="Filter by module status. Default: enabled.",
    )
    @click.option(
        "--annotation",
        "-a",
        multiple=True,
        type=click.Choice(
            [
                "destructive",
                "requires-approval",
                "readonly",
                "streaming",
                "cacheable",
                "idempotent",
                # apcore >= 0.19.0 ModuleAnnotations additions.
                "paginated",
            ]
        ),
        help="Filter by annotation flag (AND logic). Repeatable.",
    )
    @click.option(
        "--sort",
        type=click.Choice(["id", "calls", "errors", "latency"]),
        default="id",
        help="Sort order. Default: id.",
    )
    @click.option("--reverse", is_flag=True, default=False, help="Reverse sort order.")
    @click.option("--deprecated", is_flag=True, default=False, help="Include deprecated modules.")
    @click.option("--deps", is_flag=True, default=False, help="Show dependency count column.")
    @click.option(
        "--exposure",
        type=click.Choice(["exposed", "hidden", "all"]),
        default="exposed",
        help="Filter by exposure status. Default: exposed.",
    )
    @click.pass_context
    def list_cmd(
        ctx: click.Context,
        tag: tuple[str, ...],
        flat: bool,
        output_format: str | None,
        search: str | None,
        status: str,
        annotation: tuple[str, ...],
        sort: str,
        reverse: bool,
        deprecated: bool,
        deps: bool,
        exposure: str,
    ) -> None:
        """List available modules in the registry."""
        for t in tag:
            _validate_tag(t)

        # Prefer a filter pushed into ctx.obj (factory.py wires it there);
        # fall back to the closure-captured default (mode=all) for tests.
        obj = (ctx.obj or {}) if ctx else {}
        ctx_filter = obj.get("exposure_filter") if isinstance(obj, dict) else None
        active_filter = ctx_filter if ctx_filter is not None else exposure_filter

        modules = []
        for mid in registry.list():
            mdef = registry.get_definition(mid)
            if mdef is not None:
                modules.append(mdef)

        if tag:
            filter_tags = set(tag)
            modules = [m for m in modules if filter_tags.issubset(set(getattr(m, "tags", [])))]

        if search:
            query = search.lower()
            modules = [
                m
                for m in modules
                if query in (getattr(m, "module_id", "") or "").lower()
                or query in (getattr(m, "description", "") or "").lower()
            ]

        if status == "enabled":
            modules = [m for m in modules if getattr(m, "enabled", None) is not False]
        elif status == "disabled":
            modules = [m for m in modules if getattr(m, "enabled", None) is False]

        if not deprecated:
            modules = [m for m in modules if getattr(m, "deprecated", False) is not True]

        if annotation:
            _ann_map = {
                "destructive": "destructive",
                "requires-approval": "requires_approval",
                "readonly": "readonly",
                "streaming": "streaming",
                "cacheable": "cacheable",
                "idempotent": "idempotent",
                "paginated": "paginated",
            }
            for ann_flag in annotation:
                attr = _ann_map.get(ann_flag, ann_flag)
                modules = [m for m in modules if getattr(getattr(m, "annotations", None), attr, False) is True]

        if exposure == "exposed":
            modules = [m for m in modules if active_filter.is_exposed(getattr(m, "module_id", ""))]
        elif exposure == "hidden":
            modules = [m for m in modules if not active_filter.is_exposed(getattr(m, "module_id", ""))]

        if sort in ("calls", "errors", "latency"):
            from apcore_cli.system_usage import sort_modules_by_usage

            modules, used_data = sort_modules_by_usage(modules, sort, reverse=reverse)
            if not used_data:
                # Issue #17 AC: visible message (not just logger.warning) when
                # the audit log has no matching entries — fresh installs, or
                # when the user asked for usage-based sorting before the log
                # has accumulated data in the period window.
                click.echo(
                    f"note: no usage data available for --sort {sort}; "
                    "sorted by id. Run some modules first to populate "
                    "~/.apcore-cli/audit.jsonl.",
                    err=True,
                )
        else:
            modules.sort(key=lambda m: getattr(m, "module_id", ""), reverse=reverse)

        fmt = resolve_format(output_format)
        show_exposure_col = exposure == "all"

        if flat or fmt in ("json", "csv", "yaml", "jsonl"):
            format_module_list(
                modules,
                fmt,
                filter_tags=tag,
                show_deps=deps,
                exposure_filter=active_filter if show_exposure_col else None,
            )
        else:
            grouped: dict[str | None, list[tuple[str, str, list[str]]]] = {}
            for m in modules:
                group_name, cmd_name = _resolve_group_for_display(m)
                _, desc, tags_val = get_cli_display_fields(m)
                grouped.setdefault(group_name, []).append((cmd_name, desc, tags_val))
            format_grouped_module_list(grouped, filter_tags=tag)

    _ = list_cmd  # silence unused-var checker


def register_describe_command(apcli_group: click.Group, registry: Any) -> None:
    """Register the ``describe`` subcommand on the given group."""

    @apcli_group.command("describe")
    @click.argument("module_id")
    @click.option(
        "--format",
        "output_format",
        type=click.Choice(["table", "json"]),
        default=None,
        help="Output format. Default: table (TTY) or json (non-TTY).",
    )
    def describe_cmd(module_id: str, output_format: str | None) -> None:
        """Show metadata, schema, and annotations for a module."""
        validate_module_id(module_id)

        module_def = registry.get_definition(module_id)
        if module_def is None:
            click.echo(f"Error: Module '{module_id}' not found.", err=True)
            sys.exit(44)

        fmt = resolve_format(output_format)
        format_module_detail(module_def, fmt)

    _ = describe_cmd


def register_exec_command(
    apcli_group: click.Group,
    registry: Any,
    executor: Any,
) -> None:
    """Register the generic ``exec`` subcommand on the apcli group (FE-13).

    Dispatch shape: ``apcli exec <module-id> [--input JSON] [--format fmt]``.
    Unlike the per-module commands built by :func:`build_module_command`, this
    command does not derive options from the module's input schema — inputs
    are passed as a JSON object via ``--input``.
    """
    from apcore_cli.approval import check_approval
    from apcore_cli.cli import _ERROR_CODE_MAP, _emit_error_tty

    @apcli_group.command("exec")
    @click.argument("module_id")
    @click.option(
        "--format",
        "output_format",
        type=click.Choice(["json", "table", "csv", "yaml", "jsonl"]),
        default=None,
        help="Output format.",
    )
    @click.option("--fields", default=None, help="Comma-separated dot-paths to select from the result.")
    @click.option(
        "--input",
        "stdin_input",
        default=None,
        help="JSON object passed as input to the module. Use '-' to read JSON from stdin.",
    )
    @click.option("-y", "--yes", "auto_approve", is_flag=True, default=False, help="Auto-approve.")
    @click.option(
        "--approval-timeout",
        type=int,
        default=None,
        help="Seconds to wait for interactive approval.",
    )
    @click.option(
        "--sandbox",
        is_flag=True,
        default=False,
        help="Run module in an isolated subprocess with restricted filesystem and env access.",
    )
    @click.option("--strategy", default=None, help="Execution strategy (standard, parallel, sequential, etc.).")
    @click.option("--trace", is_flag=True, default=False, help="Enable pipeline trace output.")
    @click.option("--dry-run", "dry_run", is_flag=True, default=False, help="Validate inputs without executing.")
    @click.option("--stream", is_flag=True, default=False, help="Stream output as JSONL.")
    def exec_cmd(
        module_id: str,
        output_format: str | None,
        fields: str | None,
        stdin_input: str | None,
        auto_approve: bool,
        approval_timeout: int | None,
        sandbox: bool,
        strategy: str | None,
        trace: bool,
        dry_run: bool,
        stream: bool,
    ) -> None:
        """Execute a module by ID with JSON input."""
        validate_module_id(module_id)

        module_def = registry.get_definition(module_id)
        if module_def is None:
            click.echo(f"Error: Module '{module_id}' not found.", err=True)
            sys.exit(44)

        # Distinguish stdin marker from inline JSON literal.
        merged: dict[str, Any] = {}
        if stdin_input == "-":
            merged = collect_input("-", {}, False)
        elif stdin_input is not None:
            try:
                parsed = json.loads(stdin_input)
            except json.JSONDecodeError as e:
                click.echo(f"Error: --input is not valid JSON: {e}", err=True)
                sys.exit(2)
            if not isinstance(parsed, dict):
                click.echo("Error: --input JSON must be an object.", err=True)
                sys.exit(2)
            merged = parsed

        import time

        from apcore_cli.security.sandbox import Sandbox

        audit_start = time.monotonic()
        try:
            timeout = approval_timeout if approval_timeout is not None else 60
            check_approval(module_def, auto_approve=auto_approve, timeout=timeout)

            if dry_run:
                preflight = executor.validate(module_id, merged)
                format_preflight_result(preflight, output_format)
                return

            if (trace or strategy) and hasattr(executor, "call_with_trace"):
                result, _trace_data = executor.call_with_trace(
                    module_id,
                    merged,
                    strategy=strategy,
                )
            else:
                result = Sandbox(enabled=sandbox).execute(module_id, merged, executor)

            # Format output FIRST (canonical order: format → audit on success)
            fmt = resolve_format(output_format)
            format_exec_result(result, fmt, fields)
            duration_ms = int((time.monotonic() - audit_start) * 1000)
            _al = _cli_module._audit_logger
            if _al is not None:
                _al.log_execution(module_id, merged, "success", 0, duration_ms)
        except Exception as e:
            code = getattr(e, "code", None)
            exit_code = _ERROR_CODE_MAP.get(code, 1) if isinstance(code, str) else 1
            duration_ms = int((time.monotonic() - audit_start) * 1000)
            _al = _cli_module._audit_logger
            if _al is not None:
                _al.log_execution(module_id, merged, "error", exit_code, duration_ms)
            _emit_error_tty(e, exit_code)
            sys.exit(exit_code)

    _ = exec_cmd


def register_validate_command(apcli_group: click.Group, registry: Any, executor: Any) -> None:
    """Register the ``validate`` subcommand on the given group."""
    from apcore_cli.cli import _ERROR_CODE_MAP, _emit_error_tty

    @apcli_group.command("validate")
    @click.argument("module_id")
    @click.option("--input", "stdin_input", default=None, help="JSON input file or '-' for stdin.")
    @click.option(
        "--format",
        "output_format",
        type=click.Choice(["table", "json"]),
        default=None,
        help="Output format.",
    )
    def validate_cmd(module_id: str, stdin_input: str | None, output_format: str | None) -> None:
        """Run preflight checks without executing a module."""
        validate_module_id(module_id)

        module_def = registry.get_definition(module_id)
        if module_def is None:
            click.echo(f"Error: Module '{module_id}' not found.", err=True)
            sys.exit(44)

        merged = collect_input(stdin_input, {}, False) if stdin_input else {}
        try:
            preflight = executor.validate(module_id, merged)
            format_preflight_result(preflight, output_format)
        except Exception as e:
            code = getattr(e, "code", None)
            exit_code = _ERROR_CODE_MAP.get(code, 1) if isinstance(code, str) else 1
            _al = _cli_module._audit_logger
            if _al is not None:
                _al.log_execution(module_id, merged, "error", exit_code, 0)
            _emit_error_tty(e, exit_code)
            sys.exit(exit_code)
        sys.exit(0 if preflight.valid else _first_failed_exit_code(preflight))

    _ = validate_cmd


# ---------------------------------------------------------------------------
# Back-compat batched registrar (pre-v0.7 call sites)
# ---------------------------------------------------------------------------


def register_discovery_commands(
    cli: click.Group,
    registry: Any,
    exposure_filter: Any | None = None,
) -> None:
    """Legacy wrapper — delegates to the per-subcommand registrars.

    Pre-FE-13 callers (the original ``register_discovery_commands``) attached
    ``list`` + ``describe`` directly to the root Click group. FE-13 moves
    those under the ``apcli`` group; the new canonical wiring lives in
    :func:`apcore_cli.factory._register_apcli_subcommands`. This shim keeps
    existing tests working by registering ``list`` + ``describe`` on the
    group the caller passes in.
    """
    register_list_command(cli, registry, exposure_filter=exposure_filter)
    register_describe_command(cli, registry)
