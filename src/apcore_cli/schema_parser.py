"""Schema Parser — JSON Schema to Click options mapping (FE-02)."""

from __future__ import annotations

import logging
import sys
from typing import Any

import click

logger = logging.getLogger("apcore_cli.schema_parser")

# Sentinel for boolean flag marker
_BOOLEAN_FLAG = object()

# Property names that collide with built-in CLI options — schema_to_click_options
# rejects these early so the error surfaces at schema-parse time, not at invocation.
RESERVED_PROPERTY_NAMES: frozenset[str] = frozenset(
    {
        "format",
        "input",
        "yes",
        "large_input",
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
)


def _resolve_type_from_any_of(prop_schema: dict) -> str | None:
    """Extract the dominant non-null type from an anyOf schema.

    Pydantic v2 emits Optional[X] as {"anyOf": [<X-schema>, {"type": "null"}]}.
    We pick the first non-null branch and return its type string so the caller
    can avoid a "no type" warning for what is effectively a typed optional field.
    $ref entries (nested models) are treated as "object".
    """
    branches = [b for b in prop_schema.get("anyOf", []) if b.get("type") != "null"]
    if not branches:
        return None
    first = branches[0]
    branch_type = first.get("type")
    if isinstance(branch_type, str):
        return branch_type
    if isinstance(branch_type, list):
        # JSON Schema list-form type (e.g. ["string", "null"]): first non-null string.
        return next((t for t in branch_type if isinstance(t, str) and t != "null"), None)
    if "$ref" in first:  # nested model
        return "object"
    return None


def _map_type(prop_name: str, prop_schema: dict) -> Any:
    """Map JSON Schema type to Click parameter type."""
    schema_type = prop_schema.get("type")

    # JSON Schema allows a list-form type (e.g. ["string", "null"]). Reduce it to
    # the first non-null string so it maps cleanly and never reaches the
    # (hashable-key) type_map lookup below as an unhashable list.
    if isinstance(schema_type, list):
        schema_type = next((t for t in schema_type if isinstance(t, str) and t != "null"), None)

    # Pydantic v2 generates Optional[X] as {"anyOf": [<X-schema>, {"type": "null"}]}
    # rather than {"type": "X"}.  Resolve the dominant branch so we don't fall
    # through to the "no type specified" warning for every optional field.
    if schema_type is None and "anyOf" in prop_schema:
        schema_type = _resolve_type_from_any_of(prop_schema)

    # Check file convention
    if schema_type == "string" and (prop_name.endswith("_file") or prop_schema.get("x-cli-file") is True):
        return click.Path(exists=True)

    type_map = {
        "string": click.STRING,
        "integer": click.INT,
        "number": click.FLOAT,
        "boolean": _BOOLEAN_FLAG,
        "object": click.STRING,
        "array": click.STRING,
    }

    if schema_type is None:
        logger.warning(
            "No type specified for property '%s', defaulting to string.",
            prop_name,
        )
        return click.STRING

    result = type_map.get(schema_type)
    if result is None:
        logger.warning(
            "Unknown schema type '%s' for property '%s', defaulting to string.",
            schema_type,
            prop_name,
        )
        return click.STRING

    return result


def _extract_help(prop_schema: dict, max_length: int = 1000) -> str | None:
    """Extract help text from schema property, preferring x-llm-description."""
    text = prop_schema.get("x-llm-description")
    if not text:
        text = prop_schema.get("description")
    if not text:
        return None
    if max_length > 0 and len(text) > max_length:
        return text[: max_length - 3] + "..."
    return text


def schema_to_click_options(schema: dict, max_help_length: int = 1000) -> list[click.Option]:
    """Convert JSON Schema properties to a list of Click options."""
    properties = schema.get("properties", {})
    required_list = schema.get("required", [])
    options: list[click.Option] = []
    flag_names: dict[str, str] = {}

    # Warn about required properties not found in properties
    for req_name in required_list:
        if req_name not in properties:
            logger.warning(
                "Required property '%s' not found in properties, skipping.",
                req_name,
            )

    for prop_name, prop_schema in properties.items():
        if prop_name in RESERVED_PROPERTY_NAMES:
            # Cross-SDK parity: align with neighbour flag-collision behaviour
            # (sys.exit(48)) and TS / Rust schema_parser. Audit D11-NEW-005
            # (2026-05-08). Spec: docs/features/schema-parser.md Contract:
            # schema_to_click_options Errors.
            click.echo(
                f"Error: Schema property '{prop_name}' is reserved and "
                "conflicts with a built-in CLI option. Rename the property.",
                err=True,
            )
            sys.exit(48)

        flag_name = "--" + prop_name.replace("_", "-")

        # Collision detection
        if flag_name in flag_names:
            click.echo(
                f"Error: Flag name collision: properties '{prop_name}' and "
                f"'{flag_names[flag_name]}' both map to '{flag_name}'.",
                err=True,
            )
            sys.exit(48)

        flag_names[flag_name] = prop_name

        click_type = _map_type(prop_name, prop_schema)

        # Cross-SDK parity (D11-005): Rust schema_parser.rs:272 also registers
        # the auto-synthesized ``--no-<flag>`` form into the collision map for
        # boolean properties, so a schema pairing a boolean ``force`` with a
        # non-boolean ``no_force`` is rejected at parse time. Without this,
        # the second property silently shadows the negation flag.
        if click_type is _BOOLEAN_FLAG:
            flag_base = prop_name.replace("_", "-")
            no_flag = f"--no-{flag_base}"
            if no_flag in flag_names:
                click.echo(
                    f"Error: Flag name collision: boolean property '{prop_name}' "
                    f"auto-generates '{no_flag}' which is already used by property "
                    f"'{flag_names[no_flag]}'.",
                    err=True,
                )
                sys.exit(48)
            flag_names[no_flag] = prop_name
        is_required = prop_name in required_list
        _help_base = _extract_help(prop_schema, max_length=max_help_length)
        # Append [required] to help text for user clarity; do NOT set required=True
        # at the Click level because that would block --input - (STDIN) from working.
        # Schema-level required validation happens in the callback via jsonschema.validate().
        help_text = ((_help_base + " ") if _help_base else "") + "[required]" if is_required else _help_base
        default = prop_schema.get("default", None)

        if click_type is _BOOLEAN_FLAG:
            # Boolean flag pair
            default_val = prop_schema.get("default", False)
            flag_base = prop_name.replace("_", "-")
            option = click.Option(
                [f"--{flag_base}/--no-{flag_base}"],
                default=default_val,
                help=help_text,
                show_default=True,
            )
        elif "enum" in prop_schema and click_type is not _BOOLEAN_FLAG:
            enum_values = prop_schema["enum"]
            if not enum_values:
                logger.warning(
                    "Empty enum for property '%s', no values allowed.",
                    prop_name,
                )
                option = click.Option(
                    [flag_name],
                    type=click.STRING,
                    required=False,
                    default=default,
                    help=help_text,
                )
            else:
                string_values = [str(v) for v in enum_values]
                option = click.Option(
                    [flag_name],
                    type=click.Choice(string_values),
                    required=False,
                    default=str(default) if default is not None else None,
                    help=help_text,
                )
                # Store original types for post-parse reconversion
                option._enum_original_types = {str(v): type(v) for v in enum_values}
        else:
            option = click.Option(
                [flag_name],
                type=click_type,
                required=False,
                default=default,
                help=help_text,
            )

        options.append(option)

    return options


def reconvert_enum_values(kwargs: dict[str, Any], options: list[click.Option]) -> dict[str, Any]:
    """Reconvert enum values from string back to their original types."""
    result = dict(kwargs)
    for opt in options:
        original_types = getattr(opt, "_enum_original_types", None)
        if original_types is None:
            continue
        # Get the parameter name (Click uses the dest name)
        param_name = opt.name
        if param_name not in result or result[param_name] is None:
            continue
        str_val = str(result[param_name])
        if str_val in original_types:
            orig_type = original_types[str_val]
            if orig_type is int:
                result[param_name] = int(str_val)
            elif orig_type is float:
                result[param_name] = float(str_val)
            elif orig_type is bool:
                result[param_name] = str_val.lower() == "true"
    return result
