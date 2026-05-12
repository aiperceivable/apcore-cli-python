"""Core Dispatcher — CLI entry point and module routing (FE-01)."""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
import jsonschema

from apcore_cli.approval import check_approval
from apcore_cli.builtin_group import RESERVED_GROUP_NAMES as RESERVED_GROUP_NAMES  # noqa: PLC0414
from apcore_cli.display_helpers import get_display as _get_display
from apcore_cli.exit_codes import EXIT_SCHEMA_CIRCULAR_REF, EXIT_SCHEMA_VALIDATION_ERROR
from apcore_cli.output import format_exec_result
from apcore_cli.ref_resolver import (
    CircularRefError,
    MaxDepthExceededError,
    RefResolverError,
    UnresolvableRefError,
    resolve_refs,
)
from apcore_cli.schema_parser import reconvert_enum_values, schema_to_click_options
from apcore_cli.security.sandbox import Sandbox

# FE-13 §11.4: ``BUILTIN_COMMANDS`` was retired in v0.7.0. All apcore-cli-provided
# commands now live under the single reserved ``apcli`` group, so the only
# collision surface is the group name itself (see ``RESERVED_GROUP_NAMES`` above,
# imported from :mod:`apcore_cli.builtin_group`). Stale ``from apcore_cli.cli
# import BUILTIN_COMMANDS`` imports will fail at import time rather than drift
# silently — intentional per spec §11.4.

if TYPE_CHECKING:
    from apcore import Executor, Registry
    from apcore.registry.types import ModuleDescriptor

    from apcore_cli.security.audit import AuditLogger

logger = logging.getLogger("apcore_cli.cli")

# Module-level audit logger, set during CLI init
_audit_logger: AuditLogger | None = None

# Module-level all-options help flag, set during CLI init
_all_options_help: bool = False


def set_verbose_help(verbose: bool) -> None:
    """Set the all-options help flag. When False, built-in options are hidden.

    The parameter name ``verbose`` is kept for backward compatibility with
    existing embedder code. Internally this controls the ``--all-options``
    flag introduced in FR-DISP-007.
    """
    global _all_options_help
    _all_options_help = verbose


# Module-level docs URL, set by downstream projects
_docs_url: str | None = None


def set_docs_url(url: str | None) -> None:
    """Set the base URL for online documentation links in help and man pages.

    Pass None to disable. Command-level help appends ``/commands/{name}``
    automatically.

    Example::

        set_docs_url("https://docs.apcore.dev/cli")
    """
    global _docs_url
    _docs_url = url


def set_audit_logger(audit_logger: AuditLogger | None) -> None:
    """Set the global audit logger instance. Pass None to clear."""
    global _audit_logger
    _audit_logger = audit_logger


