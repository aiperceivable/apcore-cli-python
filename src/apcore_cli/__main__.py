"""Entry point for apcore-cli (FE-01).

This module is the script entry point. The `create_cli` factory has been
relocated to `apcore_cli.factory` per audit finding D9 (parallel_impl) so
downstream projects can import the factory without pulling in this module's
script semantics. This file is now intentionally tiny: it only handles the
pre-Click argv extraction needed before `create_cli` is called.
"""

from __future__ import annotations

import sys

# Re-export create_cli for backward compatibility — older callers may import
# `from apcore_cli.__main__ import create_cli`. The canonical import path is
# `from apcore_cli import create_cli` (or `apcore_cli.factory`).
from apcore_cli.factory import __version__ as _sdk_version
from apcore_cli.factory import create_cli


def _extract_argv_option(argv: list[str] | None, flag: str) -> str | None:
    """Extract an option value from argv before Click parses it.

    This is needed because certain options must be resolved before Click runs.
    Returns None if the flag is not present.
    """
    args = argv if argv is not None else sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith(f"{flag}="):
            return arg.split("=", 1)[1]
    return None


def _extract_argv_option_repeatable(argv: list[str] | None, flag: str) -> list[str]:
    """Extract every occurrence of ``flag`` from argv (repeatable option)."""
    args = argv if argv is not None else sys.argv[1:]
    out: list[str] = []
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            out.append(args[i + 1])
        elif arg.startswith(f"{flag}="):
            out.append(arg.split("=", 1)[1])
    return out


def main(prog_name: str | None = None) -> None:
    """Main entry point for apcore-cli.

    Args:
        prog_name: Override the program name shown in help/version output.
                   When None, inferred from sys.argv[0] automatically.
    """
    ext_dir = _extract_argv_option(None, "--extensions-dir")
    cmd_dir = _extract_argv_option(None, "--commands-dir")
    bind_path = _extract_argv_option(None, "--binding")
    allowed_prefixes = _extract_argv_option_repeatable(None, "--allowed-prefix") or None
    # Standalone bin entry — SDK version IS the app version here. Passing it
    # explicitly is required because create_cli() (issue #18) intentionally
    # skips --version for embedded callers that do not opt in.
    cli = create_cli(
        extensions_dir=ext_dir,
        prog_name=prog_name,
        commands_dir=cmd_dir,
        binding_path=bind_path,
        allowed_prefixes=allowed_prefixes,
        version=_sdk_version,
        description=f"{prog_name or 'apcore-cli'} — execute apcore modules from the command line",
    )
    cli(standalone_mode=True)


__all__ = ["create_cli", "main"]

if __name__ == "__main__":
    main()
