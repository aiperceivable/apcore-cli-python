"""Approval Gate — TTY-aware HITL approval (FE-03)."""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Any

import click

logger = logging.getLogger("apcore_cli.approval")


class ApprovalTimeoutError(Exception):
    """Raised when the approval prompt times out."""

    pass


def _get_annotation(annotations: Any, key: str, default: Any = None) -> Any:
    """Get an annotation value from either a dict or a ModuleAnnotations object."""
    if isinstance(annotations, dict):
        return annotations.get(key, default)
    return getattr(annotations, key, default)


def check_approval(module_def: Any, auto_approve: bool) -> None:
    """Check if module requires approval and handle accordingly.

    Returns None if approved (or approval not required).
    Calls sys.exit(46) if denied/timed out/pending.
    """
    annotations = getattr(module_def, "annotations", None)
    if annotations is None or (not isinstance(annotations, dict) and not hasattr(annotations, "requires_approval")):
        return

    requires = _get_annotation(annotations, "requires_approval", False)
    if requires is not True:
        return

    module_id = getattr(module_def, "module_id", getattr(module_def, "canonical_id", "unknown"))

    # Bypass: --yes flag (highest priority)
    if auto_approve is True:
        logger.info("Approval bypassed via --yes flag for module '%s'.", module_id)
        return

    # Bypass: APCORE_CLI_AUTO_APPROVE env var
    env_val = os.environ.get("APCORE_CLI_AUTO_APPROVE", "")
    if env_val == "1":
        logger.info(
            "Approval bypassed via APCORE_CLI_AUTO_APPROVE for module '%s'.",
            module_id,
        )
        return
    if env_val != "" and env_val != "1":
        logger.warning(
            "APCORE_CLI_AUTO_APPROVE is set to '%s', expected '1'. Ignoring.",
            env_val,
        )

    # Non-TTY check
    is_tty = sys.stdin.isatty()
    if not is_tty:
        click.echo(
            f"Error: Module '{module_id}' requires approval but no interactive "
            "terminal is available. Use --yes or set APCORE_CLI_AUTO_APPROVE=1 "
            "to bypass.",
            err=True,
        )
        logger.error(
            "Non-interactive environment, no bypass provided for module '%s'.",
            module_id,
        )
        sys.exit(46)

    # TTY prompt
    _prompt_with_timeout(module_def, timeout=60)


def _prompt_with_timeout(module_def: Any, timeout: int = 60) -> None:
    """Display approval prompt with timeout."""
    # Clamp timeout to valid range
    timeout = max(1, min(timeout, 3600))

    module_id = getattr(module_def, "module_id", getattr(module_def, "canonical_id", "unknown"))
    annotations = getattr(module_def, "annotations", None) or {}
    message = _get_annotation(annotations, "approval_message", None)
    if message is None:
        message = f"Module '{module_id}' requires approval to execute."

    click.echo(message, err=True)

    if sys.platform != "win32":
        # Unix: use SIGALRM for timeout
        _prompt_unix(module_id, timeout)
    else:
        # Windows: use threading.Timer for timeout
        _prompt_windows(module_id, timeout)


def _prompt_unix(module_id: str, timeout: int) -> None:
    """Unix approval prompt using SIGALRM."""
    import signal

    def _timeout_handler(signum: int, frame: Any) -> None:
        raise ApprovalTimeoutError()

    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout)

    try:
        approved = click.confirm("Proceed?", default=False)
    except ApprovalTimeoutError:
        logger.warning("Approval timed out after %ds for module '%s'.", timeout, module_id)
        click.echo(
            f"Error: Approval prompt timed out after {timeout} seconds.",
            err=True,
        )
        sys.exit(46)
    finally:
        # Always cancel the alarm and restore the previous handler, regardless of
        # how the block exits (normal return, sys.exit, KeyboardInterrupt, etc.).
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

    if approved:
        logger.info("User approved execution of module '%s'.", module_id)
        return
    else:
        logger.warning("Approval rejected by user for module '%s'.", module_id)
        click.echo("Error: Approval denied.", err=True)
        sys.exit(46)


def _prompt_windows(module_id: str, timeout: int) -> None:
    """Windows approval prompt using threading.Timer + ctypes."""
    import ctypes

    def _interrupt_main() -> None:
        ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(threading.main_thread().ident),
            ctypes.py_object(ApprovalTimeoutError),
        )

    timer = threading.Timer(timeout, _interrupt_main)
    timer.start()

    try:
        approved = click.confirm("Proceed?", default=False)
        timer.cancel()

        if approved:
            logger.info("User approved execution of module '%s'.", module_id)
            return
        else:
            logger.warning("Approval rejected by user for module '%s'.", module_id)
            click.echo("Error: Approval denied.", err=True)
            sys.exit(46)
    except ApprovalTimeoutError:
        timer.cancel()
        logger.warning("Approval timed out after %ds for module '%s'.", timeout, module_id)
        click.echo(
            f"Error: Approval prompt timed out after {timeout} seconds.",
            err=True,
        )
        sys.exit(46)
