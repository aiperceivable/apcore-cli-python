"""Approval Gate — TTY-aware HITL approval (FE-03, FE-11 §3.5)."""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Any

import click

logger = logging.getLogger("apcore_cli.approval")


class ApprovalTimeoutError(Exception):
    """Raised when the approval prompt times out.

    Carries ``code='APPROVAL_TIMEOUT'`` so the discovery.py exec_cmd
    ``except Exception`` handler maps it to exit code 46 via _ERROR_CODE_MAP
    while still flushing the audit log (D11-001).
    """

    code = "APPROVAL_TIMEOUT"


class ApprovalDeniedError(Exception):
    """Raised when an approval request is denied (rejected or pending).

    Carries ``code='APPROVAL_DENIED'`` so the discovery.py exec_cmd
    ``except Exception`` handler maps it to exit code 46 via _ERROR_CODE_MAP
    while still flushing the audit log (D11-001). Previously the legacy
    ``check_approval`` wrapper called ``sys.exit(46)`` directly — SystemExit
    is a BaseException and bypassed the audit-flush handler.
    """

    code = "APPROVAL_DENIED"


def _get_annotation(annotations: Any, key: str, default: Any = None) -> Any:
    """Get an annotation value from either a dict or a ModuleAnnotations object."""
    if isinstance(annotations, dict):
        return annotations.get(key, default)
    return getattr(annotations, key, default)


def _read_timeout_from_env() -> int | None:
    """Parse APCORE_CLI_APPROVAL_TIMEOUT, warn on malformed, return None on absent/invalid.

    D11-012 cross-SDK parity: TS's `readTimeoutFromEnv()` reads the same
    variable; Rust will be aligned in a follow-up. Documented precedence is
    `CLI flag > env var > 60s default`.
    """
    raw = os.environ.get("APCORE_CLI_APPROVAL_TIMEOUT")
    if raw is None or raw == "":
        return None
    try:
        parsed = int(raw)
    except ValueError:
        print(
            f"Warning: APCORE_CLI_APPROVAL_TIMEOUT='{raw}' is not an integer. Ignoring.",
            file=sys.stderr,
        )
        return None
    if parsed <= 0:
        print(
            f"Warning: APCORE_CLI_APPROVAL_TIMEOUT='{raw}' must be > 0. Ignoring.",
            file=sys.stderr,
        )
        return None
    return parsed


def _approval_result(status: str, approved_by: str | None = None, reason: str | None = None) -> Any:
    """Build the apcore-protocol ``ApprovalResult`` the executor's gate expects.

    The approval gate reads the handler's return value by attribute
    (``result.status`` in apcore ``builtin_steps``), so a plain mapping raises
    ``AttributeError`` inside the gate and surfaces to the caller as
    ``MODULE_EXECUTE_ERROR``. Mirrors the Rust adapter's ``cli_to_apcore_result``
    and TypeScript's structurally-typed ``ApprovalResult`` object.

    Falls back to the mapping shape when apcore is not importable so the handler
    stays usable in isolation (unit tests that stub the runtime out).
    """
    payload: dict[str, Any] = {"status": status}
    if approved_by is not None:
        payload["approved_by"] = approved_by
    if reason is not None:
        payload["reason"] = reason
    try:
        from apcore.approval import ApprovalResult
    except ImportError:
        return payload
    return ApprovalResult(**payload)


# ---------------------------------------------------------------------------
# CliApprovalHandler — implements apcore ApprovalHandler protocol (FE-11 §3.5)
# ---------------------------------------------------------------------------


