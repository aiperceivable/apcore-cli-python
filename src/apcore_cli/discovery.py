"""Discovery commands — list and describe (FE-04)."""

from __future__ import annotations

import re
import sys
from typing import Any

import click

from apcore_cli.cli import validate_module_id
from apcore_cli.output import format_module_detail, format_module_list, resolve_format

_TAG_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


def _validate_tag(tag: str) -> None:
    """Validate tag format."""
    if not _TAG_PATTERN.match(tag):
        click.echo(
            f"Error: Invalid tag format: '{tag}'. Tags must match [a-z][a-z0-9_-]*.",
            err=True,
        )
        sys.exit(2)


def register_discovery_commands(cli: click.Group, registry: Any) -> None:
    """Register list and describe commands on the CLI group."""

    @cli.command("list")
    @click.option("--tag", multiple=True, help="Filter modules by tag (AND logic). Repeatable.")
    @click.option(
        "--format",
        "output_format",
        type=click.Choice(["table", "json"]),
        default=None,
        help="Output format. Default: table (TTY) or json (non-TTY).",
    )
    def list_cmd(tag: tuple[str, ...], output_format: str | None) -> None:
        """List available modules in the registry."""
        # Validate tag format
        for t in tag:
            _validate_tag(t)

        modules = []
        for mid in registry.list():
            mdef = registry.get_definition(mid)
            if mdef is not None:
                modules.append(mdef)

        if tag:
            filter_tags = set(tag)
            modules = [m for m in modules if filter_tags.issubset(set(getattr(m, "tags", [])))]

        fmt = resolve_format(output_format)
        format_module_list(modules, fmt, filter_tags=tag)

    @cli.command("describe")
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
