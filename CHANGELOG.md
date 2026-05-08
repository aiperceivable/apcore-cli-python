# Changelog

All notable changes to apcore-cli (Python SDK) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [0.8.0] - 2026-05-08

### Removed

- **D9-001 — FE-13 §11.2 deprecation shims removed**. The 13 hidden root-level
  shims (`list`, `describe`, `exec`, `init`, `validate`, `health`, `usage`,
  `enable`, `disable`, `reload`, `config`, `completion`, `describe-pipeline`)
  installed by `_register_deprecation_shims` and the `__is_deprecation_shim__`
  collision-handling path in `extra_commands` wiring have been deleted along
  with the `_DEPRECATED_ROOT_COMMANDS` table. Use the canonical
  `apcli <command>` paths instead. Calls like `apcore-cli list` now exit
  non-zero with Click's "No such command" message — the warning window
  documented as "removed in v0.8" is closed.

### Added

- **`builtin_group_name="apcli"` kwarg on `create_cli`** — downstream branded CLIs that embed apcore-cli can now expose the built-in commands under a custom namespace (e.g. `mycorp-cli admin health` instead of `mycorp-cli apcli health`). `ApcliGroup` gains a `name` parameter (with property accessor) threaded through `from_cli_config` / `from_yaml` / `_build`. Default `"apcli"` is unchanged. Validated against `/^[a-z][a-z0-9_-]*$/`; invalid values exit 2. `RESERVED_GROUP_NAMES` collision check now consults `GroupedModuleGroup._reserved_group_names` (instance attribute, defaults to the static frozenset; factory replaces with the resolved name). Env var `APCORE_CLI_APCLI` and config keys `apcli.*` deliberately do NOT rename — they are apcore-cli-internal toggles, not user-facing. Cross-SDK parity with TypeScript `createCli({ builtinGroupName })`. New `DEFAULT_BUILTIN_GROUP_NAME` constant exported from `apcore_cli.builtin_group`.
- **`_exit_on_system_error(e)` helper in `system_cmd.py`** — centralizes the canonical error→exit-code mapping for system-management subcommands, replacing 7 sites that previously used bare `sys.exit(1)` (audit D11-B-002, see Fixed).
- **5 new tests in `tests/test_builtin_group.py`** — `TestBuiltinGroupRename` class covers default name, custom name via both factories, validation of valid/invalid name shapes (5 valid + 6 invalid forms each).

### Fixed

- **D11-B-006 — `discovery.py:208` sort direction inverted**. `apcli list --sort calls|errors|latency` now defaults to DESCENDING (highest call count first) per spec T-LST-04, matching Rust `discovery.rs:209` and TypeScript `discovery.ts:186`. Previously the user's raw `--reverse` flag (default False) was passed directly to `sort_modules_by_usage(..., reverse=...)`, producing ASCENDING output by default — the inverse of the spec. Fix passes `reverse=not reverse` for the data path AND adds a re-sort at the call site for the audit-log-empty fallback so id-fallback continues to default ASCENDING per spec.
- **D11-B-002 — `system_cmd.py` collapsed every error to exit 1**. The 7 `except Exception as e: sys.exit(1)` sites bypassed Python's own `_ERROR_CODE_MAP` (canonical 44/46/47/77) — scripted operators could not distinguish "module not found" from "ACL denied" from generic failure. All 7 sites now route through the new `_exit_on_system_error(e)` helper which calls `exit_code_for_error(e)` from `apcore_cli.exit_codes`. The 4 audit-log entries previously hardcoding `exit_code=1` now log the resolved code.
- **D11-NEW-005 — RESERVED_PROPERTY_NAMES no longer raises generic `ValueError`**. `schema_to_click_options` previously raised `ValueError` when a schema property collided with a built-in CLI option — opaque to scripted callers and inconsistent with the neighbour flag-collision branch (which already exited 48). Now writes a user-facing `Error:` line to stderr and calls `sys.exit(48)` per spec, matching TS `process.exit(EXIT_CODES.SCHEMA_CIRCULAR_REF)` and Rust `CliError::SchemaParserFailure → EXIT_SCHEMA_CIRCULAR_REF`. Tests tightened from `pytest.raises((ValueError, Exception))` to `pytest.raises(SystemExit)` with `code == 48` assertion.
- **D9-NEW-002 — `ref_resolver.py` `allOf required` not deduplicated**. `_resolve_node`'s `allOf` branch concatenated parent `required` + each branch's `required` without dedup, producing duplicate entries in the merged schema's `required` array. JSON Schema validators ignore duplicates so observable validation behaviour was unchanged, but cross-SDK byte-comparison tooling (and the `anyOf`/`oneOf` paths, which already deduped) flagged the divergence. Fix: explicit seen-set dedup preserving first-seen order, matching TS `[...new Set(...)]` and Rust `merge_allof`.

