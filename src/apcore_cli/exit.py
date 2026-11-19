"""Process-exit indirection for embedded hosts.

The CLI is also a library: ``create_cli()`` and ``build_module_command()`` are
called from host applications that own the process. A hard ``sys.exit()``
inside those paths makes the host untestable and prevents it from translating
an apcore exit code into its own (for example mapping
``SCHEMA_VALIDATION_ERROR`` 45 to a generic 2).

Every library-side exit goes through :func:`exit_with`. The default handler
raises ``SystemExit`` -- the normal Click/console-script path -- so the
standalone binary is unchanged. An embedded host installs its own handler
once::

    set_exit_handler(lambda code: (_ for _ in ()).throw(MyCliExit(code)))

A handler may raise its own exception (recommended) or return; a returning
handler causes ``SystemExit(code)`` to be raised so callers never continue
past a failed command.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NoReturn

ExitHandler = Callable[[int], NoReturn]


def _default_exit(code: int) -> NoReturn:
    raise SystemExit(code)


_exit_handler: ExitHandler = _default_exit


def set_exit_handler(handler: ExitHandler | None) -> None:
    """Replace the exit action used by the library.

    Pass ``None`` to restore the default (``raise SystemExit(code)``).
    """
    global _exit_handler
    _exit_handler = handler or _default_exit


def get_exit_handler() -> ExitHandler:
    """Return the currently installed exit handler."""
    return _exit_handler


def reset_exit_handler() -> None:
    """Restore the default handler; intended for tests."""
    global _exit_handler
    _exit_handler = _default_exit


def exit_with(code: int) -> NoReturn:
    """Terminate through the installed handler.

    A handler that returns causes ``SystemExit(code)`` to be raised rather
    than letting the caller continue.
    """
    _exit_handler(code)
    raise SystemExit(code)
