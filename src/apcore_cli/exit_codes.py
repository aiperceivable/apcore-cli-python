"""Exit code constants and helper for mapping error classes to process exit codes.

Mirrors apcore-cli-typescript ``src/errors.ts`` (``EXIT_CODES`` map and
``exitCodeForError``) and apcore-cli-rust ``src/lib.rs`` (``EXIT_*`` consts).
See ``apcore-cli/docs/features/core-dispatcher.md`` for the canonical exit-code
table.
"""

from __future__ import annotations

from apcore_cli.approval import ApprovalDeniedError, ApprovalTimeoutError
from apcore_cli.security.auth import AuthenticationError
from apcore_cli.security.config_encryptor import ConfigDecryptionError
from apcore_cli.security.sandbox import (
    ModuleExecutionError,
    ModuleNotFoundError,
    SchemaValidationError,
)

# ---------------------------------------------------------------------------
# Exit code constants — match the EXIT_* names in apcore-cli-rust src/lib.rs
# ---------------------------------------------------------------------------

EXIT_SUCCESS = 0
EXIT_MODULE_EXECUTE_ERROR = 1
EXIT_MODULE_TIMEOUT = 1
EXIT_INVALID_INPUT = 2
EXIT_MODULE_NOT_FOUND = 44
EXIT_MODULE_LOAD_ERROR = 44
EXIT_MODULE_DISABLED = 44
EXIT_DEPENDENCY_NOT_FOUND = 44
EXIT_DEPENDENCY_VERSION_MISMATCH = 44
EXIT_SCHEMA_VALIDATION_ERROR = 45
EXIT_APPROVAL_DENIED = 46
EXIT_APPROVAL_TIMEOUT = 46
EXIT_CONFIG_NOT_FOUND = 47
EXIT_CONFIG_INVALID = 47
EXIT_SCHEMA_CIRCULAR_REF = 48
EXIT_CONFIG_BIND_ERROR = 65
EXIT_CONFIG_MOUNT_ERROR = 66
EXIT_ERROR_FORMATTER_DUPLICATE = 70
EXIT_ACL_DENIED = 77
# Both namespace errors share exit code 78 per protocol spec.
EXIT_CONFIG_NAMESPACE_RESERVED = 78
EXIT_CONFIG_NAMESPACE_DUPLICATE = 78
EXIT_SIGINT = 130

# ---------------------------------------------------------------------------
# Map: error class -> exit code (mirrors TS exitCodeForError instanceof chain)
# ---------------------------------------------------------------------------

EXIT_CODES: dict[type[BaseException], int] = {
    ApprovalTimeoutError: EXIT_APPROVAL_TIMEOUT,
    ApprovalDeniedError: EXIT_APPROVAL_DENIED,
    AuthenticationError: EXIT_ACL_DENIED,
    ConfigDecryptionError: EXIT_CONFIG_INVALID,
    SchemaValidationError: EXIT_SCHEMA_VALIDATION_ERROR,
    ModuleNotFoundError: EXIT_MODULE_NOT_FOUND,
    ModuleExecutionError: EXIT_MODULE_EXECUTE_ERROR,
    KeyboardInterrupt: EXIT_SIGINT,
}


# ---------------------------------------------------------------------------
# Map: apcore wire error code -> exit code
# ---------------------------------------------------------------------------
# The CLI's own exception classes (above) never match an error raised by the
# apcore runtime, which signals failure through ``ModuleError`` subclasses
# carrying a ``code`` attribute. Matching on that code is what keeps the
# canonical taxonomy (44 / 45 / 46 / 47 / 77) intact for every dispatch path —
# ``apcli`` system commands included, which route apcore errors straight to
# :func:`exit_code_for_error`. Mirrors the ``codeMap`` in TS ``exitCodeForError``
# and Rust ``cli::map_apcore_error_to_exit_code``.
APCORE_ERROR_CODE_MAP: dict[str, int] = {
    "MODULE_NOT_FOUND": EXIT_MODULE_NOT_FOUND,
    "MODULE_LOAD_ERROR": EXIT_MODULE_LOAD_ERROR,
    "MODULE_DISABLED": EXIT_MODULE_DISABLED,
    "DEPENDENCY_NOT_FOUND": EXIT_DEPENDENCY_NOT_FOUND,
    "DEPENDENCY_VERSION_MISMATCH": EXIT_DEPENDENCY_VERSION_MISMATCH,
    "SCHEMA_VALIDATION_ERROR": EXIT_SCHEMA_VALIDATION_ERROR,
    "SCHEMA_CIRCULAR_REF": EXIT_SCHEMA_CIRCULAR_REF,
    "APPROVAL_DENIED": EXIT_APPROVAL_DENIED,
    "APPROVAL_TIMEOUT": EXIT_APPROVAL_TIMEOUT,
    "APPROVAL_PENDING": EXIT_APPROVAL_DENIED,
    "CONFIG_NOT_FOUND": EXIT_CONFIG_NOT_FOUND,
    "CONFIG_INVALID": EXIT_CONFIG_INVALID,
    "MODULE_EXECUTE_ERROR": EXIT_MODULE_EXECUTE_ERROR,
    "MODULE_TIMEOUT": EXIT_MODULE_TIMEOUT,
    "ACL_DENIED": EXIT_ACL_DENIED,
    # FE-14 §6.1: a malformed ACL file is a *configuration* fault, not a
    # denial. 47 (CONFIG_INVALID) rather than 77 — 77 must stay reserved for
    # an actual access decision or scripts branching on it would misreport a
    # broken config as a permissions problem.
    "ACL_RULE_ERROR": EXIT_CONFIG_INVALID,
    # Config Bus errors (apcore >= 0.15.0)
    "CONFIG_NAMESPACE_RESERVED": EXIT_CONFIG_NAMESPACE_RESERVED,
    "CONFIG_NAMESPACE_DUPLICATE": EXIT_CONFIG_NAMESPACE_DUPLICATE,
    "CONFIG_ENV_PREFIX_CONFLICT": EXIT_CONFIG_NAMESPACE_DUPLICATE,
    "CONFIG_ENV_MAP_CONFLICT": EXIT_CONFIG_NAMESPACE_DUPLICATE,
    "CONFIG_MOUNT_ERROR": EXIT_CONFIG_MOUNT_ERROR,
    "CONFIG_BIND_ERROR": EXIT_CONFIG_BIND_ERROR,
    "ERROR_FORMATTER_DUPLICATE": EXIT_ERROR_FORMATTER_DUPLICATE,
}


def exit_code_for_error(err: BaseException) -> int:
    """Return the configured exit code for an error instance.

    Resolves in two steps, matching TS ``exitCodeForError`` and the Rust
    ``ExitCode::from`` mapping: first the CLI's own exception classes
    (:data:`EXIT_CODES`), then the apcore wire code the error carries
    (:data:`APCORE_ERROR_CODE_MAP`). Falls back to ``1`` (generic execute
    error) when neither matches.
    """
    for cls, code in EXIT_CODES.items():
        if isinstance(err, cls):
            return code

    # Fall back to the apcore wire code. None of the classes above match an
    # error raised by the apcore runtime, so without this every apcore failure
    # would collapse to 1 — losing the taxonomy that scripted callers read.
    wire_code = getattr(err, "code", None)
    wire_code = getattr(wire_code, "value", wire_code)
    if isinstance(wire_code, str) and wire_code in APCORE_ERROR_CODE_MAP:
        return APCORE_ERROR_CODE_MAP[wire_code]
    return 1
