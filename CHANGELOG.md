# Changelog

All notable changes to apcore-cli (Python SDK) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-03-16

### Added
- `APCORE_CLI_LOGGING_LEVEL` env var — CLI-specific log level that takes priority over `APCORE_LOGGING_LEVEL`; 3-tier precedence: `--log-level` flag > `APCORE_CLI_LOGGING_LEVEL` > `APCORE_LOGGING_LEVEL` > `WARNING` (`__main__.py`)
- `test_cli_logging_level_takes_priority_over_global` — verifies `APCORE_CLI_LOGGING_LEVEL=DEBUG` wins over `APCORE_LOGGING_LEVEL=ERROR`
- `test_cli_logging_level_fallback_to_global` — verifies fallback when CLI-specific var is unset
- `test_builtin_name_collision_exits_2` — schema property named `format` (or other reserved names) causes `build_module_command` to exit 2
- `test_exec_result_table_format` — `--format table` renders Rich Key/Value table to stdout
- `test_bash_completion_quotes_prog_name_in_directive` — verifies `shlex.quote()` applied to `complete -F` directive, not just embedded subshell
- `test_zsh_completion_quotes_prog_name_in_directives` — verifies `compdef` line uses quoted prog_name
- `test_fish_completion_quotes_prog_name_in_directives` — verifies `complete -c` lines use quoted prog_name
- 17 new tests (244 → 261 total)

### Changed
- `--log-level` accepted choices: `WARN` → `WARNING` (`__main__.py`)
- `schema_to_click_options`: schema-derived options now always have `required=False`; required fields marked `[required]` in help text instead of Click enforcement — allows `--input -` STDIN to supply required values without Click rejecting first (`schema_parser.py`)
- `format_exec_result`: now routes through `resolve_format()` and renders Rich table when `--format table` is specified; previously ignored its `format` parameter (`output.py`)
- `_generate_bash_completion`, `_generate_zsh_completion`, `_generate_fish_completion`: `shlex.quote()` applied to ALL prog_name positions in generated scripts (complete directives, compdef, complete -c), not only embedded subshell commands (`shell.py`)
- `check_approval`: removed unused `ctx: click.Context` parameter (`approval.py`)
- `set_audit_logger`: broadened type annotation from `AuditLogger` to `AuditLogger | None` (`cli.py`)
- `collect_input`: simplified redundant condition `if not raw or raw_size == 0:` → `if not raw:` (`cli.py`)
- Example `Input` models: all 7 modules updated with `Field(description=...)` on every field so CLI `--help` shows descriptive text for each flag

### Fixed
- **`--input -` STDIN blocked by Click required enforcement**: `schema_to_click_options` was generating `required=True` Click options; Click validated before the callback ran, rejecting STDIN-only invocations. Resolved by always using `required=False` and delegating required validation to `jsonschema.validate()` after input collection. Fixes all 6 `TestRealStdinPiping` failures.
- **`--log-level` had no effect**: `logging.basicConfig()` is a no-op after the first call; subsequent `create_cli()` calls in tests retained the prior handler's level. Fixed by calling `logging.getLogger().setLevel()` explicitly after `basicConfig()`.
- **`test_log_level_flag_takes_effect` false pass**: `--help` is an eager flag that exits before the group callback, so `--log-level DEBUG --help` never applied the log level. Test updated to use `completion bash` subcommand instead.
- **Shell completion directives not shell-safe**: prog names with spaces or special characters were unquoted in `complete -F`, `compdef`, and `complete -c` lines. Fixed by assigning `quoted = shlex.quote(prog_name)` and using it in all directive positions.
- **Audit `set_audit_logger(None)` type error**: type annotation rejected `None`; broadened to `AuditLogger | None`.
- **Test logger level leakage**: tests modifying root logger level affected subsequent tests; fixed with `try/finally` that restores the original level.

### Security
- `AuditLogger._hash_input`: now uses `secrets.token_bytes(16)` per-invocation salt before hashing, preventing cross-invocation input correlation via SHA-256 rainbow tables
- `build_module_command`: added reserved-name collision guard — exits 2 if a schema property (`input`, `yes`, `large_input`, `format`, `sandbox`) conflicts with a built-in CLI option name
- `_prompt_with_timeout` (SIGALRM path): wrapped in `try/finally` to guarantee signal handler restoration regardless of exit path

## [0.1.0] - 2026-03-15

### Added
- `--sandbox` flag for subprocess-isolated module execution (FE-05)
- `ModuleExecutionError` exception class for sandbox failures
- Windows approval timeout support via `threading.Timer` + `ctypes` (FE-03)
- Approval timeout clamping to 1..3600 seconds range (FE-03)
- Tag format validation (`^[a-z][a-z0-9_-]*$`) in `list --tag` (FE-04)
- `cli.auto_approve` config key with `False` default (FE-07)
- Extensions directory readability check with exit code 47 (FE-01)
- Missing required property warning in schema parser (FE-02)
- DEBUG log `"Loading extensions from {path}"` before registry discovery (FE-01)
- `TYPE_CHECKING` imports for proper type annotations (`Registry`, `Executor`, `ModuleDescriptor`, `ConfigResolver`, `AuditLogger`)
- `_get_module_id()` helper for `canonical_id`/`module_id` resolution
- `APCORE_AUTH_API_KEY` and `APCORE_CLI_SANDBOX` to README environment variables table
- `--sandbox` to README module execution options table
- CHANGELOG.md
- Core Dispatcher (FE-01): `LazyModuleGroup`, `build_module_command`, `collect_input`, `validate_module_id`
- Schema Parser (FE-02): `schema_to_click_options`, `_map_type`, `_extract_help`, `reconvert_enum_values`
- Ref Resolver (FE-02): `resolve_refs`, `_resolve_node` with `$ref`, `allOf`, `anyOf`, `oneOf` support
- Config Resolver (FE-07): `ConfigResolver` with 4-tier precedence (CLI > Env > File > Default)
- Approval Gate (FE-03): `check_approval`, `_prompt_with_timeout` with TTY detection and Unix SIGALRM
- Discovery (FE-04): `list` and `describe` commands with tag filtering and TTY-adaptive output
- Output Formatter (FE-08): `format_module_list`, `format_module_detail`, `format_exec_result` with Rich rendering
- Security Manager (FE-05): `AuthProvider`, `ConfigEncryptor` (keyring + AES-256-GCM), `AuditLogger` (JSON Lines), `Sandbox` (subprocess isolation)
- Shell Integration (FE-06): bash/zsh/fish completion generators, roff man page generator
- 8 example modules: `math.add`, `math.multiply`, `text.upper`, `text.reverse`, `text.wordcount`, `sysutil.info`, `sysutil.env`, `sysutil.disk`
- 244 tests (unit, integration, end-to-end)
- CI workflow with pytest and coverage
- Pre-commit hooks configuration
