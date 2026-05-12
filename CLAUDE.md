# CLAUDE.md — apcore-cli-python

## Build & Test

- `pytest` — run all tests. **Must pass before considering any task complete.**
- `pytest --cov` — run with coverage report.
- `ruff check .` — lint check.
- `ruff format .` — format all code. **Run after every code change.**

## Code Style

- Python 3.11+ with `from __future__ import annotations`.
- All code must pass `ruff check` and `ruff format --check`.
- Type annotations on all public function signatures.
- Use `click.echo()` for user-facing output, `logger.*` for debug/diagnostic output.
- Prefer `sys.exit(code)` with exit code constants over raising exceptions for CLI errors.

## Project Conventions

- Spec repo (single source of truth): `../apcore-cli/docs/`
- Package structure: `src/apcore_cli/` with `__init__.py` exporting `__version__` only.
- Entry point: `apcore_cli.__main__:main`.
- Security modules live in `src/apcore_cli/security/` sub-package.
- ConfigResolver.DEFAULTS values are Python-typed (str, int, bool).
- Tests organized by module: `tests/test_<module>.py`, security tests in `tests/test_security/`.

## Environment

- Python >= 3.11
- Key dependencies: click >= 8.1, rich >= 13.0, jsonschema >= 4.20, pyyaml >= 6.0, keyring >= 24, cryptography >= 41
- Runtime: apcore >= 0.21.0 (v0.9.0 bump, was 0.19.0 at v0.7.0)
- Runtime (required, v0.9.0+): apcore-toolkit >= 0.7.0. The `[toolkit]` extras group is retained as a no-op for downstream install scripts.
- Dev: pytest, pytest-asyncio, pytest-cov, mypy, ruff

## v0.9.0 Conventions

- Public surface (`__init__.py`): `__version__`, `create_cli`, `ExposureFilter`,
  `ApcliGroup`, `ApcliMode`, `RESERVED_GROUP_NAMES`, `CliApprovalHandler`,
  `resolve_refs`, `schema_to_click_options`, `format_exec_result`,
  `set_verbose_help`, `set_docs_url`, `set_audit_logger`,
  `ConfigResolver`, `AuditLogger`, `AuthProvider`, `ConfigEncryptor`, `Sandbox`,
  plus error classes (AuthenticationError, ConfigDecryptionError,
  ModuleExecutionError, ApprovalTimeoutError, ApprovalDeniedError).
  Non-listed symbols (e.g. `GroupedModuleGroup`) must be imported via full
  submodule path (e.g., `from apcore_cli.cli import GroupedModuleGroup`).
- ExposureFilter + `expose=` kwarg on create_cli (FE-12).
- `extra_commands=[...]` kwarg on create_cli as the FE-11 extension point (with
  collision detection against RESERVED_GROUP_NAMES — `BUILTIN_COMMANDS` retired v0.7.0).
- `builtin_group_name="apcli"` kwarg on create_cli (v0.8.0+) for embed-API renaming (FE-13).
- Default click Group class is `GroupedModuleGroup` (multi-level grouping since v0.3.0).
- All apcore-cli commands live exclusively under the `apcli` group (FE-13). Root-level
  deprecation shims were removed in v0.9.0 (deprecated in v0.8.x).
- `--verbose` global flag renamed to `--all-options` in v0.9.0; `set_verbose_help(bool)` is
  the programmatic equivalent (internal name retained for back-compat; Rust uses `set_all_options_help`).
- `verbose` removed from reserved schema property names set in v0.9.0; modules may now
  define `verbose: boolean` freely.
- apcore-toolkit promoted from optional to REQUIRED runtime dep in v0.9.0; csv/jsonl/markdown/skill
  output formats now toolkit-delegated (ADR-09).
- `system_cmd` module registers runtime system commands (health/usage/enable/disable/
  reload/config) — FE-11.
- `strategy` module registers describe-pipeline + --strategy flag — FE-11.
- `validate` module + --dry-run flag — FE-11.
- `CliApprovalHandler` async protocol (request_approval/check_approval) — FE-11 §3.5.1.
- Config Bus namespace registration at package import time (apcore >= 0.15.0).
- New env vars (v0.6.0): APCORE_CLI_APPROVAL_TIMEOUT, APCORE_CLI_STRATEGY, APCORE_CLI_GROUP_DEPTH.
- New config keys (v0.6.0): cli.approval_timeout, cli.strategy, cli.group_depth.
- New exit codes from apcore 0.17.1: CONFIG_BIND_ERROR (65), CONFIG_MOUNT_ERROR (66),
  ERROR_FORMATTER_DUPLICATE (70), CONFIG_NAMESPACE_* (78).
