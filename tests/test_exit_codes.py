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


class TestApcoreWireCodeFallback:
    """apcore signals failure with ``ModuleError`` subclasses carrying ``code``.

    None of the CLI's own exception classes match one, so before the wire-code
    fallback every apcore failure collapsed to exit 1 — including on the
    ``apcli`` system commands, whose ``_exit_on_system_error`` documents the
    canonical taxonomy. TS ``exitCodeForError`` and Rust
    ``map_apcore_error_to_exit_code`` both map by code; Python did not.
    """

    def test_schema_validation_error_from_apcore_maps_to_45(self) -> None:
        from apcore.errors import SchemaValidationError as ApcoreSchemaValidationError

        assert exit_code_for_error(ApcoreSchemaValidationError("bad input")) == 45

    def test_module_not_found_from_apcore_maps_to_44(self) -> None:
        from apcore.errors import ModuleNotFoundError as ApcoreModuleNotFoundError

        assert exit_code_for_error(ApcoreModuleNotFoundError("missing")) == 44

    def test_dependency_codes_map_to_44(self) -> None:
        """Cross-SDK parity: all three CLIs map both dependency failures to 44.

        apcore-cli-rust reached its catch-all arm for these and exited 1 until
        the same release; pinned here so the three maps cannot drift again.
        """
        assert exit_code_for_error(_wire("DEPENDENCY_NOT_FOUND")) == 44
        assert exit_code_for_error(_wire("DEPENDENCY_VERSION_MISMATCH")) == 44

    def test_unknown_wire_code_still_falls_back_to_one(self) -> None:
        class UnmappedError(Exception):
            code = "SOMETHING_UNMAPPED"

        assert exit_code_for_error(UnmappedError()) == EXIT_MODULE_EXECUTE_ERROR

    def test_non_string_code_attribute_is_ignored(self) -> None:
        class UnmappedError(Exception):
            code = 42

        assert exit_code_for_error(UnmappedError()) == EXIT_MODULE_EXECUTE_ERROR

    def test_acl_rule_error_maps_to_47_not_77(self) -> None:
        """T-ACL-30 / FE-14 §6.1.

        A malformed ACL file could not be *read* — a configuration fault. 77
        must stay reserved for an actual access decision, or a script branching
        on it would misreport a broken config as a permissions problem.
        """
        from apcore_cli.exit_codes import APCORE_ERROR_CODE_MAP

        assert APCORE_ERROR_CODE_MAP["ACL_RULE_ERROR"] == 47
        assert APCORE_ERROR_CODE_MAP["ACL_DENIED"] == 77
        assert exit_code_for_error(_wire("ACL_RULE_ERROR")) == 47

    def test_real_apcore_acl_rule_error_maps_to_47(self) -> None:
        """The mapping keys off the wire code the real apcore error carries."""
        from apcore.errors import ACLRuleError

        assert exit_code_for_error(ACLRuleError("bad rule")) == 47

    def test_cli_error_code_map_is_the_shared_map(self) -> None:
        """Two copies of a value set are two things that drift — cli.py's
        ``_ERROR_CODE_MAP`` and the fallback must be the same object."""
        from apcore_cli.cli import _ERROR_CODE_MAP
        from apcore_cli.exit_codes import APCORE_ERROR_CODE_MAP

        assert _ERROR_CODE_MAP is APCORE_ERROR_CODE_MAP


def _wire(code: str) -> Exception:
    """Build a stand-in for an apcore error carrying `code`."""

    class _ApcoreLikeError(Exception):
        pass

    err = _ApcoreLikeError(code)
    err.code = code
    return err
