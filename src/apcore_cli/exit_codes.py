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
# All four namespace/env errors share exit code 78 per protocol spec.
EXIT_CONFIG_NAMESPACE_RESERVED = 78
EXIT_CONFIG_NAMESPACE_DUPLICATE = 78
EXIT_CONFIG_ENV_PREFIX_CONFLICT = 78
EXIT_CONFIG_ENV_MAP_CONFLICT = 78
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


def exit_code_for_error(err: BaseException) -> int:
    """Return the configured exit code for an error instance.

    Falls back to ``1`` (generic execute error) when no entry in
    :data:`EXIT_CODES` matches. Mirrors TS ``exitCodeForError`` and the
    Rust ``ExitCode::from`` mapping.
    """
    for cls, code in EXIT_CODES.items():
        if isinstance(err, cls):
            return code
    return 1