class CliApprovalHandler:
    """ApprovalHandler that prompts in TTY, auto-denies in non-TTY (unless bypassed).

    Implements the apcore ApprovalHandler protocol:
    - ``request_approval(request) -> ApprovalResult``
    - ``check_approval(approval_id) -> ApprovalResult``

    Pass to Executor via ``executor.set_approval_handler(handler)``.
    """

    def __init__(self, auto_approve: bool = False, timeout: int | None = None) -> None:
        self.auto_approve = auto_approve
        # D11-012: default-timeout resolution honors APCORE_CLI_APPROVAL_TIMEOUT
        # env var when caller did not pass an explicit timeout. Precedence:
        # constructor arg > env var > 60s default. Matches TS readTimeoutFromEnv.
        if timeout is None:
            timeout = _read_timeout_from_env() or 60
        self.timeout = max(1, min(timeout, 3600))

    async def request_approval(self, request: Any) -> Any:
        """Request approval for a module invocation.

        Follows the apcore ApprovalRequest/ApprovalResult protocol.
        Returns a dict with ``status``, ``approved_by``, ``reason`` fields
        (duck-type compatible with ApprovalResult dataclass).
        """
        module_id = getattr(request, "module_id", "unknown")

        # D11-014: defense-in-depth — short-circuit when the request explicitly
        # declares it does not require approval. Matches Rust approval.rs:421
        # (`if !get_requires_approval(module_def) { return Approved::not_required }`).
        requires_attr = getattr(request, "requires_approval", None)
        if requires_attr is False:
            return _approval_result("approved", approved_by="not_required")
        module_def = getattr(request, "module_def", None)
        if module_def is not None:
            annotations = getattr(module_def, "annotations", None)
            if annotations is not None:
                requires = _get_annotation(annotations, "requires_approval", None)
                if requires is False:
                    return _approval_result("approved", approved_by="not_required")

        # Bypass: auto_approve flag
        if self.auto_approve:
            logger.info("Approval bypassed via --yes flag for module '%s'.", module_id)
            return _approval_result("approved", approved_by="auto_approve")

        # Bypass: APCORE_CLI_AUTO_APPROVE env var
        env_val = os.environ.get("APCORE_CLI_AUTO_APPROVE", "")
        if env_val == "1":
            logger.info("Approval bypassed via APCORE_CLI_AUTO_APPROVE for '%s'.", module_id)
            return _approval_result("approved", approved_by="env_auto_approve")
        if env_val != "" and env_val != "1":
            # D10-009 cross-SDK parity: emit to stderr (matching TS) so log
            # capture / handler config does not mask the warning. Spec at
            # apcore-cli/docs/features/approval-gate.md:122 says "Log WARNING"
            # which was ambiguous; the three SDKs now agree on stderr.
            print(
                f"Warning: APCORE_CLI_AUTO_APPROVE is set to '{env_val}', expected '1'. Ignoring.",
                file=sys.stderr,
            )

        # Non-TTY: reject
        if not sys.stdin.isatty():
            return _approval_result("rejected", reason="Non-interactive session without --yes")

        # TTY prompt
        annotations = getattr(request, "annotations", None) or {}
        extra = getattr(annotations, "extra", {}) if not isinstance(annotations, dict) else annotations
        message = extra.get("approval_message") or f"Module '{module_id}' requires approval to execute."

        click.echo(message, err=True)
        try:
            approved = _tty_prompt(module_id, self.timeout)
        except ApprovalTimeoutError:
            return _approval_result("timeout", reason=f"Timed out after {self.timeout}s")

        if approved:
            return _approval_result("approved", approved_by="tty_user")
        return _approval_result("rejected", reason="User rejected")

    async def check_approval(self, approval_id: str) -> Any:
        """Check status of a previously pending approval (Phase B).

        CLI does not support async approval polling; always returns rejected.
        """
        return _approval_result("rejected", reason="CLI does not support async approval polling")


# ---------------------------------------------------------------------------
# Legacy check_approval() — backward-compatible wrapper
# ---------------------------------------------------------------------------


