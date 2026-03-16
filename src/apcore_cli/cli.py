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
from apcore_cli.output import format_exec_result
from apcore_cli.ref_resolver import resolve_refs
from apcore_cli.schema_parser import reconvert_enum_values, schema_to_click_options
from apcore_cli.security.sandbox import Sandbox

if TYPE_CHECKING:
    from apcore import Executor, Registry
    from apcore.registry.types import ModuleDescriptor

    from apcore_cli.security.audit import AuditLogger

logger = logging.getLogger("apcore_cli.cli")

BUILTIN_COMMANDS = ["exec", "list", "describe", "completion", "man"]

# Module-level audit logger, set during CLI init
_audit_logger: AuditLogger | None = None


def set_audit_logger(audit_logger: AuditLogger | None) -> None:
    """Set the global audit logger instance. Pass None to clear."""
    global _audit_logger
    _audit_logger = audit_logger


class LazyModuleGroup(click.Group):
    """Custom Click Group that lazily loads apcore modules as subcommands."""

    def __init__(self, registry: Registry, executor: Executor, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._registry = registry
        self._executor = executor
        self._module_cache: dict[str, click.Command] = {}

    def list_commands(self, ctx: click.Context) -> list[str]:
        builtin = list(BUILTIN_COMMANDS)
        try:
            module_ids = self._registry.list()
        except Exception:
            logger.warning("Failed to list modules from registry")
            module_ids = []
        return sorted(set(builtin + module_ids))

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        # Check built-in commands first
        if cmd_name in self.commands:
            return self.commands[cmd_name]

        # Check cache
        if cmd_name in self._module_cache:
            return self._module_cache[cmd_name]

        # Look up in registry
        module_def = self._registry.get_definition(cmd_name)
        if module_def is None:
            return None

        cmd = build_module_command(module_def, self._executor)
        self._module_cache[cmd_name] = cmd
        return cmd


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


def build_module_command(module_def: ModuleDescriptor, executor: Executor) -> click.Command:
    """Build a Click command from an apcore module definition.

    Generates Click options from the module's input_schema, wires up
    STDIN input collection, schema validation, approval gating,
    execution, audit logging, and output formatting.
    """
    # Resolve $refs and generate Click options from input_schema
    raw_schema = getattr(module_def, "input_schema", None)
    module_id = _get_module_id(module_def)

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

    schema_options = schema_to_click_options(resolved_schema)

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
            exit_code = _ERROR_CODE_MAP.get(error_code, 1)

            # Audit log (error)
            if _audit_logger is not None:
                _audit_logger.log_execution(module_id, merged, "error", exit_code, 0)

            click.echo(f"Error: {e}", err=True)
            sys.exit(exit_code)

    # Build the command with schema-generated options + built-in options
    cmd = click.Command(
        name=module_id,
        help=module_def.description,
        callback=callback,
    )

    # Add built-in options
    cmd.params.append(
        click.Option(
            ["--input"],
            default=None,
            help="Read input from file or STDIN ('-').",
        )
    )
    cmd.params.append(
        click.Option(
            ["--yes", "-y"],
            is_flag=True,
            default=False,
            help="Bypass approval prompts.",
        )
    )
    cmd.params.append(
        click.Option(
            ["--large-input"],
            is_flag=True,
            default=False,
            help="Allow STDIN input larger than 10MB.",
        )
    )
    cmd.params.append(
        click.Option(
            ["--format"],
            type=click.Choice(["json", "table"]),
            default=None,
            help="Output format.",
        )
    )
    cmd.params.append(
        click.Option(
            ["--sandbox"],
            is_flag=True,
            default=False,
            help="Run module in subprocess sandbox.",
        )
    )

    # Guard: schema property names must not collide with built-in option names.
    _reserved = {"input", "yes", "large_input", "format", "sandbox"}
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
