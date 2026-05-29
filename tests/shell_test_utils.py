"""Test-only helper that builds the pre-v0.7 *flat* CLI shape.

The production CLI (``factory.create_cli``) registers ``completion`` under the
``apcli`` builtin group and exposes ``man`` as a root ``--man`` overlay
(``configure_man_help``). This helper instead reproduces the legacy flat shape —
``completion`` and ``man`` as root subcommands — purely so the completion
generators and the man-page builder can be exercised through a CLI in tests.

Relocated from ``apcore_cli.shell.register_shell_commands`` (audit D9-001): the
shim had zero production callers and was removed from the shipped package to
match apcore-cli-rust and apcore-cli-typescript, both of which dropped the
equivalent wrapper in v0.7.0 / FE-13. Keeping it here preserves the behavioral
coverage without shipping dead code.
"""

import sys

import click

from apcore_cli.shell import _generate_man_page, register_completion_command


def register_shell_commands(cli: click.Group, prog_name: str = "apcore-cli") -> None:
    """Attach flat ``completion`` and ``man`` subcommands to *cli* (test-only)."""
    register_completion_command(cli, prog_name=prog_name)

    @cli.command("man")
    @click.argument("command")
    @click.pass_context
    def man_cmd(ctx: click.Context, command: str) -> None:
        """Generate a roff man page for COMMAND and print it to stdout."""
        parent = ctx.parent
        if parent is None:
            click.echo(f"Error: Unknown command '{command}'.", err=True)
            sys.exit(2)

        resolved_prog = ctx.find_root().info_name or prog_name
        parent_group = parent.command
        cmd = parent_group.commands.get(command) if isinstance(parent_group, click.Group) else None

        known_builtins = {"completion", "describe", "exec", "init", "list", "man"}
        if cmd is None and command not in known_builtins:
            click.echo(f"Error: Unknown command '{command}'.", err=True)
            sys.exit(2)

        roff = _generate_man_page(command, cmd, resolved_prog)
        click.echo(roff)

    _ = man_cmd