def check_approval(
    module_def: Any,
    auto_approve: bool,
    timeout: int | None = None,
    acl_requires_approval: bool = False,
) -> None:
    """Check if module requires approval and handle accordingly.

    Returns None if approved (or approval not required).
    Raises :class:`ApprovalDeniedError` on denial / non-TTY / user reject,
    or :class:`ApprovalTimeoutError` on prompt timeout. Both errors carry a
    ``code`` attribute that maps to exit code 46 via the CLI ``_ERROR_CODE_MAP``;
    the discovery.py exec_cmd ``except Exception`` handler is responsible for
    flushing the audit log and translating the error to ``sys.exit(46)``
    (D11-001 — previously this function called ``sys.exit`` directly, which
    raises BaseException and bypassed the audit-flush handler).

    Args:
        module_def: Module descriptor with annotations.
        auto_approve: If True, bypass approval (--yes flag).
        timeout: Approval prompt timeout in seconds.
        acl_requires_approval: An ACL rule matched this call and carried
            ``approval: required`` (§6.1.6). **Unioned** with the module's own
            annotation, exactly as apcore's gate composes the two for a normal
            call — so a rule demanding a human demands one on every execution
            path, not just the ones that reach the Executor. Only supplied by
            §4.10 callers that gate an off-Executor path themselves; the
            executor's own gate handles the in-process case.
    """
    annotations = getattr(module_def, "annotations", None)
    annotated = False
    if annotations is not None and (isinstance(annotations, dict) or hasattr(annotations, "requires_approval")):
        annotated = _get_annotation(annotations, "requires_approval", False) is True

    # Union of the two sources. Checked before the annotation shape guard
    # above short-circuits, so an un-annotated module still honours an
    # ACL-sourced requirement.
    if not annotated and not acl_requires_approval:
        return

    # D11-012: resolve timeout precedence — explicit arg > APCORE_CLI_APPROVAL_TIMEOUT env > 60s.
    if timeout is None:
        timeout = _read_timeout_from_env() or 60

    module_id = getattr(module_def, "module_id", getattr(module_def, "canonical_id", "unknown"))

    # Bypass: --yes flag (highest priority)
    if auto_approve is True:
        logger.info("Approval bypassed via --yes flag for module '%s'.", module_id)
        return

    # Bypass: APCORE_CLI_AUTO_APPROVE env var
    env_val = os.environ.get("APCORE_CLI_AUTO_APPROVE", "")
    if env_val == "1":
        logger.info("Approval bypassed via APCORE_CLI_AUTO_APPROVE for '%s'.", module_id)
        return
    if env_val != "" and env_val != "1":
        # D10-009 cross-SDK parity: emit to stderr (matching TS) for
        # consistent user-visible channel regardless of logger config.
        print(
            f"Warning: APCORE_CLI_AUTO_APPROVE is set to '{env_val}', expected '1'. Ignoring.",
            file=sys.stderr,
        )

    # Non-TTY check
    if not sys.stdin.isatty():
        raise ApprovalDeniedError(
            f"Module '{module_id}' requires approval but no interactive "
            "terminal is available. Use --yes or set APCORE_CLI_AUTO_APPROVE=1 "
            "to bypass."
        )

    # TTY prompt
    _prompt_with_timeout(module_def, timeout=timeout)


# ---------------------------------------------------------------------------
# Internal prompt implementation
# ---------------------------------------------------------------------------


def _prompt_with_timeout(module_def: Any, timeout: int = 60) -> None:
    """Display approval prompt with timeout."""
    timeout = max(1, min(timeout, 3600))

    module_id = getattr(module_def, "module_id", getattr(module_def, "canonical_id", "unknown"))
    annotations = getattr(module_def, "annotations", None) or {}
    message = _get_annotation(annotations, "approval_message", None)
    if message is None:
        message = f"Module '{module_id}' requires approval to execute."

    click.echo(message, err=True)

    try:
        approved = _tty_prompt(module_id, timeout)
    except ApprovalTimeoutError:
        raise ApprovalTimeoutError(f"Approval prompt timed out after {timeout} seconds.") from None

    if approved:
        logger.info("User approved execution of module '%s'.", module_id)
    else:
        raise ApprovalDeniedError("Approval denied.")


def _tty_prompt(module_id: str, timeout: int) -> bool:
    """Run the TTY prompt with timeout. Returns True if approved, raises on timeout."""
    if sys.platform != "win32":
        return _prompt_unix(module_id, timeout)
    return _prompt_windows(module_id, timeout)


def _prompt_unix(module_id: str, timeout: int) -> bool:
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
        raise
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

    return approved


def _prompt_windows(module_id: str, timeout: int) -> bool:
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
        return approved
    except ApprovalTimeoutError:
        timer.cancel()
        logger.warning("Approval timed out after %ds for module '%s'.", timeout, module_id)
        raise
