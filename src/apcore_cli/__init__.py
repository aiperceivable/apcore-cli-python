"""apcore-cli: CLI adapter for the apcore module ecosystem."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _get_version

try:
    __version__ = _get_version("apcore-cli")
except PackageNotFoundError:
    __version__ = "unknown"

# Public API re-exports
from apcore_cli.approval import (
    ApprovalDeniedError,
    ApprovalTimeoutError,
    CliApprovalHandler,
    check_approval,
)
from apcore_cli.builtin_group import (
    APCLI_SUBCOMMAND_NAMES,
    RESERVED_GROUP_NAMES,
    ApcliConfig,
    ApcliGroup,
    ApcliGroupError,
    ApcliMode,
)
from apcore_cli.cli import (
    build_module_command,
    collect_input,
    set_audit_logger,
    set_docs_url,
    set_verbose_help,
    validate_module_id,
)
from apcore_cli.config import (
    DEFAULTS,
    ConfigResolver,
    register_config_namespace,
)
from apcore_cli.discovery import (
    register_describe_command,
    register_exec_command,
    register_list_command,
    register_validate_command,
)
from apcore_cli.exit_codes import (
    EXIT_ACL_DENIED,
    EXIT_APPROVAL_DENIED,
    EXIT_APPROVAL_TIMEOUT,
    EXIT_CODES,
    EXIT_CONFIG_BIND_ERROR,
    EXIT_CONFIG_INVALID,
    EXIT_CONFIG_MOUNT_ERROR,
    EXIT_CONFIG_NAMESPACE_DUPLICATE,
    EXIT_CONFIG_NAMESPACE_RESERVED,
    EXIT_CONFIG_NOT_FOUND,
    EXIT_DEPENDENCY_NOT_FOUND,
    EXIT_DEPENDENCY_VERSION_MISMATCH,
    EXIT_ERROR_FORMATTER_DUPLICATE,
    EXIT_INVALID_INPUT,
    EXIT_MODULE_DISABLED,
    EXIT_MODULE_EXECUTE_ERROR,
    EXIT_MODULE_LOAD_ERROR,
    EXIT_MODULE_NOT_FOUND,
    EXIT_MODULE_TIMEOUT,
    EXIT_SCHEMA_CIRCULAR_REF,
    EXIT_SCHEMA_VALIDATION_ERROR,
    EXIT_SIGINT,
    EXIT_SUCCESS,
    exit_code_for_error,
)
from apcore_cli.exposure import ExposureFilter
from apcore_cli.factory import create_cli
from apcore_cli.init_cmd import register_init_command
from apcore_cli.output import (
    format_exec_result,
    format_module_detail,
    format_module_list,
    resolve_format,
)
from apcore_cli.ref_resolver import resolve_refs
from apcore_cli.schema_parser import schema_to_click_options
from apcore_cli.security.audit import AuditLogger
from apcore_cli.security.auth import AuthenticationError, AuthProvider
from apcore_cli.security.config_encryptor import ConfigDecryptionError, ConfigEncryptor
from apcore_cli.security.sandbox import (
    CliModuleNotFoundError,
    ModuleExecutionError,
    ModuleNotFoundError,
    Sandbox,
    SchemaValidationError,
)
from apcore_cli.shell import configure_man_help, register_completion_command
from apcore_cli.strategy import register_pipeline_command
from apcore_cli.system_cmd import (
    register_config_command,
    register_disable_command,
    register_enable_command,
    register_health_command,
    register_reload_command,
    register_usage_command,
)

# Config Bus namespace registration (apcore >= 0.15.0).
# Side-effect at import time; helper is also exported for explicit re-registration.
register_config_namespace()

__all__ = [
    "__version__",
    # Factory
    "create_cli",
    # FE-13 builtin-group surface
    "ApcliConfig",
    "ApcliGroup",
    "ApcliGroupError",
    "ApcliMode",
    "APCLI_SUBCOMMAND_NAMES",
    "RESERVED_GROUP_NAMES",
    # FE-11 approval
    "CliApprovalHandler",
    "check_approval",
    # FE-12 exposure
    "ExposureFilter",
    # Schema / output / ref resolution
    "resolve_refs",
    "schema_to_click_options",
    "format_exec_result",
    "format_module_list",
    "format_module_detail",
    "resolve_format",
    # Config / security
    "ConfigResolver",
    "DEFAULTS",
    "register_config_namespace",
    "AuditLogger",
    "AuthProvider",
    "ConfigEncryptor",
    "Sandbox",
    # Core dispatcher (parity with TS/Rust embedder API).
    "build_module_command",
    "collect_input",
    "validate_module_id",
    "set_audit_logger",
    "set_verbose_help",
    "set_docs_url",
    # Per-subcommand registrar factories (parity with TS/Rust embedder API).
    "register_list_command",
    "register_describe_command",
    "register_exec_command",
    "register_validate_command",
    "register_init_command",
    "register_completion_command",
    "configure_man_help",
    "register_health_command",
    "register_usage_command",
    "register_enable_command",
    "register_disable_command",
    "register_reload_command",
    "register_config_command",
    "register_pipeline_command",
    # Error classes
    "ApprovalDeniedError",
    "ApprovalTimeoutError",
    "AuthenticationError",
    "ConfigDecryptionError",
    "ModuleExecutionError",
    "ModuleNotFoundError",
    # Deprecated alias kept for backward compat — prefer ModuleNotFoundError.
    # Will be removed in v0.10.0.
    "CliModuleNotFoundError",
    "SchemaValidationError",
    # Exit codes (parity with TS EXIT_CODES + Rust EXIT_* consts).
    "EXIT_CODES",
    "exit_code_for_error",
    "EXIT_SUCCESS",
    "EXIT_MODULE_EXECUTE_ERROR",
    "EXIT_MODULE_TIMEOUT",
    "EXIT_INVALID_INPUT",
    "EXIT_MODULE_NOT_FOUND",
    "EXIT_MODULE_LOAD_ERROR",
    "EXIT_MODULE_DISABLED",
    "EXIT_DEPENDENCY_NOT_FOUND",
    "EXIT_DEPENDENCY_VERSION_MISMATCH",
    "EXIT_SCHEMA_VALIDATION_ERROR",
    "EXIT_APPROVAL_DENIED",
    "EXIT_APPROVAL_TIMEOUT",
    "EXIT_CONFIG_NOT_FOUND",
    "EXIT_CONFIG_INVALID",
    "EXIT_SCHEMA_CIRCULAR_REF",
    "EXIT_CONFIG_BIND_ERROR",
    "EXIT_CONFIG_MOUNT_ERROR",
    "EXIT_ERROR_FORMATTER_DUPLICATE",
    "EXIT_ACL_DENIED",
    "EXIT_CONFIG_NAMESPACE_RESERVED",
    "EXIT_CONFIG_NAMESPACE_DUPLICATE",
    "EXIT_SIGINT",
]
