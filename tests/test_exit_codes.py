"""Tests for apcore_cli.exit_codes (D1-003 — cross-SDK parity)."""

from __future__ import annotations

from apcore_cli.exit_codes import (
    EXIT_APPROVAL_DENIED,
    EXIT_APPROVAL_TIMEOUT,
    EXIT_CODES,
    EXIT_MODULE_EXECUTE_ERROR,
    EXIT_MODULE_NOT_FOUND,
    EXIT_SUCCESS,
    exit_code_for_error,
)
from apcore_cli.security.sandbox import ModuleExecutionError, ModuleNotFoundError


def test_exit_success_is_zero() -> None:
    """EXIT_SUCCESS must be 0 — POSIX success convention, parity with TS/Rust."""
    assert EXIT_SUCCESS == 0


def test_all_exit_constants_are_integers() -> None:
    """Every EXIT_* constant exposed by the module must be an int."""
    import apcore_cli.exit_codes as ec

    # EXIT_CODES is the dict mapping; the scalar constants all start with EXIT_
    # but we filter that one out by name.
    exit_names = [name for name in dir(ec) if name.startswith("EXIT_") and name != "EXIT_CODES"]
    assert exit_names, "expected at least one EXIT_* scalar constant"
    for name in exit_names:
        value = getattr(ec, name)
        assert isinstance(value, int), f"{name} must be int, got {type(value).__name__}"


def test_exit_code_for_module_not_found() -> None:
    """ModuleNotFoundError instances map to EXIT_MODULE_NOT_FOUND (44)."""
    assert exit_code_for_error(ModuleNotFoundError("missing.module")) == EXIT_MODULE_NOT_FOUND
    assert EXIT_MODULE_NOT_FOUND == 44


def test_exit_code_for_module_execution_error() -> None:
    """ModuleExecutionError instances map to EXIT_MODULE_EXECUTE_ERROR (1)."""
    assert exit_code_for_error(ModuleExecutionError("boom")) == EXIT_MODULE_EXECUTE_ERROR
    assert EXIT_MODULE_EXECUTE_ERROR == 1


def test_exit_code_for_unknown_error_falls_back_to_one() -> None:
    """Unmapped errors fall back to generic exit 1 — parity with TS default."""
    assert exit_code_for_error(RuntimeError("unknown failure")) == 1


def test_exit_codes_dict_contains_known_mappings() -> None:
    """EXIT_CODES dict exposes the expected error->code mapping."""
    assert EXIT_CODES[ModuleNotFoundError] == EXIT_MODULE_NOT_FOUND
    assert EXIT_CODES[ModuleExecutionError] == EXIT_MODULE_EXECUTE_ERROR
    # Approval pairs share exit code 46 per protocol spec.
    assert EXIT_APPROVAL_DENIED == 46
    assert EXIT_APPROVAL_TIMEOUT == 46