### Changed

- **`apcli system *` and `apcli strategy describe-pipeline` `--format` choices**
  expanded from `[table, json]` to `[table, json, csv, yaml, jsonl]`, matching
  the existing `apcli list` / `apcli exec` choice set. `markdown` and `skill`
  are deliberately excluded from these subcommands — their payloads are
  health / strategy results, not `ScannedModule` data. Issue
  [#20](https://github.com/aiperceivable/apcore-cli/issues/20).
- **Dependency bump**: requires `apcore >= 0.21.0` (was `>= 0.19.0`) and the
  optional `[toolkit]` extra now requires `apcore-toolkit >= 0.6` (was `>= 0.5`).
  Aligns with upstream `apcore 0.21.0` (Module.preview / PreflightResult.predicted_changes,
  ephemeral.* namespace pilot) and `apcore-toolkit 0.6.0` (surface-aware formatters).
  No CLI-visible behavioural breaks — apcore 0.20→0.21 deprecations
  (`TaskStore.put`/`save`, `TaskStatus.RETRYING`, `CircuitOpenError`) keep
  legacy aliases for one minor release; the cli does not call those surfaces directly.
- **Issue #19 — drop "apcore" branding from embedded-mode `--help`**:
  `create_cli()` now resolves the top-level CLI description from the new
  `description=` parameter (defaults to `f"{prog_name} CLI"`), the `apcli`
  subgroup advertises itself as `Built-in commands` rather than
  `apcore-cli built-in commands`, and the `--verbose` option / footer drop
  the trailing `apcore` from `(including built-in apcore options)`. Standalone
  bin entry (`apcore_cli/__main__.py:main()`) passes
  `description="<prog> — execute apcore modules from the command line"`
  explicitly so the standalone surface is unchanged.

### Added

- **`--format markdown` and `--format skill`** for `apcli list` and `apcli describe`
  (issue [#20](https://github.com/aiperceivable/apcore-cli/issues/20)). Both
  delegate to `apcore_toolkit.format_module(s)` (≥0.6) so the output is
  byte-identical to the same toolkit call in the TypeScript and Rust SDKs.
  `--format skill` produces vendor-neutral SKILL.md content directly loadable
  by Claude Code (`.claude/skills/<id>/SKILL.md`) and Gemini CLI
  (`.gemini/skills/<id>/SKILL.md`):

  ```bash
  apcli describe users.create --format skill > .claude/skills/users.create/SKILL.md
  ```

  A new internal adapter `_descriptor_to_scanned()` maps `ModuleDescriptor`
  (apcore registry) to `ScannedModule` (apcore-toolkit). A `ClickException` with
  a clear install hint is raised if the optional `[toolkit]` extra is missing.
- **Issue #18 — host-app `--version` opt-in**: new `version: str | None = None`
  parameter on `create_cli()`. When supplied, registers `-V/--version` with
  the host's version string. **When omitted, the `--version` flag is no
  longer registered** — embedded CLIs that do not opt in stop leaking the
  SDK's own version through `-V/--version`. The standalone bin entry
  passes `version=apcore_cli.__version__` explicitly so the
  `apcore-cli` binary's behaviour is preserved.
- **Issue #19 — `description: str | None = None`** on `create_cli()`.
- **Issue #17 — `system.usage` aggregator + `list --sort calls|errors|latency`**:
  new module `apcore_cli.system_usage` reads `~/.apcore-cli/audit.jsonl`,
  filters by period (default 24h), and returns per-module aggregates
  (`calls`, `errors`, `avg latency_ms`). `list --sort {calls,errors,latency}`
  now consults the aggregator instead of falling back to id-sort with a
  buried `logger.warning`. When the audit log has no entries in the period
  window the discovery layer prints a user-visible note to stderr
  (`note: no usage data available for --sort <field>; sorted by id. ...`)
  and falls back to id-sort. Module-protocol registration of
  `system.usage.summary` / `system.usage.module` as registry-callable
  built-ins is tracked as a follow-up — today the readers are invoked
  directly by the discovery layer.
- New file: `apcore_cli/system_usage.py`.

---

## [0.7.0] - 2026-04-23

### Changed

- **Dependency bump**: requires `apcore >= 0.18.0` (was `>= 0.17.1`). Aligns with upstream `apcore 0.18.0` and `apcore-toolkit 0.4.2` breaking changes.
- **`MAX_MODULE_ID_LENGTH` 128 → 192**: `validate_module_id()` and all references updated to the new 192-character limit introduced in `apcore 0.18.0` (`apcore.registry.registry.MAX_MODULE_ID_LENGTH`).
- **`describe-pipeline` renders `StrategyInfo`**: `executor.describe_pipeline(strategy)` now returns a `StrategyInfo` dataclass (`name`, `step_count`, `step_names`, `description`). `strategy.py` updated to use `StrategyInfo` fields; header line is `Pipeline: {info.name} ({info.step_count} steps)`. Falls back gracefully to the legacy `_resolve_strategy_name` path when `describe_pipeline` is unavailable.
- **CI — spec-repo checkout**: `.github/workflows/ci.yml` now checks out `aiperceivable/apcore-cli` into `.apcore-cli-spec/` and exposes it to `pytest` via `APCORE_CLI_SPEC_REPO`. Mirrors the pattern established in `apcore-python` / `apcore-cli-typescript`.

### Added

- **`create_cli(app=...)` parameter**: `create_cli()` accepts an optional `app: APCore` unified client (introduced in `apcore 0.18.0`). `app` is mutually exclusive with `registry`/`executor` (raises `ValueError`). When `app` is provided, `registry` and `executor` are extracted from `app.registry` and `app.executor`. Filesystem discovery is skipped if `app.registry` already contains registered modules; otherwise normal discovery proceeds into `app.registry`.
- **Cross-language conformance test harness** (`tests/conformance/`) consuming the shared apcli-visibility fixtures from the `aiperceivable/apcore-cli` spec repo (`conformance/fixtures/apcli-visibility/`). Behavioral assertions (apcli group visibility, registered subcommand set for `include`/`exclude` modes, always-registered `exec`) run today across all five canonical scenarios (`standalone-default`, `embedded-default`, `cli-override`, `env-override`, `yaml-include`). Byte-matching against `expected_help.txt` is marked `xfail` until Click's `HelpFormatter` is replaced with a canonical clap v4 / GNU-style emitter, tracked for parity with `apcore-cli-typescript/src/canonical-help.ts`.
- **`APCORE_CLI_SPEC_REPO` env var** — overrides the spec-repo lookup path for conformance fixtures. Defaults to a sibling checkout (`../apcore-cli/`). Tests are skipped (not failed) when the spec repo is absent.
- **FE-12: Module Exposure Filtering** — Declarative control over which discovered modules are exposed as CLI commands.
  - `ExposureFilter` class in `exposure.py` with `is_exposed(module_id)` and `filter_modules(ids)` methods.
  - Three modes: `all` (default), `include` (whitelist), `exclude` (blacklist) with glob-pattern matching.
  - `ExposureFilter.from_config(dict)` classmethod for loading from `apcore.yaml` `expose` section.
  - `create_cli(expose=...)` parameter accepting `dict` or `ExposureFilter` instance.
  - `list --exposure {exposed,hidden,all}` filter flag in discovery commands.
  - `GroupedModuleGroup._build_group_map()` integration: calls `ExposureFilter.is_exposed()` to filter command registration.
  - `ConfigResolver` gains `expose.*` config keys.
  - 4-tier config precedence: `CliConfig.expose` > `--expose-mode` CLI flag > env var > `apcore.yaml`.
  - Hidden modules remain invocable via `exec <module_id>`.
- New file: `exposure.py`.

---

## [0.6.0] - 2026-04-06

### Changed

- **Dependency bump**: requires `apcore >= 0.17.1` (was `>= 0.15.1`). Adds Execution Pipeline Strategy, Config Bus enhancements, Pipeline v2 declarative step metadata, `minimal` strategy preset.
- **Schema parser**: Required schema properties now correctly enforced at CLI option level (was silently optional).
- **Approval gate**: Fixed inverted logic in annotation type guard; `check_approval()` now accepts `timeout` parameter.

### Added

- **FE-11: Usability Enhancements** — 11 new capabilities:
  - `--dry-run` preflight mode via `Executor.validate()`. Standalone `validate` command.
  - System management commands: `health`, `usage`, `enable`, `disable`, `reload`, `config get`/`config set`. Graceful no-op when system modules unavailable.
  - Enhanced error output: structured JSON with `ai_guidance`, `suggestion`, `retryable`, `user_fixable`, `details`. TTY hides machine-only fields.
  - `--trace` pipeline visualization via `call_with_trace()`.
  - `CliApprovalHandler` class implementing apcore `ApprovalHandler` protocol, wired to `Executor.set_approval_handler()`. `--approval-timeout`, `--approval-token` flags.
  - `--stream` JSONL output via `Executor.stream()`.
  - Enhanced `list` command: `--search`, `--status`, `--annotation`, `--sort`, `--reverse`, `--deprecated`, `--deps`.
  - `--strategy` selection: `standard`, `internal`, `testing`, `performance`, `minimal`. `describe-pipeline` command.
  - Output format extensions: `--format csv|yaml|jsonl`, `--fields` dot-path field selection.
  - Multi-level grouping: `cli.group_depth` config key.
  - Custom command extension: `create_cli(extra_commands=[...])` with collision detection.
- New error code: `CONFIG_ENV_MAP_CONFLICT`.
- New config keys: `cli.approval_timeout` (60), `cli.strategy` ("standard"), `cli.group_depth` (1).
- New environment variables: `APCORE_CLI_APPROVAL_TIMEOUT`, `APCORE_CLI_STRATEGY`, `APCORE_CLI_GROUP_DEPTH`.
- New files: `system_cmd.py`, `strategy.py`.

---

## [0.5.1] - 2026-04-03

### Added
- **Pre-populated registry support** — `create_cli()` accepts optional `registry` and `executor` parameters. When a pre-populated `Registry` is provided, filesystem discovery is skipped entirely. This enables frameworks that register modules at runtime (e.g. apflow's bridge) to generate CLI commands from their existing registry without requiring an extensions directory.
- Passing `registry` alone auto-builds an `Executor`; passing `executor` without `registry` raises `ValueError`.

---

## [0.4.1] - 2026-03-30

### Fixed
- prevent click parameter mismatch by setting expose_value=False for the --man option

## [0.4.0] - 2026-03-29

### Added
- **Verbose help mode** — Built-in apcore options (`--input`, `--yes`, `--large-input`, `--format`, `--sandbox`) are now hidden from `--help` output by default. Pass `--help --verbose` to display the full option list including built-in options.
- **Universal man page generation** — `build_program_man_page()` generates a complete roff man page covering all registered commands. `configure_man_help()` adds `--help --man` support to any Click CLI, enabling downstream projects to get man pages for free.
- **Documentation URL support** — `set_docs_url()` sets a base URL for online docs. Per-command help shows `Docs: {url}/commands/{name}`, man page SEE ALSO includes `Full documentation at {url}`. No default — disabled when not set.

### Changed
- `build_module_command()` respects the global verbose help flag to control built-in option visibility.
- `--sandbox` is now always hidden from help (not yet implemented). Only four built-in options (`--input`, `--yes`, `--large-input`, `--format`) toggle with `--verbose`.
- Improved built-in option descriptions for clarity.

---

## [0.3.1] - 2026-03-27

### Added

- **DisplayResolver integration** — `__main__.py` integrates `DisplayResolver` from `apcore-toolkit` (optional) when `--binding` option is provided; gracefully skipped when not installed.
- **`init` to `BUILTIN_COMMANDS`** — `init` subcommand is now registered in the builtin commands set.
- **`APCORE_AUTH_API_KEY` to man page** — environment variable documented in generated roff man page.
- **Grouped shell completion with `_APCORE_GRP`** — bash/zsh/fish completion scripts now support two-level group/command completion via the `_APCORE_GRP` environment variable (`shell.py`).
- **Path traversal validation for `--dir` in `init` command** — rejects paths containing `..` segments to prevent directory escape (`init_cmd.py`).

### Fixed

- **`RegistryWriter` API call** — constructor now called without parameters; fixes `TypeError` introduced by upstream API change.

### Changed

- `apcore` dependency bumped to `>=0.14.0`.

---

## [0.3.0] - 2026-03-23

### Added

- **Display overlay routing** (§5.13) — `LazyModuleGroup` now reads `metadata["display"]["cli"]` for alias and description when building the command list and routing `get_command()`. Commands are exposed under their CLI alias instead of raw module_id.
  - `_alias_map`: built from `metadata["display"]["cli"]["alias"]` (with module_id fallback), enabling `apcore-cli alias-name` invocation.
  - `_descriptor_cache`: populated during alias map build to avoid double `registry.get_definition()` calls in `get_command()`.
  - `_alias_map_built` flag only set on successful build, allowing retry after transient registry errors.
- **Display overlay in JSON output** — `format_module_list(..., "json")` now reads `metadata["display"]["cli"]` for `id`, `description`, and `tags`, consistent with the table output branch.

### Changed

- `_ERROR_CODE_MAP.get(error_code, 1)`: guarded with `isinstance(error_code, str)` to prevent `None`-key lookup.
- Runtime companion: `apcore-toolkit >= 0.4.0` enables `DisplayResolver` and `ConventionScanner` (graceful fallback when not installed).

### Tests

- `TestDisplayOverlayAliasRouting` (6 tests): `list_commands` uses CLI alias, `get_command` by alias, cache hit path, module_id fallback, `build_module_command` alias and description.
- `test_format_list_json_uses_display_overlay`: JSON output uses display overlay alias/description/tags.
- `test_format_list_json_falls_back_to_scanner_when_no_overlay`: JSON output falls back to scanner values.

### Added (Grouped Commands — FE-09)

- **`GroupedModuleGroup(LazyModuleGroup)`** — organizes modules into nested `click.Group` subcommands based on namespace prefixes. Auto-groups by first `.` segment, with `display.cli.group` override from binding.yaml.
  - `_resolve_group()` — 3-tier group resolution: explicit `display.cli.group` > first `.` segment of CLI alias > top-level.
  - `_build_group_map()` — lazy, idempotent group map builder with builtin collision detection and shell-safe group name validation.
  - `format_help()` — collapsed root help with Commands, Modules, and Groups sections (with command counts).
- **`_LazyGroup(click.Group)`** — nested group that lazily builds subcommands from module descriptors.
- **`list --flat` flag** — opt-in flat display mode for `list` command; default is now grouped display.
- **`format_grouped_module_list()`** — Rich table output grouped by namespace.
- **Updated shell completions** — bash/zsh/fish completion scripts handle two-level group/command structure.

### Changed (Grouped Commands)

- `create_cli()` now uses `GroupedModuleGroup` instead of `LazyModuleGroup`.

### Tests (Grouped Commands)

- 48 new tests: `TestResolveGroup` (8+), `TestBuildGroupMap` (5+), `TestGroupedModuleGroupRouting` (7), `TestLazyGroupInner` (4), `TestGroupedHelpDisplay` (5), `TestCreateCliGrouped` (1), `TestGroupedE2E` (5), `TestGroupedDiscovery` (7+), `TestGroupedCompletion` (6).

### Added (Convention Module Discovery — §5.14)

- **`apcore-cli init module <id>`** — scaffolding command with `--style` (decorator, convention, binding) and `--description` options. Generates module templates in the appropriate directory.
- **`--commands-dir` CLI option** — path to a convention commands directory. When set, `ConventionScanner` from `apcore-toolkit` scans for plain functions and registers them as modules.

### Tests (Convention Module Discovery)

- 6 new tests in `tests/test_init_cmd.py` covering all three styles and options.

---

## [0.2.2] - 2026-03-22

### Changed
- Rebrand: aipartnerup → aiperceivable

## [0.2.1] - 2026-03-19

### Changed
- Help text truncation limit increased from 200 to 1000 characters (configurable via `cli.help_text_max_length` config key)
- `_extract_help`: added `max_length: int = 1000` parameter (`schema_parser.py`)
- `schema_to_click_options`: added `max_help_length: int = 1000` parameter (`schema_parser.py`)
- `build_module_command`: added `help_text_max_length: int = 1000` parameter, threaded through to schema parser (`cli.py`)
- `LazyModuleGroup`: constructor accepts `help_text_max_length: int = 1000`, passes to `build_module_command` (`cli.py`)
- `create_cli`: resolves `cli.help_text_max_length` from `ConfigResolver` and passes to `LazyModuleGroup` (`__main__.py`)
- `format_exec_result`: nested dict/list values in table mode now rendered with `json.dumps` instead of `str()` (`output.py`)

### Added
- `cli.help_text_max_length` config key (default: 1000) in `ConfigResolver.DEFAULTS` (`config.py`)
- `APCORE_CLI_HELP_TEXT_MAX_LENGTH` environment variable support for configuring help text max length
- `test_help_truncation_default`: tests default 1000-char truncation
- `test_help_no_truncation_within_limit`: tests no truncation at 999 chars
- `test_help_truncation_custom_max`: tests custom max_length parameter
- 263 tests (up from 261)

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
