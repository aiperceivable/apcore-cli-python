"""Schema Parser — JSON Schema to Click options mapping (FE-02)."""

from __future__ import annotations

import logging
import sys
from typing import Any

import click

logger = logging.getLogger("apcore_cli.schema_parser")

# Sentinel for boolean flag marker
_BOOLEAN_FLAG = object()


def _map_type(prop_name: str, prop_schema: dict) -> Any:
    """Map JSON Schema type to Click parameter type."""
    schema_type = prop_schema.get("type")

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


def _extract_help(prop_schema: dict) -> str | None:
    """Extract help text from schema property, preferring x-llm-description."""
    text = prop_schema.get("x-llm-description")
    if not text:
        text = prop_schema.get("description")
    if not text:
        return None
    if len(text) > 200:
        return text[:197] + "..."
    return text


def schema_to_click_options(schema: dict) -> list[click.Option]:
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
        is_required = prop_name in required_list
        _help_base = _extract_help(prop_schema)
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
