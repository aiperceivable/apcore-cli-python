"""Entry point for apcore-cli (FE-01)."""

from __future__ import annotations

import logging
import os
import sys

import click

from apcore_cli import __version__
from apcore_cli.cli import LazyModuleGroup, set_audit_logger
from apcore_cli.config import ConfigResolver
from apcore_cli.discovery import register_discovery_commands
from apcore_cli.security.audit import AuditLogger
from apcore_cli.shell import register_shell_commands

logger = logging.getLogger("apcore_cli")

EXIT_CONFIG_NOT_FOUND = 47


def _extract_extensions_dir(argv: list[str] | None = None) -> str | None:
    """Extract --extensions-dir value from argv before Click parses it.

    This is needed because the registry must be created before Click runs,
    but --extensions-dir is a Click option parsed at runtime.
    Returns None if the flag is not present.
    """
    args = argv if argv is not None else sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--extensions-dir" and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith("--extensions-dir="):
            return arg.split("=", 1)[1]
    return None


def create_cli(extensions_dir: str | None = None, prog_name: str | None = None) -> click.Group:
    """Create the CLI application.

    Args:
        extensions_dir: Override for extensions directory.
                        When None, resolves via ConfigResolver (env/file/default).
        prog_name: Name shown in help text and version output.
                   Defaults to the basename of sys.argv[0], so downstream projects
                   that install their own entry-point script get the correct name
                   automatically (e.g. ``mycli`` instead of ``apcore-cli``).
    """
    if prog_name is None:
        prog_name = os.path.basename(sys.argv[0]) or "apcore-cli"

    # Resolve CLI log level (3-tier precedence, evaluated before Click runs):
    #   APCORE_CLI_LOGGING_LEVEL (CLI-specific) > APCORE_LOGGING_LEVEL (global) > WARNING
    # The --log-level flag (parsed later) can further override at runtime.
    _cli_level_str = os.environ.get("APCORE_CLI_LOGGING_LEVEL", "").upper()
    _global_level_str = os.environ.get("APCORE_LOGGING_LEVEL", "").upper()
    _active_level_str = _cli_level_str or _global_level_str
    _default_level = getattr(logging, _active_level_str, logging.WARNING) if _active_level_str else logging.WARNING
    logging.basicConfig(level=_default_level, format="%(levelname)s: %(message)s")
    # basicConfig is a no-op if handlers already exist; always set the root level explicitly.
    logging.getLogger().setLevel(_default_level)
    # Silence noisy upstream apcore loggers unless the user requests verbose output.
    # Always set explicitly so the level is deterministic regardless of prior state.
    apcore_level = _default_level if _default_level <= logging.INFO else logging.ERROR
    logging.getLogger("apcore").setLevel(apcore_level)

    if extensions_dir is not None:
        ext_dir = extensions_dir
    else:
        config = ConfigResolver()
        ext_dir = config.resolve(
            "extensions.root",
            cli_flag="--extensions-dir",
            env_var="APCORE_EXTENSIONS_ROOT",
        )

    ext_dir_missing = not os.path.exists(ext_dir)
    ext_dir_unreadable = not ext_dir_missing and not os.access(ext_dir, os.R_OK)

    if ext_dir_missing:
        click.echo(
            f"Error: Extensions directory not found: '{ext_dir}'. " "Set APCORE_EXTENSIONS_ROOT or verify the path.",
            err=True,
        )
        sys.exit(EXIT_CONFIG_NOT_FOUND)

    if ext_dir_unreadable:
        click.echo(
            f"Error: Cannot read extensions directory: '{ext_dir}'. Check permissions.",
            err=True,
        )
        sys.exit(EXIT_CONFIG_NOT_FOUND)

    try:
        from apcore import Executor, Registry

        registry = Registry(extensions_dir=ext_dir)
        try:
            logger.debug("Loading extensions from %s", ext_dir)
            count = registry.discover()
            logger.info("Initialized apcore-cli with %d modules.", count)
        except Exception as e:
            logger.warning("Discovery failed: %s", e)

        executor = Executor(registry)
    except Exception as e:
        click.echo(f"Error: Failed to initialize registry: {e}", err=True)
        sys.exit(EXIT_CONFIG_NOT_FOUND)

    # Initialize audit logger
    try:
        audit_logger = AuditLogger()
        set_audit_logger(audit_logger)
    except Exception as e:
        logger.warning("Failed to initialize audit logger: %s", e)

    @click.group(
        cls=LazyModuleGroup,
        registry=registry,
        executor=executor,
        name=prog_name,
        help="CLI adapter for the apcore module ecosystem.",
    )
    @click.version_option(
        version=__version__,
        prog_name=prog_name,
    )
    @click.option(
        "--extensions-dir",
        "extensions_dir_opt",
        default=None,
        help="Path to apcore extensions directory.",
    )
    @click.option(
        "--log-level",
        default=None,
        type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
        help="Log verbosity. Overrides APCORE_CLI_LOGGING_LEVEL and APCORE_LOGGING_LEVEL env vars.",
    )
    @click.pass_context
    def cli(ctx: click.Context, extensions_dir_opt: str | None = None, log_level: str | None = None) -> None:
        if log_level is not None:
            # basicConfig() is a no-op once handlers exist; set level on the root logger directly.
            level = getattr(logging, log_level.upper(), logging.WARNING)
            logging.getLogger().setLevel(level)
            # Keep apcore logger in sync: verbose when user asks for it, quiet otherwise.
            apcore_level = level if level <= logging.INFO else logging.ERROR
            logging.getLogger("apcore").setLevel(apcore_level)
        ctx.ensure_object(dict)
        ctx.obj["extensions_dir"] = ext_dir

    # Register discovery commands
    register_discovery_commands(cli, registry)

    # Register shell integration commands
    register_shell_commands(cli, prog_name=prog_name)

    return cli


def main(prog_name: str | None = None) -> None:
    """Main entry point for apcore-cli.

    Args:
        prog_name: Override the program name shown in help/version output.
                   When None, inferred from sys.argv[0] automatically.
    """
    ext_dir = _extract_extensions_dir()
    cli = create_cli(extensions_dir=ext_dir, prog_name=prog_name)
    cli(standalone_mode=True)


if __name__ == "__main__":
    main()