class _LazyGroup(click.Group):
    """Click Group for a single command group — lazily builds subcommands."""

    def __init__(
        self,
        members: dict[str, tuple[str, Any]],
        executor: Any,
        help_text_max_length: int = 1000,
        extensions_root: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._members = members  # dict[cmd_name, (module_id, descriptor)]
        self._executor = executor
        self._help_text_max_length = help_text_max_length
        self._extensions_root = extensions_root
        self._cmd_cache: dict[str, click.Command] = {}

    def list_commands(self, ctx: click.Context) -> list[str]:
        return sorted(self._members.keys())

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        if cmd_name in self._cmd_cache:
            return self._cmd_cache[cmd_name]
        entry = self._members.get(cmd_name)
        if entry is None:
            return None
        _, descriptor = entry
        cmd = build_module_command(
            descriptor,
            self._executor,
            help_text_max_length=self._help_text_max_length,
            cmd_name=cmd_name,
            extensions_root=self._extensions_root,
        )
        self._cmd_cache[cmd_name] = cmd
        return cmd


class LazyModuleGroup(click.Group):
    """Custom Click Group that lazily loads apcore modules as subcommands."""

    def __init__(
        self,
        registry: Registry,
        executor: Executor,
        help_text_max_length: int = 1000,
        extensions_root: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._registry = registry
        self._executor = executor
        self._help_text_max_length = help_text_max_length
        self._extensions_root = extensions_root
        self._module_cache: dict[str, click.Command] = {}
        # alias → canonical module_id (populated lazily)
        self._alias_map: dict[str, str] = {}
        # module_id → descriptor cache (populated during alias map build)
        self._descriptor_cache: dict[str, Any] = {}
        self._alias_map_built: bool = False

    def _build_alias_map(self) -> None:
        """Build alias→module_id map from display overlay metadata."""
        if self._alias_map_built:
            return
        try:
            for module_id in self._registry.list():
                descriptor = self._registry.get_definition(module_id)
                if descriptor is None:
                    continue
                self._descriptor_cache[module_id] = descriptor
                display = _get_display(descriptor)
                cli_alias: str | None = (display.get("cli") or {}).get("alias")
                if cli_alias and cli_alias != module_id:
                    self._alias_map[cli_alias] = module_id
            self._alias_map_built = True
        except Exception:
            logger.warning("Failed to build alias map from registry")

    def list_commands(self, ctx: click.Context) -> list[str]:
        # Root-level commands are whatever the factory has registered
        # (post-FE-13 v0.8: the `apcli` group + any caller extras — the
        # legacy root-level shims were retired). Module-derived commands
        # live on top of those.
        registered = [name for name, cmd in self.commands.items() if not cmd.hidden]
        try:
            self._build_alias_map()
            reverse: dict[str, str] = {v: k for k, v in self._alias_map.items()}
            module_ids = self._registry.list()
            names = [reverse.get(mid, mid) for mid in module_ids]
        except Exception:
            logger.warning("Failed to list modules from registry")
            names = []
        return sorted(set(registered + names))

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        # Check built-in commands first
        if cmd_name in self.commands:
            return self.commands[cmd_name]

        # Check cache
        if cmd_name in self._module_cache:
            return self._module_cache[cmd_name]

        # Resolve alias → canonical module_id
        self._build_alias_map()
        module_id = self._alias_map.get(cmd_name, cmd_name)

        # Look up in descriptor cache (populated during alias map build) or registry
        module_def = self._descriptor_cache.get(module_id)
        if module_def is None:
            module_def = self._registry.get_definition(module_id)
        if module_def is None:
            return None

        cmd = build_module_command(
            module_def,
            self._executor,
            help_text_max_length=self._help_text_max_length,
            cmd_name=cmd_name,
            extensions_root=self._extensions_root,
        )
        self._module_cache[cmd_name] = cmd
        return cmd


class GroupedModuleGroup(LazyModuleGroup):
    """Extended LazyModuleGroup that organises modules into named groups."""

    def __init__(self, **kwargs: Any) -> None:
        from apcore_cli.exposure import ExposureFilter

        exposure_filter = kwargs.pop("exposure_filter", None)
        super().__init__(**kwargs)
        self._exposure_filter: ExposureFilter = exposure_filter or ExposureFilter()
        self._group_map: dict[str, dict[str, tuple[str, Any]]] = {}
        self._top_level_modules: dict[str, tuple[str, Any]] = {}
        self._group_cache: dict[str, _LazyGroup] = {}
        self._group_map_built: bool = False
        # Names that business modules MUST NOT claim. Default mirrors the
        # static :data:`RESERVED_GROUP_NAMES` constant (= {"apcli"}); when
        # ``create_cli`` is invoked with ``builtin_group_name`` overridden,
        # the factory replaces this with ``frozenset({apcli_cfg.name})``
        # so the collision check fires against the renamed group too.
        self._reserved_group_names: frozenset[str] = RESERVED_GROUP_NAMES

    @staticmethod
    def _resolve_group(module_id: str, descriptor: Any) -> tuple[str | None, str]:
        """Determine (group, command_name) for a module from its display overlay."""
        if not module_id:
            logger.warning("Empty module_id encountered in _resolve_group")
            return (None, "")

        display = _get_display(descriptor)
        cli_display = display.get("cli") or {}
        explicit_group = cli_display.get("group")

        if isinstance(explicit_group, str) and explicit_group != "":
            return (explicit_group, cli_display.get("alias") or module_id)
        if explicit_group == "":
            return (None, cli_display.get("alias") or module_id)

        cli_name = cli_display.get("alias") or module_id
        if "." in cli_name:
            group, _, cmd = cli_name.partition(".")
            return (group, cmd)
        return (None, cli_name)

    def _build_group_map(self) -> None:
        """Build the group map from registry modules.

        Applies the exposure filter before building groups — hidden modules are
        excluded from --help and tab-completion but remain invocable via exec.

        FE-13 §4.10: rejects modules whose explicit group name, dotted-ID
        auto-extracted group, or top-level CLI name is ``apcli`` (reserved).
        Rejection is a hard :class:`click.UsageError` — exit 2 at import time.
        """
        if self._group_map_built:
            return
        try:
            self._build_alias_map()
            for module_id in self._registry.list():
                descriptor = self._descriptor_cache.get(module_id)
                if descriptor is None:
                    continue
                # Skip modules that should not appear as CLI commands (FE-12).
                if not self._exposure_filter.is_exposed(module_id):
                    continue
                group, cmd = self._resolve_group(module_id, descriptor)
                # FE-13: reserved-name enforcement covers all three collision
                # shapes — explicit group, auto-grouped from dotted module_id,
                # and top-level CLI name. Raised as UsageError so Click exits
                # with code 2 per spec §7 FR-13-09. UsageError must propagate
                # past the below try/except so test fixtures and live CLI see
                # the intended failure path.
                if group is not None and group in self._reserved_group_names:
                    raise click.UsageError(
                        f"Module '{module_id}': group name '{group}' is reserved. "
                        f"Use a different CLI alias or set display.cli.group to another value."
                    )
                if group is None and cmd in self._reserved_group_names:
                    raise click.UsageError(
                        f"Module '{module_id}': top-level CLI name '{cmd}' is reserved. Use a different CLI alias."
                    )
                if group is None:
                    self._top_level_modules[cmd] = (module_id, descriptor)
                elif not re.fullmatch(r"[a-z][a-z0-9_-]*", group):
                    logger.warning(
                        "Module '%s': group name '%s' is not shell-safe — treating as top-level.",
                        module_id,
                        group,
                    )
                    self._top_level_modules[cmd] = (module_id, descriptor)
                else:
                    self._group_map.setdefault(group, {})[cmd] = (module_id, descriptor)
            self._group_map_built = True
        except click.UsageError:
            raise
        except Exception:
            # Transient registry errors — allow retry on next call.
            logger.warning("Failed to build group map")

    def list_commands(self, ctx: click.Context) -> list[str]:
        self._build_group_map()
        # Registered root commands (post-FE-13: `apcli` group + deprecation
        # shims + caller extras). Hidden entries (e.g. the apcli group when
        # mode=none) are filtered out so tab-completion matches --help.
        registered = [name for name, cmd in self.commands.items() if not cmd.hidden]
        group_names = list(self._group_map.keys())
        top_names = list(self._top_level_modules.keys())
        return sorted(set(registered + group_names + top_names))

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        # Check built-in commands first
        if cmd_name in self.commands:
            return self.commands[cmd_name]

        self._build_group_map()

        # Check group cache
        if cmd_name in self._group_cache:
            return self._group_cache[cmd_name]

        # Check if it's a group
        if cmd_name in self._group_map:
            grp = _LazyGroup(
                members=self._group_map[cmd_name],
                executor=self._executor,
                help_text_max_length=self._help_text_max_length,
                extensions_root=self._extensions_root,
                name=cmd_name,
            )
            self._group_cache[cmd_name] = grp
            return grp

        # Check top-level modules
        if cmd_name in self._top_level_modules:
            if cmd_name in self._module_cache:
                return self._module_cache[cmd_name]
            _, descriptor = self._top_level_modules[cmd_name]
            cmd = build_module_command(
                descriptor,
                self._executor,
                help_text_max_length=self._help_text_max_length,
                cmd_name=cmd_name,
                extensions_root=self._extensions_root,
            )
            self._module_cache[cmd_name] = cmd
            return cmd

        return None

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        self._build_group_map()
        self.format_usage(ctx, formatter)
        if self.help:
            formatter.write_paragraph()
            formatter.write(self.help)

        # Options section
        opts = []
        for p in self.get_params(ctx):
            rv = p.get_help_record(ctx)
            if rv is not None:
                opts.append(rv)
        if opts:
            with formatter.section("Options"):
                formatter.write_dl(opts)

        # Commands section. Post-FE-13, all apcore-cli built-ins live under
        # the `apcli` group — at root we only show non-hidden registered
        # commands (the apcli group itself when visible, help, and any
        # user-added extras via extra_commands / add_command).
        cmd_records = []
        for name in sorted(self.commands):
            cmd = self.commands[name]
            if cmd is None or cmd.hidden:
                continue
            cmd_records.append((name, cmd.get_short_help_str()))
        if cmd_records:
            with formatter.section("Commands"):
                formatter.write_dl(cmd_records)

        # Modules section (top-level)
        if self._top_level_modules:
            module_records = []
            for name in sorted(self._top_level_modules.keys()):
                _, descriptor = self._top_level_modules[name]
                desc = getattr(descriptor, "description", "") or ""
                module_records.append((name, desc))
            with formatter.section("Modules"):
                formatter.write_dl(module_records)

        # Groups section (business-module-derived groups only — the `apcli`
        # reserved group was rejected in _build_group_map, so filtering here
        # is defensive rather than load-bearing).
        if self._group_map:
            group_records = []
            for group_name in sorted(self._group_map.keys()):
                count = len(self._group_map[group_name])
                suffix = "s" if count != 1 else ""
                group_records.append((group_name, f"({count} command{suffix})"))
            if group_records:
                with formatter.section("Groups"):
                    formatter.write_dl(group_records)

        # Footer hints for discoverability
        formatter.write_paragraph()
        formatter.write(
            "Use --help --all-options to show all options (including built-in options).\n"
            "Use --help --man to display a formatted man page."
        )


# Error code mapping from apcore error codes to CLI exit codes
_ERROR_CODE_MAP = {
    "MODULE_NOT_FOUND": 44,
    "MODULE_LOAD_ERROR": 44,
    "MODULE_DISABLED": 44,
    "SCHEMA_VALIDATION_ERROR": 45,
    "SCHEMA_CIRCULAR_REF": 48,
    "APPROVAL_DENIED": 46,
    "APPROVAL_TIMEOUT": 46,
    "APPROVAL_PENDING": 46,
    "CONFIG_NOT_FOUND": 47,
    "CONFIG_INVALID": 47,
    "MODULE_EXECUTE_ERROR": 1,
    "MODULE_TIMEOUT": 1,
    "ACL_DENIED": 77,
    # Config Bus errors (apcore >= 0.15.0)
    "CONFIG_NAMESPACE_RESERVED": 78,
    "CONFIG_NAMESPACE_DUPLICATE": 78,
    "CONFIG_ENV_PREFIX_CONFLICT": 78,
    "CONFIG_ENV_MAP_CONFLICT": 78,
    "CONFIG_MOUNT_ERROR": 66,
    "CONFIG_BIND_ERROR": 65,
    "ERROR_FORMATTER_DUPLICATE": 70,
}


# D9-005: format_preflight_result and _first_failed_exit_code moved to
# apcore_cli.validate to mirror apcore-cli-rust/src/validate.rs split.
# Re-imported here for back-compat with legacy callers that did
# ``from apcore_cli.cli import format_preflight_result``.
from apcore_cli.validate import (  # noqa: E402
    first_failed_exit_code as _first_failed_exit_code,
)
from apcore_cli.validate import (  # noqa: E402
    format_preflight_result,
)


def _emit_error_json(e: Exception, exit_code: int) -> None:
    """Emit structured JSON error to stderr for AI agents."""
    code = getattr(e, "code", None)
    payload: dict[str, Any] = {
        "error": True,
        "code": code or "UNKNOWN",
        "message": str(e),
        "exit_code": exit_code,
    }
    for field in ("details", "suggestion", "ai_guidance", "retryable", "user_fixable"):
        val = getattr(e, field, None)
        if val is not None:
            payload[field] = val
    click.echo(json.dumps(payload, default=str), err=True)


def _emit_error_tty(e: Exception, exit_code: int) -> None:
    """Emit human-readable error to stderr with guidance fields."""
    code = getattr(e, "code", None)
    header = f"Error [{code}]: {e}" if code else f"Error: {e}"
    click.echo(header, err=True)

    details = getattr(e, "details", None)
    if details and isinstance(details, dict):
        click.echo("", err=True)
        click.echo("  Details:", err=True)
        for k, v in details.items():
            click.echo(f"    {k}: {v}", err=True)

    suggestion = getattr(e, "suggestion", None)
    if suggestion:
        click.echo(f"\n  Suggestion: {suggestion}", err=True)

    retryable = getattr(e, "retryable", None)
    if retryable is not None:
        label = "Yes" if retryable else "No (same input will fail again)"
        click.echo(f"  Retryable: {label}", err=True)

    click.echo(f"\n  Exit code: {exit_code}", err=True)


def _get_module_id(module_def: ModuleDescriptor) -> str:
    """Get the canonical module ID, falling back to module_id."""
    cid = getattr(module_def, "canonical_id", None)
    if isinstance(cid, str):
        return cid
    return module_def.module_id


def build_module_command(
    module_def: ModuleDescriptor,
    executor: Executor,
    help_text_max_length: int = 1000,
    cmd_name: str | None = None,
    extensions_root: str | None = None,
) -> click.Command:
    """Build a Click command from an apcore module definition.

    Generates Click options from the module's input_schema, wires up
    STDIN input collection, schema validation, approval gating,
    execution, audit logging, and output formatting.
    """
    # Resolve display overlay fields (§5.13)
    display = _get_display(module_def)
    cli_display = display.get("cli") or {}

    raw_schema = getattr(module_def, "input_schema", None)
    module_id = _get_module_id(module_def)
    # cmd_name is the user-facing name (alias or module_id)
    effective_cmd_name: str = cmd_name or cli_display.get("alias") or module_id
    cmd_help: str = cli_display.get("description") or module_def.description

    # Defensively convert Pydantic model class to dict
    if raw_schema is None:
        input_schema: dict = {}
    elif isinstance(raw_schema, dict):
        input_schema = raw_schema
    elif hasattr(raw_schema, "model_json_schema"):
        # Pydantic v2 BaseModel class
        input_schema = raw_schema.model_json_schema()
    elif hasattr(raw_schema, "schema"):
        # Pydantic v1 BaseModel class
        input_schema = raw_schema.schema()
    else:
        input_schema = {}

    if input_schema.get("properties"):
        try:
            resolved_schema = resolve_refs(input_schema, max_depth=32, module_id=module_id)
        except SystemExit:
            raise
        except (CircularRefError, MaxDepthExceededError) as e:
            # D10-003: translate typed circular/depth errors to the documented
            # exit 48 contract. Mirrors the schema_parser flag-collision path
            # (schema_parser.py:111) and matches Rust/TS exit codes.
            click.echo(f"Error: {e}", err=True)
            sys.exit(EXIT_SCHEMA_CIRCULAR_REF)
        except UnresolvableRefError as e:
            # D10-003: translate unresolvable $ref errors to exit 45.
            click.echo(f"Error: {e}", err=True)
            sys.exit(EXIT_SCHEMA_VALIDATION_ERROR)
        except RefResolverError as e:
            # Unknown subclasses of RefResolverError fall through to the
            # generic schema-validation exit code rather than leaking a
            # traceback. Mirrors TS resolveRefs catch-all.
            click.echo(f"Error: {e}", err=True)
            sys.exit(EXIT_SCHEMA_VALIDATION_ERROR)
        except Exception as e:
            logger.warning("Failed to resolve $refs in schema for '%s', using raw schema: %s", module_id, e)
            resolved_schema = input_schema
    else:
        resolved_schema = input_schema

    try:
        schema_options = schema_to_click_options(resolved_schema, max_help_length=help_text_max_length)
    except ValueError as e:
        click.echo(f"Error: Module '{module_id}' schema error: {e}", err=True)
        sys.exit(2)

    def callback(**kwargs: Any) -> None:
        # Separate built-in options from schema-generated kwargs
        stdin_input = kwargs.pop("input", None)
        auto_approve = kwargs.pop("yes", False)
        large_input = kwargs.pop("large_input", False)
        output_format = kwargs.pop("format", None)
        output_fields = kwargs.pop("fields", None)
        sandbox_flag = kwargs.pop("sandbox", False)
        dry_run = kwargs.pop("dry_run", False)
        trace_flag = kwargs.pop("trace", False)
        stream_flag = kwargs.pop("stream", False)
        strategy_name = kwargs.pop("strategy", None)
        approval_timeout = kwargs.pop("approval_timeout", None) or 60
        approval_token = kwargs.pop("approval_token", None)

        merged: dict[str, Any] = {}
        audit_start = time.monotonic()
        try:
            # 1. Collect and merge input (STDIN + CLI flags)
            merged = collect_input(stdin_input, kwargs, large_input)

            # 2. Reconvert enum values to original types
            merged = reconvert_enum_values(merged, schema_options)

            # -- Dry-run: preflight validation only, no execution --
            if dry_run:
                preflight = executor.validate(module_id, merged)
                format_preflight_result(preflight, output_format)
                # --trace --dry-run: show which pipeline steps would run
                if trace_flag and hasattr(preflight, "checks"):
                    click.echo("\nPipeline preview (dry-run):", err=True)
                    _pure_steps = {
                        "context_creation",
                        "call_chain_guard",
                        "module_lookup",
                        "acl_check",
                        "input_validation",
                    }
                    _all_steps = [
                        "context_creation",
                        "call_chain_guard",
                        "module_lookup",
                        "acl_check",
                        "approval_gate",
                        "middleware_before",
                        "input_validation",
                        "execute",
                        "output_validation",
                        "middleware_after",
                        "return_result",
                    ]
                    for s in _all_steps:
                        if s in _pure_steps:
                            click.echo(f"  \u2713 {s:<24} (pure — would execute)", err=True)
                        else:
                            click.echo(f"  \u25cb {s:<24} (impure — skipped in dry-run)", err=True)
                sys.exit(0 if preflight.valid else _first_failed_exit_code(preflight))

            # 3. Validate against schema (if schema has properties)
            if resolved_schema.get("properties"):
                try:
                    jsonschema.validate(merged, resolved_schema)
                except jsonschema.ValidationError as ve:
                    click.echo(
                        f"Error: Validation failed for '{ve.path}': {ve.message}.",
                        err=True,
                    )
                    sys.exit(45)

            # -- Inject approval token if provided --
            if approval_token:
                merged["_approval_token"] = approval_token

            # 4. Check approval gate
            check_approval(module_def, auto_approve, timeout=approval_timeout)

            # 5. Execute (optionally sandboxed)

            # -- Streaming execution --
            if stream_flag:
                import asyncio

                # Streaming always outputs JSONL; --format table is ignored (spec §3.6.2)
                if output_format == "table":
                    logger.warning("Streaming mode always outputs JSONL; --format table is ignored.")

                annotations = getattr(module_def, "annotations", None)
                is_streaming = getattr(annotations, "streaming", False)
                if not is_streaming:
                    logger.warning(
                        "Module '%s' does not declare streaming support. Falling back to standard execution.",
                        module_id,
                    )

                if is_streaming and hasattr(executor, "stream"):

                    async def _do_stream() -> None:
                        chunks = 0
                        async for chunk in executor.stream(module_id, merged):
                            chunks += 1
                            click.echo(json.dumps(chunk, default=str))
                            sys.stdout.flush()
                            if sys.stderr.isatty():
                                click.echo(
                                    f"\rStreaming {module_id}... ({chunks} chunks)",
                                    err=True,
                                    nl=False,
                                )
                        if sys.stderr.isatty():
                            click.echo("", err=True)

                    asyncio.run(_do_stream())
                    duration_ms = int((time.monotonic() - audit_start) * 1000)
                    if _audit_logger is not None:
                        _audit_logger.log_execution(module_id, merged, "success", 0, duration_ms)
                    return
                # else: fall through to normal execution

            # -- Traced execution --
            if trace_flag and hasattr(executor, "call_with_trace"):
                result, trace = executor.call_with_trace(
                    module_id,
                    merged,
                    strategy=strategy_name,
                )
                duration_ms = int((time.monotonic() - audit_start) * 1000)

                # Print result (before audit so a formatter raise doesn't double-log)
                if output_format == "json" or not sys.stdout.isatty():
                    # Merge _trace into JSON output
                    trace_data = {
                        "strategy": trace.strategy_name,
                        "total_duration_ms": trace.total_duration_ms,
                        "success": trace.success,
                        "steps": [
                            {
                                "name": s.name,
                                "duration_ms": s.duration_ms,
                                "skipped": s.skipped,
                                **({"skip_reason": s.skip_reason} if s.skipped else {}),
                            }
                            for s in trace.steps
                        ],
                    }
                    if isinstance(result, dict):
                        output = {**result, "_trace": trace_data}
                    else:
                        output = {"result": result, "_trace": trace_data}
                    click.echo(json.dumps(output, indent=2, default=str))
                else:
                    format_exec_result(result, output_format, fields=output_fields)
                    # Print trace to stderr
                    step_count = len(trace.steps)
                    click.echo(
                        f"\nPipeline Trace (strategy: {trace.strategy_name}, "
                        f"{step_count} steps, {trace.total_duration_ms:.1f}ms)",
                        err=True,
                    )
                    for s in trace.steps:
                        if s.skipped:
                            sym = "\u25cb"
                            dur = "\u2014"
                            reason = f"  skipped ({s.skip_reason or 'n/a'})"
                        else:
                            sym = "\u2713"
                            dur = f"{s.duration_ms:.1f}ms"
                            reason = ""
                        click.echo(f"  {sym} {s.name:<24} {dur:>8}{reason}", err=True)

                # Audit after formatting — formatter raise will not produce a
                # duplicate success+error pair
                if _audit_logger is not None:
                    _audit_logger.log_execution(module_id, merged, "success", 0, duration_ms)
                return

            # -- Standard execution (with optional strategy) --
            sandbox = Sandbox(enabled=sandbox_flag).with_extensions_root(extensions_root)
            if strategy_name and hasattr(executor, "call_with_trace"):
                if sandbox_flag:
                    # Sandbox mode: delegate to subprocess (strategy not available in sandbox)
                    logger.warning("--sandbox ignores --strategy; sandboxed execution uses default strategy.")
                    result = sandbox.execute(module_id, merged, executor)
                else:
                    # Strategy requires call_with_trace to pass strategy param
                    result, _trace = executor.call_with_trace(
                        module_id,
                        merged,
                        strategy=strategy_name,
                    )
                if strategy_name != "standard" and sys.stderr.isatty():
                    click.echo(
                        f"Warning: Using '{strategy_name}' strategy.",
                        err=True,
                    )
            else:
                if strategy_name and not hasattr(executor, "call_with_trace"):
                    logger.warning(
                        "--strategy '%s' requested but executor does not support call_with_trace; "
                        "using default pipeline.",
                        strategy_name,
                    )
                result = sandbox.execute(module_id, merged, executor)
            duration_ms = int((time.monotonic() - audit_start) * 1000)

            # 6. Format and print result (before audit so a formatter raise doesn't
            #    produce a spurious success entry followed by an error entry)
            format_exec_result(result, output_format, fields=output_fields)

            # 7. Audit log (success) — only reached if formatting did not raise
            if _audit_logger is not None:
                _audit_logger.log_execution(module_id, merged, "success", 0, duration_ms)

        except KeyboardInterrupt:
            click.echo("Execution cancelled.", err=True)
            sys.exit(130)
        except SystemExit:
            raise
        except Exception as e:
            error_code = getattr(e, "code", None)
            exit_code = _ERROR_CODE_MAP.get(error_code, 1) if isinstance(error_code, str) else 1

            # Audit log (error)
            if _audit_logger is not None:
                duration_ms = int((time.monotonic() - audit_start) * 1000)
                _audit_logger.log_execution(module_id, merged, "error", exit_code, duration_ms)

            if output_format == "json" or not sys.stderr.isatty():
                _emit_error_json(e, exit_code)
            else:
                _emit_error_tty(e, exit_code)
            sys.exit(exit_code)

    # Build the command with schema-generated options + built-in options
    _epilog_parts: list[str] = []
    if not _all_options_help:
        _epilog_parts.append("Use --all-options to show all options (including built-in options).")
    if _docs_url:
        _epilog_parts.append(f"Docs: {_docs_url}/commands/{effective_cmd_name}")
    _epilog = "\n".join(_epilog_parts) if _epilog_parts else None
    cmd = click.Command(
        name=effective_cmd_name,
        help=cmd_help,
        callback=callback,
        epilog=_epilog,
    )

    # Add built-in options (hidden unless --all-options is passed with --help)
    _hide = not _all_options_help
    cmd.params.append(
        click.Option(
            ["--input"],
            default=None,
            help="Read JSON input from a file path, or use '-' to read from stdin pipe.",
            hidden=_hide,
        )
    )
    cmd.params.append(
        click.Option(
            ["--yes", "-y"],
            is_flag=True,
            default=False,
            help="Skip interactive approval prompts (for scripts and CI).",
            hidden=_hide,
        )
    )
    cmd.params.append(
        click.Option(
            ["--large-input"],
            is_flag=True,
            default=False,
            help="Allow stdin input larger than 10MB (default limit protects against accidental pipes).",
            hidden=_hide,
        )
    )
    cmd.params.append(
        click.Option(
            ["--format"],
            type=click.Choice(["json", "table", "csv", "yaml", "jsonl"]),
            default=None,
            help="Output format: json, table, csv, yaml, jsonl.",
            hidden=_hide,
        )
    )
    cmd.params.append(
        click.Option(
            ["--fields"],
            default=None,
            help="Comma-separated dot-paths to select from the result (e.g., 'status,data.count').",
            hidden=_hide,
        )
    )
    # --sandbox is always hidden (not yet implemented)
    cmd.params.append(
        click.Option(
            ["--sandbox"],
            is_flag=True,
            default=False,
            help="Run module in an isolated subprocess with restricted filesystem and env access.",
            hidden=True,
        )
    )
    cmd.params.append(
        click.Option(
            ["--dry-run"],
            is_flag=True,
            default=False,
            help="Run preflight checks without executing the module. Shows validation results.",
            hidden=_hide,
        )
    )
    cmd.params.append(
        click.Option(
            ["--trace"],
            is_flag=True,
            default=False,
            help="Show execution pipeline trace with per-step timing after the result.",
            hidden=_hide,
        )
    )
    cmd.params.append(
        click.Option(
            ["--stream"],
            is_flag=True,
            default=False,
            help="Stream module output as JSONL (one JSON object per line, flushed immediately).",
            hidden=_hide,
        )
    )
    cmd.params.append(
        click.Option(
            ["--strategy"],
            type=click.Choice(["standard", "internal", "testing", "performance", "minimal"]),
            default=None,
            help="Execution pipeline strategy: standard (default), internal, testing, performance.",
            hidden=_hide,
        )
    )
    cmd.params.append(
        click.Option(
            ["--approval-timeout"],
            type=int,
            default=None,
            help="Override approval prompt timeout in seconds (default: 60).",
            hidden=_hide,
        )
    )
    cmd.params.append(
        click.Option(
            ["--approval-token"],
            default=None,
            help="Resume a pending approval with the given token (for async approval flows).",
            hidden=_hide,
        )
    )

    # Guard: schema property names must not collide with built-in option names.
    _reserved = {
        "input",
        "yes",
        "large_input",
        "format",
        "fields",
        "sandbox",
        "all_options",
        "dry_run",
        "trace",
        "stream",
        "strategy",
        "approval_timeout",
        "approval_token",
    }
    for opt in schema_options:
        if opt.name in _reserved:
            click.echo(
                f"Error: Module '{module_id}' schema property '{opt.name}' conflicts "
                f"with a reserved CLI option name. Rename the property.",
                err=True,
            )
            sys.exit(2)

    # Add schema-generated options
    cmd.params.extend(schema_options)

    return cmd


def validate_module_id(module_id: str) -> None:
    """Validate module ID format and length.

    Length limit tracks PROTOCOL_SPEC §2.7 EBNF constraint #1 — bumped from
    128 to 192 in spec 1.6.0-draft to accommodate Java/.NET deep-namespace
    FQN-derived IDs. Filesystem-safe (192 + len('.binding.yaml')=205 < 255).
    """
    if len(module_id) > 192:
        click.echo(
            f"Error: Invalid module ID format: '{module_id}'. Maximum length is 192 characters.",
            err=True,
        )
        sys.exit(2)
    if not re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*", module_id):
        click.echo(
            f"Error: Invalid module ID format: '{module_id}'.",
            err=True,
        )
        sys.exit(2)


def collect_input(
    stdin_flag: str | None,
    cli_kwargs: dict[str, Any],
    large_input: bool = False,
) -> dict[str, Any]:
    """Collect and merge input from STDIN, a file path, and CLI flags.

    ``stdin_flag`` accepts three forms:
      * ``None`` or empty — use CLI kwargs only.
      * ``"-"`` — read JSON from STDIN (10 MB cap unless ``large_input``).
      * any other string — treat as a file path; open and read JSON.

    CLI flags override STDIN/file values for duplicate keys.
    """
    # Remove None values from CLI kwargs
    cli_kwargs_non_none = {k: v for k, v in cli_kwargs.items() if v is not None}

    if not stdin_flag:
        return cli_kwargs_non_none

    if stdin_flag == "-":
        raw = sys.stdin.read()
        raw_size = len(raw.encode("utf-8"))

        if raw_size > 10_485_760 and not large_input:
            click.echo(
                "Error: STDIN input exceeds 10MB limit. Use --large-input to override.",
                err=True,
            )
            sys.exit(2)
        source_label = "STDIN"
    else:
        # File-path form.
        try:
            raw = Path(stdin_flag).read_text(encoding="utf-8")
        except FileNotFoundError:
            click.echo(f"Error: --input file '{stdin_flag}' does not exist.", err=True)
            sys.exit(2)
        except OSError as e:
            click.echo(f"Error: could not read --input file '{stdin_flag}': {e}.", err=True)
            sys.exit(2)
        raw_size = len(raw.encode("utf-8"))
        if raw_size > 10_485_760 and not large_input:
            click.echo(
                f"Error: --input file '{stdin_flag}' exceeds 10MB limit. Use --large-input to override.",
                err=True,
            )
            sys.exit(2)
        source_label = f"--input file '{stdin_flag}'"

    if not raw:
        stdin_data: dict[str, Any] = {}
    else:
        try:
            stdin_data = json.loads(raw)
        except json.JSONDecodeError as e:
            click.echo(
                f"Error: {source_label} does not contain valid JSON: {e.msg}.",
                err=True,
            )
            sys.exit(2)

        if not isinstance(stdin_data, dict):
            click.echo(
                f"Error: {source_label} JSON must be an object, got {type(stdin_data).__name__}.",
                err=True,
            )
            sys.exit(2)

    # CLI flags override STDIN/file for duplicate keys
    return {**stdin_data, **cli_kwargs_non_none}
