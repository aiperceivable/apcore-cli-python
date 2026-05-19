"""Init command — scaffold new apcore modules (Phase 1)."""

from __future__ import annotations

import sys
from pathlib import Path

import click

# Templates emit `# TODO: implement` placeholders into the GENERATED user
# module. These are user-facing scaffolding — not SDK source-level TODOs —
# and intentionally match the parity templates in
# `../apcore-cli-typescript/src/init-cmd.ts` and
# `../apcore-cli-rust/src/init_cmd.rs`. Audit tools that grep for "TODO"
# should treat occurrences inside the template literals below as expected.
_DECORATOR_TEMPLATE = '''"""Module: {module_id}"""

from apcore import module


@module(id="{module_id}", description="{description}")
def {func_name}({params_str}) -> dict:
    """{description}"""
    # TODO: implement
    return {{"status": "ok"}}
'''

_CONVENTION_TEMPLATE = '''"""{description}"""

{cli_group_line}{tags_line}

def {func_name}({params_str}) -> dict:
    """{description}"""
    # TODO: implement
    return {{"status": "ok"}}
'''

_BINDING_TEMPLATE = """bindings:
  - module_id: "{module_id}"
    target: "{target}"
    description: "{description}"
    auto_schema: true
"""


def register_init_command(cli: click.Group) -> None:
    """Register the init command on the CLI group."""

    @cli.group("init")
    def init_group():
        """Scaffold new modules."""
        pass

    @init_group.command("module")
    @click.argument("module_id")
    @click.option(
        "--style",
        type=click.Choice(["decorator", "convention", "binding"]),
        default="convention",
        help="Module style: decorator (@module), convention (plain function), or binding (YAML).",
    )
    @click.option("--dir", "output_dir", default=None, help="Output directory. Default: extensions/ or commands/.")
    @click.option("--description", "-d", default="TODO: add description", help="Module description.")
    @click.option(
        "-f",
        "--force",
        is_flag=True,
        default=False,
        help="Overwrite existing files. Without this flag, init refuses to clobber.",
    )
    def init_module(module_id: str, style: str, output_dir: str | None, description: str, force: bool) -> None:
        """Create a new module from a template.

        MODULE_ID is the module identifier (e.g., ops.deploy, user.create).
        """
        if output_dir is not None and ".." in Path(output_dir).parts:
            click.echo("Error: Output directory must not contain '..' path components.", err=True)
            sys.exit(2)

        # Parse module_id into parts
        parts = module_id.rsplit(".", 1)
        if len(parts) == 2:
            prefix, func_name = parts
        else:
            prefix = parts[0]
            func_name = parts[0]

        if style == "decorator":
            _create_decorator_module(module_id, prefix, func_name, description, output_dir, force)
        elif style == "convention":
            _create_convention_module(module_id, prefix, func_name, description, output_dir, force)
        elif style == "binding":
            _create_binding_module(module_id, prefix, func_name, description, output_dir, force)


def _refuse_if_exists(filepath: Path, force: bool) -> bool:
    """Return True if writing should proceed; False if skipped.

    When ``force`` is False and the file already exists, emit a refusal message
    to stderr and return False so the caller can skip the write. Never aborts
    the whole init (one-file refusal should not kill the other templates).
    """
    if filepath.exists() and not force:
        click.echo(
            f"Error: {filepath} already exists. Use -f/--force to overwrite.",
            err=True,
        )
        return False
    return True


def _create_decorator_module(
    module_id: str, prefix: str, func_name: str, description: str, output_dir: str | None, force: bool
) -> None:
    base = Path(output_dir or "extensions")
    base.mkdir(parents=True, exist_ok=True)
    filename = module_id.replace(".", "_") + ".py"
    filepath = base / filename

    if not _refuse_if_exists(filepath, force):
        return

    content = _DECORATOR_TEMPLATE.format(
        module_id=module_id,
        func_name=func_name,
        description=description,
        params_str="",
    )
    filepath.write_text(content)
    click.echo(f"Created {filepath}")


def _create_convention_module(
    module_id: str, prefix: str, func_name: str, description: str, output_dir: str | None, force: bool
) -> None:
    base = Path(output_dir or "commands")
    # If prefix has dots, create subdirectories
    prefix_parts = prefix.split(".")
    dir_path = base / Path(*prefix_parts) if len(prefix_parts) > 1 else base
    dir_path.mkdir(parents=True, exist_ok=True)

    filename = (prefix_parts[-1] if len(prefix_parts) > 1 else prefix) + ".py"
    # If the file would be the same as the function name, use prefix as filename
    if prefix == func_name:
        filename = prefix + ".py"
    filepath = dir_path / filename

    if not _refuse_if_exists(filepath, force):
        return

    cli_group_line = f'CLI_GROUP = "{prefix_parts[0]}"\n' if len(prefix_parts) >= 1 and "." in module_id else ""
    tags_line = ""

    content = _CONVENTION_TEMPLATE.format(
        func_name=func_name,
        description=description,
        params_str="",
        cli_group_line=cli_group_line,
        tags_line=tags_line,
    )
    filepath.write_text(content)
    click.echo(f"Created {filepath}")


def _create_binding_module(
    module_id: str, prefix: str, func_name: str, description: str, output_dir: str | None, force: bool
) -> None:
    base_bindings = Path(output_dir or "bindings")
    base_bindings.mkdir(parents=True, exist_ok=True)

    yaml_file = base_bindings / (module_id.replace(".", "_") + ".binding.yaml")
    src_base_name = Path(output_dir or "commands").name
    target = f"{src_base_name}.{prefix}:{func_name}"

    if not _refuse_if_exists(yaml_file, force):
        return

    yaml_content = _BINDING_TEMPLATE.format(
        module_id=module_id,
        target=target,
        description=description,
    )
    yaml_file.write_text(yaml_content)
    click.echo(f"Created {yaml_file}")

    # Also create the target function file — honour --dir so all artifacts land together
    base_src = Path(output_dir or "commands")
    base_src.mkdir(parents=True, exist_ok=True)
    src_file = base_src / (prefix.replace(".", "_") + ".py")
    if not _refuse_if_exists(src_file, force):
        return
    src_content = (
        f'def {func_name}() -> dict:\n    """{description}"""\n    # TODO: implement\n    return {{"status": "ok"}}\n'
    )
    src_file.write_text(src_content)
    click.echo(f"Created {src_file}")
