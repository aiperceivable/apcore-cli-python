"""Core Dispatcher — CLI entry point and module routing (FE-01)."""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from typing import TYPE_CHECKING, Any

import click
import jsonschema

from apcore_cli.approval import check_approval
from apcore_cli.display_helpers import get_display as _get_display
from apcore_cli.output import format_exec_result
from apcore_cli.ref_resolver import resolve_refs
from apcore_cli.schema_parser import reconvert_enum_values, schema_to_click_options
from apcore_cli.security.sandbox import Sandbox

if TYPE_CHECKING:
    from apcore import Executor, Registry
    from apcore.registry.types import ModuleDescriptor

    from apcore_cli.security.audit import AuditLogger

logger = logging.getLogger("apcore_cli.cli")

BUILTIN_COMMANDS = ["completion", "describe", "exec", "init", "list", "man"]

# Module-level audit logger, set during CLI init
_audit_logger: AuditLogger | None = None

# Module-level verbose help flag, set during CLI init
_verbose_help: bool = False


def set_verbose_help(verbose: bool) -> None:
    """Set the verbose help flag. When False, built-in options are hidden."""
    global _verbose_help
    _verbose_help = verbose


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
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._members = members  # dict[cmd_name, (module_id, descriptor)]
        self._executor = executor
        self._help_text_max_length = help_text_max_length
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
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._registry = registry
        self._executor = executor
        self._help_text_max_length = help_text_max_length
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
        builtin = list(BUILTIN_COMMANDS)
        try:
            self._build_alias_map()
            # Reverse map: module_id → cli alias (if any)
            reverse: dict[str, str] = {v: k for k, v in self._alias_map.items()}
            module_ids = self._registry.list()
            names = [reverse.get(mid, mid) for mid in module_ids]
        except Exception:
            logger.warning("Failed to list modules from registry")
            names = []
        return sorted(set(builtin + names))

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
        )
        self._module_cache[cmd_name] = cmd
        return cmd


