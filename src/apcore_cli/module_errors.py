"""Shared failure handling for module commands.

The per-module commands and ``apcli exec`` must behave identically for an
embedding host: same classification, same envelope, same destination, and the
same host hooks (``on_error`` / ``exit_codes`` / ``exit`` / ``error_stream``).
Keeping that in one module also avoids a cli <-> discovery import cycle.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, NoReturn, TextIO

from apcore_cli.exit import exit_with
from apcore_cli.exit_codes import EXIT_INVALID_INPUT, exit_code_for_error

ErrorStream = TextIO | str | None


@dataclass(frozen=True)
class ModuleErrorContext:
    """Context handed to ``on_error`` before the process exits."""

    error: BaseException
    exit_code: int
    code: str | None
    module_id: str


def resolve_error_stream(spec: ErrorStream) -> TextIO:
    """Resolve the destination for a failure envelope.

    ``"stdout"`` lets an agent-facing host honour the "stdout carries exactly
    one document" contract. The default remains stderr.
    """
    if spec is None or spec == "stderr":
        return sys.stderr
    if spec == "stdout":
        return sys.stdout
    return spec


def emit_error_json(e: BaseException, exit_code: int, file: TextIO | None = None) -> None:
    """Emit the single-line JSON error document (F3)."""
    dest = file or sys.stderr
    code = getattr(e, "code", None)
    payload: dict[str, Any] = {
        "error": True,
        "code": code or "UNKNOWN",
        "message": str(e),
        "exit_code": exit_code,
    }
    for field in ("details", "suggestion", "ai_guidance", "retryable", "user_fixable"):
        val = getattr(e, field, None)
        if val is not None:
            payload[field] = val
    dest.write(json.dumps(payload, default=str) + "\n")
    dest.flush()


def emit_error_tty(e: BaseException, exit_code: int, file: TextIO | None = None) -> None:
    """Emit the human-readable error with guidance fields (F3)."""
    dest = file or sys.stderr
    code = getattr(e, "code", None)
    header = f"Error [{code}]: {e}" if code else f"Error: {e}"
    dest.write(header + "\n")

    details = getattr(e, "details", None)
    if details and isinstance(details, dict):
        dest.write("\n  Details:\n")
        for k, v in details.items():
            dest.write(f"    {k}: {v}\n")

    suggestion = getattr(e, "suggestion", None)
    if suggestion:
        dest.write(f"\n  Suggestion: {suggestion}\n")

    retryable = getattr(e, "retryable", None)
    if retryable is not None:
        label = "Yes" if retryable else "No (same input will fail again)"
        dest.write(f"  Retryable: {label}\n")

    dest.write(f"\n  Exit code: {exit_code}\n")
    dest.flush()


class InvalidCliInputError(ValueError):
    """Invalid arguments detected by the CLI before calling the executor."""

    code = "GENERAL_INVALID_INPUT"


def resolve_exit_code(
    e: BaseException,
    exit_codes: Mapping[str, int] | None = None,
    explicit_code: int | None = None,
) -> int:
    """Classify a failure, honouring a host-provided code translation."""
    code = getattr(e, "code", None)
    if exit_codes and isinstance(code, str) and code in exit_codes:
        return int(exit_codes[code])
    if explicit_code is not None:
        return explicit_code
    if isinstance(e, InvalidCliInputError):
        return EXIT_INVALID_INPUT
    return int(exit_code_for_error(e))


def handle_module_error(
    e: BaseException,
    *,
    module_id: str,
    output_format: str | None = None,
    on_error: Callable[[ModuleErrorContext], None] | None = None,
    exit_codes: Mapping[str, int] | None = None,
    exit: Callable[[int], None] | None = None,
    error_stream: ErrorStream = None,
    explicit_code: int | None = None,
) -> NoReturn:
    """Report a module failure through the host hooks and exit.

    Order matches the TypeScript SDK: on_error -> envelope -> exit. A host can
    replace the exit with ``exit`` (raise to unwind) or globally with
    :func:`apcore_cli.exit.set_exit_handler`.
    """
    code = getattr(e, "code", None)
    exit_code = resolve_exit_code(e, exit_codes, explicit_code)

    if on_error is not None:
        on_error(
            ModuleErrorContext(
                error=e,
                exit_code=exit_code,
                code=code if isinstance(code, str) else None,
                module_id=module_id,
            )
        )

    stream = resolve_error_stream(error_stream)
    if output_format == "json" or not sys.stderr.isatty():
        emit_error_json(e, exit_code, stream)
    else:
        emit_error_tty(e, exit_code, stream)

    (exit or exit_with)(exit_code)
    raise SystemExit(exit_code)