class GroupedModuleGroup(LazyModuleGroup):
    """Extended LazyModuleGroup that organises modules into named groups."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._group_map: dict[str, dict[str, tuple[str, Any]]] = {}
        self._top_level_modules: dict[str, tuple[str, Any]] = {}
        self._group_cache: dict[str, _LazyGroup] = {}
        self._group_map_built: bool = False

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
        """Build the group map from registry modules."""
        if self._group_map_built:
            return
        try:
            self._build_alias_map()
            for module_id in self._registry.list():
                descriptor = self._descriptor_cache.get(module_id)
                if descriptor is None:
                    continue
                group, cmd = self._resolve_group(module_id, descriptor)
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
            for group_name in self._group_map:
                if group_name in BUILTIN_COMMANDS:
                    logger.warning(
                        "Group name '%s' collides with a built-in command and will be ignored",
                        group_name,
                    )
            self._group_map_built = True
        except Exception:
            logger.warning("Failed to build group map")

    def list_commands(self, ctx: click.Context) -> list[str]:
        builtin = list(BUILTIN_COMMANDS)
        self._build_group_map()
        group_names = [g for g in self._group_map if g not in BUILTIN_COMMANDS]
        top_names = list(self._top_level_modules.keys())
        return sorted(set(builtin + group_names + top_names))

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

        # Commands section (builtins)
        builtin_records = []
        for name in sorted(BUILTIN_COMMANDS):
            cmd = self.commands.get(name)
            help_text = cmd.get_short_help_str() if cmd else ""
            builtin_records.append((name, help_text))
        if builtin_records:
            with formatter.section("Commands"):
                formatter.write_dl(builtin_records)

        # Modules section (top-level)
        if self._top_level_modules:
            module_records = []
            for name in sorted(self._top_level_modules.keys()):
                _, descriptor = self._top_level_modules[name]
                desc = getattr(descriptor, "description", "") or ""
                module_records.append((name, desc))
            with formatter.section("Modules"):
                formatter.write_dl(module_records)

        # Groups section
        if self._group_map:
            group_records = []
            for group_name in sorted(self._group_map.keys()):
                if group_name in BUILTIN_COMMANDS:
                    continue
                count = len(self._group_map[group_name])
                suffix = "s" if count != 1 else ""
                group_records.append((group_name, f"({count} command{suffix})"))
            if group_records:
                with formatter.section("Groups"):
                    formatter.write_dl(group_records)


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
}


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
        except Exception as e:
            logger.warning("Failed to resolve $refs in schema for '%s', using raw schema: %s", module_id, e)
            resolved_schema = input_schema
    else:
        resolved_schema = input_schema

    schema_options = schema_to_click_options(resolved_schema, max_help_length=help_text_max_length)

    def callback(**kwargs: Any) -> None:
        # Separate built-in options from schema-generated kwargs
        stdin_input = kwargs.pop("input", None)
        auto_approve = kwargs.pop("yes", False)
        large_input = kwargs.pop("large_input", False)
        output_format = kwargs.pop("format", None)
        sandbox_flag = kwargs.pop("sandbox", False)

        merged: dict[str, Any] = {}
        try:
            # 1. Collect and merge input (STDIN + CLI flags)
            merged = collect_input(stdin_input, kwargs, large_input)

            # 2. Reconvert enum values to original types
            merged = reconvert_enum_values(merged, schema_options)

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

            # 4. Check approval gate
            check_approval(module_def, auto_approve)

            # 5. Execute with timing (optionally sandboxed)
            audit_start = time.monotonic()
            sandbox = Sandbox(enabled=sandbox_flag)
            result = sandbox.execute(module_id, merged, executor)
            duration_ms = int((time.monotonic() - audit_start) * 1000)

            # 6. Audit log (success)
            if _audit_logger is not None:
                _audit_logger.log_execution(module_id, merged, "success", 0, duration_ms)

            # 7. Format and print result
            format_exec_result(result, output_format)

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
                _audit_logger.log_execution(module_id, merged, "error", exit_code, 0)

            click.echo(f"Error: {e}", err=True)
            sys.exit(exit_code)

    # Build the command with schema-generated options + built-in options
    _epilog_parts: list[str] = []
    if not _verbose_help:
        _epilog_parts.append("Use --verbose to show all options (including built-in apcore options).")
    if _docs_url:
        _epilog_parts.append(f"Docs: {_docs_url}/commands/{effective_cmd_name}")
    _epilog = "\n".join(_epilog_parts) if _epilog_parts else None
    cmd = click.Command(
        name=effective_cmd_name,
        help=cmd_help,
        callback=callback,
        epilog=_epilog,
    )

    # Add built-in options (hidden unless --verbose is passed with --help)
    _hide = not _verbose_help
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
            type=click.Choice(["json", "table"]),
            default=None,
            help="Set output format: 'json' for machine-readable, 'table' for human-readable.",
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

    # Guard: schema property names must not collide with built-in option names.
    _reserved = {"input", "yes", "large_input", "format", "sandbox", "verbose"}
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
    """Validate module ID format and length."""
    if len(module_id) > 128:
        click.echo(
            f"Error: Invalid module ID format: '{module_id}'. Maximum length is 128 characters.",
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
    """Collect and merge input from STDIN and CLI flags."""
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

        if not raw:
            stdin_data: dict[str, Any] = {}
        else:
            try:
                stdin_data = json.loads(raw)
            except json.JSONDecodeError as e:
                click.echo(
                    f"Error: STDIN does not contain valid JSON: {e.msg}.",
                    err=True,
                )
                sys.exit(2)

            if not isinstance(stdin_data, dict):
                click.echo(
                    f"Error: STDIN JSON must be an object, got {type(stdin_data).__name__}.",
                    err=True,
                )
                sys.exit(2)

        # CLI flags override STDIN for duplicate keys
        return {**stdin_data, **cli_kwargs_non_none}

    return cli_kwargs_non_none
