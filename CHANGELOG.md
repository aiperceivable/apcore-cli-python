# Changelog

All notable changes to apcore-cli (Python SDK) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.11.0] - 2026-09-02

Bumps the required `apcore` floor to `0.28.0` and `apcore-toolkit` to `0.10.2`, and fixes **three defects the 0.28.0 upgrade made reachable or visible**. Full suite: 815 passed, 5 xfailed (798 → 815; 17 new regression tests), verified in a throwaway venv built from the declared `dev` extra rather than the working interpreter. Each new test was confirmed to fail against the pre-fix behaviour rather than merely to pass against the new one.

**Why a minor rather than a patch.** Every fix below restores behaviour that was already specified, but two of them change what a working consumer observes, and this ecosystem's rule — stated in apcore's own 0.28.0 release note — is that such a change "must ship as a **minor** (or major) version bump, never a patch". A script branching on exit code `1` from an `apcli` system command now sees `45`; a caller doing `handler.request_approval(...)["status"]` now gets a `TypeError`. Neither was correct behaviour, and both were reachable.

### Fixed

- **The `apcli health` summary line reported "no data" for a project whose modules it had just listed.** apcore classifies module health in **four** tiers — `healthy` / `degraded` / `error` / `unknown` — and the tally iterated only the first three. `unknown` means "no calls recorded yet", which is the state every module in a fresh project is in, so the common case rendered a populated table above a total that denied it:

  ```
    probe.echo                   unknown      0.0%         --
  Summary: no data
  ```

  **Pre-existing, and not introduced by this upgrade** — all three SDKs have emitted `unknown` since the tier set existed. apcore 0.28.0 is what brought it into focus: `sys-health-summary.schema.json` had declared the enum as `["healthy", "degraded", "unhealthy"]`, a value **no SDK emits**, and the release corrects it to the four tiers actually produced, splitting the summary's `unhealthy` count field into `error` and `unknown`. With the canonical shape finally naming four tiers, rendering three is a plain omission. Fixed in all three SDKs together, with the tally now covering `unknown`; a genuinely empty tally still reads "no data".

- **The approval gate crashed on every gate-routed approval — `CliApprovalHandler` returned a mapping where the protocol requires an `ApprovalResult`.** apcore's `BuiltinApprovalGate` reads the handler's answer by attribute (`result.status`, `result.approved_by` in `builtin_steps.py`), so the handler's `{"status": "approved", ...}` dict raised `AttributeError` *inside* the gate and reached the caller as `MODULE_EXECUTE_ERROR`. Every one of the eight return paths was affected, so a module annotated `requires_approval: true` could not execute through the wired handler at all — including the `--yes` and `APCORE_CLI_AUTO_APPROVE=1` bypasses, which return early and therefore failed the same way.

  It survived because nothing exercised it: no test in this SDK called `request_approval`, and the class was only reachable through `executor.set_approval_handler` in `factory.py`. apcore-cli-rust converts through `cli_to_apcore_result` and apcore-cli-typescript's `ApprovalResult` is a structurally-typed interface, so neither had the defect — this was a Python-only divergence from a shape both other SDKs got right.

  **Pre-existing, but apcore 0.28.0 widens the blast radius.** Before 0.28.0 the gate fired only for a module whose *annotation* said `requires_approval: true`. Since spec v1.28.0 §6.9 the gate fires on the union of three sources, so an ACL rule carrying `approval: required` (§6.1.6) now routes calls to modules annotated `requires_approval: false` through the same broken path — the exact shape argument-scoped approval was added for (`git push --force` needs a human, `git push` does not).

  `_approval_result()` now constructs `apcore.approval.ApprovalResult`, falling back to the mapping shape when apcore is not importable so the handler stays usable in isolation.

- **Every apcore error from an `apcli` system command exited 1 instead of its canonical code.** `exit_code_for_error` matched only on the CLI's own exception classes, none of which an apcore-raised `ModuleError` subclass is an instance of, so the wire code it carries was never read. `system_cmd._exit_on_system_error` documents the canonical taxonomy (44 module-not-found, 45 schema-validation, 46 approval-denied, 47 config-invalid, 77 ACL-denied) and delivered `1` for all of them. TS `exitCodeForError` falls through to a `codeMap` and Rust `map_module_error_to_exit_code` reads `err.code`; Python had the map — in `cli.py` — and never consulted it from this path.

  **apcore 0.28.0 makes it newly reachable on a routine command.** `system.usage.summary` / `system.usage.module` now declare `"pattern": "^[1-9][0-9]*[hd]$"` on `period`, and 0.28.0 also stops `_DictSchemaAdapter.model_validate` being a pass-through, so dict-declared schemas are finally enforced. `apcore-cli apcli usage --period 0h` passes the flag through verbatim and now raises `SCHEMA_VALIDATION_ERROR` where it previously returned an empty window with exit 0 — and was reported as exit 1 rather than 45.

  `APCORE_ERROR_CODE_MAP` moves to `exit_codes.py` as the single source of truth (`cli._ERROR_CODE_MAP` is now an alias, so the two copies cannot drift), gains the `DEPENDENCY_NOT_FOUND` / `DEPENDENCY_VERSION_MISMATCH` entries TS already had, and `exit_code_for_error` falls back to it.

### Changed

- **`apcore>=0.28.0`, `apcore-toolkit>=0.10.2`.** apcore-toolkit 0.10.2 is a dependency-tracking release with no source change; its stable surface consumed by the CLI (`format_*`, `DisplayResolver`, `BindingLoader`, `RegistryWriter`) is unchanged.

- **Exit-code map parity pinned across the three SDKs.** A mechanical three-way diff of the maps found `DEPENDENCY_NOT_FOUND` and `DEPENDENCY_VERSION_MISMATCH` mapped to 44 here and in the other non-Rust SDK, but falling through to 1 in apcore-cli-rust (fixed in its 0.11.0). Both codes now carry an explicit assertion here too, so the three maps cannot drift again without a test going red.

- **The new handler tests drive their coroutines with `asyncio.run` instead of `@pytest.mark.asyncio`.** This suite declares no async plugin — `pytest-asyncio` is absent from the `dev` extra and there is not one other async test in it — so a marker-based test is collected happily and then fails at run time with *"async def functions are not natively supported"* on any machine that does not happen to have the plugin installed for other reasons. Caught by CI, not locally, which is the point: the local interpreter had it and the declared dependency set does not.

### Notes

- **What the 0.27.0 → 0.28.0 delta does *not* touch.** The CLI never constructs or loads an `ACL`, never calls `check()` / `check_access()` (so §6.8.1's fail-closed legacy boolean does not reach it), never reads an `AuditEntry`, and never builds an `ACLRule` (so §6.1.5's `effect` value closure and the new `approval` field are inert here). `ExecutionPolicy.resolve()`'s new keyword-only call-site parameters are additive and the CLI configures no policy. `p99_latency_ms` changing value is display-only in `_format_usage_summary_tty`.
- **`Executor.validate()` now reports the governance-effective requirement (§7.9.5), which is an improvement the CLI gets for free.** `apcli validate` and the `--dry-run` path forward `result.requires_approval` verbatim, so a call gated only by an ACL argument-scoped rule is now correctly reported as needing approval. Pinned by `test_preflight_reports_the_acl_sourced_requirement`.
- **The CLI's own pre-execution `check_approval(module_def, ...)` still reads the static annotation**, which since spec v1.29.0 no longer means "no consent needed". That is correct here rather than a defect: the pre-check is an ergonomic early prompt, and a call it skips still meets the executor's gate, which now composes all three sources and routes to the same `CliApprovalHandler`. Verified end-to-end by `TestApprovalGateEndToEnd`.

## [0.10.5] - 2026-08-17

Patch release. Bumps the required `apcore` floor to `0.27.0` to track the aligned apcore 0.27.0 release (2026-08-14). **No source changes** — the full test suite passes unchanged (798 passed, 5 xfailed) against apcore 0.27.0.

The apcore 0.26.0 → 0.27.0 delta is BREAKING at the spec level, but touches no surface the CLI consumes — verified against the release notes and the actual call sites:

- **Middleware semantics** — `before_step` failure is now terminal/non-recoverable, `after_step` fires after a recovered step body, `state.outputs` excludes the current step in `after_step`. The CLI never constructs or configures middleware or pipelines; it only calls `executor.call` / `executor.validate` / `call_with_trace` with strategy names. No exposure.
- **ACL-failed `validate()` introspection** — a failed `acl` check now withholds `module_preflight` / `module_preview` checks and `predicted_changes`. The CLI's `executor.validate()` calls (per-module `--dry-run`, `apcli validate`, and the `system.health.summary` probe) consume only `valid` / `checks` / `requires_approval`; the probe discards its result. Behavior stays correct (ACL-denied calls surface exit 77 as before).
- **`Registry.register` metadata `dependencies` persistence** — the CLI never calls `register()` directly (module registration is via `discover()` / toolkit `RegistryWriter`); it only reads `len(descriptor.dependencies)` for the `--deps` column. No exposure.
- **Schema conversion (A23)** — object detection, nullable `anyOf` wrapping, sorted `required` are SDK-conversion rules. The CLI runs its **own** schema→Click converter (`schema_parser.py`) on the descriptor's `input_schema`; it already strips `{"type": "null"}` branches from `anyOf` (v0.10.3) and treats `required` order-insensitively. No exposure.
- **`pipeline.configure` 4-field set / `requires`/`provides` non-configurable** — the CLI never configures pipelines; a host config carrying other keys now fails at load (spec-mandated strictness, upstream concern).
- **No type coercion at the module boundary** — CLI flag parsing (Click) produces typed values; only `--input -` JSON passthrough with string-typed numeric values now fails `SCHEMA_VALIDATION_ERROR` instead of being silently coerced (spec-mandated strictness).
- **Removed/renamed API surface** — Python `apcore.middleware.namespace_keys` removed, `SchemaValidator` default flip, `TraceContext.inject()` raises `InvalidParentIdError` — none used by the CLI.

## [0.10.4] - 2026-07-14

Patch release. Bumps the required `apcore` floor to `0.26.0` to align the ecosystem on the 0.26.0 governance layer (Execution Policy, governance events, no-handler fail-loud — additive, no breaking changes). No code or API changes; all 798 tests pass (5 xfailed) unmodified against apcore 0.26.0.

## [0.10.3] - 2026-07-07

Patch release: fixes Pydantic v2 `Optional[...]` field type mapping and bumps the required `apcore-toolkit` floor. All 798 tests pass (5 xfailed).

### Fixed

- **`Optional[X]` fields mapped to the wrong Click type.** Pydantic v2 emits `Optional[X]` as `{"anyOf": [<X>, {"type": "null"}]}` rather than `{"type": "X"}`, so every optional CLI option fell through to `click.STRING` with a spurious "no type specified" warning — `Optional[bool]` lost its flag, `Optional[int]` lost `INT`, and the `*_file` convention stopped applying. `_map_type` (`schema_parser.py`) and the `anyOf`/`oneOf` branch of `_resolve_node` (`ref_resolver.py`) now recover the dominant non-null type from `anyOf`. JSON Schema list-form types (`{"type": ["string", "null"]}`) are likewise reduced to their first non-null string, so they no longer risk an unhashable-key `TypeError` in the type lookup. Regression tests added in `tests/test_schema_parser.py` and `tests/test_ref_resolver.py`.

### Changed

- Bumped the required `apcore-toolkit` floor to `>= 0.10.0` (which centralizes `RegistryWriter` field mapping and adds the shared annotation-preservation conformance verifier — additive, no breaking changes).

## [0.10.2] - 2026-06-24

### Changed

- **Required runtime bumped to apcore 0.25.0 and apcore-toolkit 0.9.1.** Dependency
  floors in `pyproject.toml` raised from `apcore>=0.24.0` / `apcore-toolkit>=0.8.1`
  to `apcore>=0.25.0` / `apcore-toolkit>=0.9.1`, tracking the aligned apcore 0.25.0
  and apcore-toolkit 0.9.1 releases. **No source changes** — the full test suite
  passes unchanged.

  Neither delta touches a surface the CLI consumes:
  - **apcore 0.24.0 → 0.25.0** adds config-driven ACL discovery (`acl.root`
    activation + `ACL.discover`), auto-wired only by the `APCore` bootstrap and
    skipped when the caller supplies its own `Executor`. The CLI never constructs
    `APCore`, so discovery does not engage; the change is backward-compatible
    regardless (a missing `acl.root` attaches no ACL, preserving the no-enforcement
    default).
  - **apcore-toolkit 0.8.1 → 0.9.1** is a bug-fix release; the Python fix (OpenAPI
    parser handling integer status-code keys and explicit `null`) lives in
    `extract_output_schema`, which the CLI does not call. The toolkit surface the
    CLI uses (`RegistryWriter`, `BindingLoader`, `DisplayResolver`, `format_*`) is
    unchanged.

## [0.10.1] - 2026-06-15

### Changed

- **Required runtime bumped to apcore 0.24.0 and apcore-toolkit 0.8.1.** Dependency
  floors in `pyproject.toml` raised from `apcore>=0.22.0` / `apcore-toolkit>=0.8.0`
  to `apcore>=0.24.0` / `apcore-toolkit>=0.8.1`, tracking the aligned apcore 0.24.0
  and apcore-toolkit 0.8.1 releases. **No source changes** — the full test suite
  passes unchanged.

  The apcore 0.22.0 → 0.24.0 delta does not touch any surface the CLI consumes:
  - **Per-instance `ToggleState` isolation (#71)** — the CLI never constructs
    `ToggleState`/`APCore` nor calls the free `is_module_disabled()`; module
    toggling is delegated to the `system.control.toggle_feature` module via
    `Executor.call()`.
  - **Default AI error-recovery metadata (#70)** and **error `details` snake_case
    key alignment (A-D-019)** — the CLI reads error fields (`details`, `suggestion`,
    `ai_guidance`, `retryable`, `user_fixable`) via `getattr()` and passes `details`
    through verbatim, so it is agnostic to both new defaults and inner-key casing.
  - **`Registry.list()` / `get_definition()` / `Executor.call()` / `call_with_trace()`
    / `set_approval_handler()`** signatures are unchanged across the delta; descriptor
    fields are read defensively with fallbacks.
  - Out of scope and unused by the CLI: `CircuitBreakerMiddleware`, `A2ASubscriber`,
    DLQ/`original_event`, `apcore.Config.validate()`, `Context.create()`, redaction
    utilities, `EventEmitter`.

## [0.10.0] - 2026-05-18

### Changed — BREAKING

- **Removed graceful ImportError fallbacks for `apcore` and `apcore-toolkit` (resolves 6.2).** Both packages are declared as required runtime dependencies in `pyproject.toml` (`apcore>=0.21.0`, `apcore-toolkit>=0.7.0`) — the prior `try: from apcore_toolkit import X; except ImportError: logger.warning(...); return` pattern in `factory.py` and the `try: from apcore import Config; except (ImportError, AttributeError): return False` pattern in `config.py` were self-contradictory: the dep was hard-required by the package manifest but soft-degraded at runtime. The fallbacks have been removed; missing or too-old `apcore` / `apcore-toolkit` now fails fast at import time with `ModuleNotFoundError`, matching the manifest contract.
  - Sites cleaned up: `factory.py:581-616` (DisplayResolver/RegistryWriter/ConventionScanner/BindingLoader), `output.py:144-146/270-272` (format_module/format_modules), `config.py:32-35` (apcore.Config).
  - Toolkit symbols are now imported once at the top of `factory.py` and `output.py`.
  - The `_TOOLKIT_MISSING_HINT` constant in `output.py` is removed (no longer reachable).
- **Tests updated**: two tests that asserted the removed fallback behaviour (`test_toolkit_missing_logs_warning_and_returns`, `test_binding_loader_missing_warns_but_continues`) are deleted; the import-time failure mode is covered by Python's standard `ModuleNotFoundError`. Remaining `_apply_toolkit_integration` tests now patch `apcore_cli.factory.{BindingLoader,DisplayResolver,RegistryWriter,ConventionScanner}` instead of `apcore_toolkit.X` (standard "patch where the name is used" Python mock convention now that the imports are static).

### Migration

- If your environment previously relied on the soft-degrade behaviour (apcore-cli running with `apcore-toolkit` uninstalled), install the package: `pip install apcore-toolkit>=0.7.0`. With the manifest already declaring it required, this should already be satisfied by any standard `pip install apcore-cli` invocation.

## [0.9.0] - 2026-05-13

### Added

- **`tests/conformance/test_snake_case_kwargs.py`** — runs the cross-language Algorithm C-SNAKE fixture (`apcore-cli/conformance/fixtures/snake-case-kwargs/cases.json`) against `build_module_command` via `click.testing.CliRunner`. Five cases verify that schema property names with underscores (`has_solution`, `sort_by`, `sort_order`) survive the round trip from CLI parse to the input dict received by `executor.call`. No source change required — click natively maps `--has-solution` to `has_solution`; the Python SDK is the parity reference for the parallel TypeScript fix. Surfaced as part of the cross-SDK regression coverage gap audit.

### Fixed (2026-05-13 — cross-SDK audit D10/D11/D1)

- **`ConfigEncryptor` LOGNAME key-derivation chain** (D10-001 / D11-003) — PBKDF2 username fallback was `USER → USERNAME → "unknown"` (3-tier); now `USER → LOGNAME → USERNAME → "unknown"` (4-tier) matching the spec and Rust. On hosts where `USER` is unset but `LOGNAME` is set (cron, `sudo -i`, container init), ciphertext written by the Python SDK now round-trips correctly with the Rust SDK. `src/apcore_cli/security/config_encryptor.py:96, 165`.
- **`ConfigEncryptor.store` keyring write-failure not wrapped** (D11-004) — raw `keyring.set_password` exceptions now caught and re-raised as `ConfigDecryptionError`, matching TypeScript and Rust. `src/apcore_cli/security/config_encryptor.py:31`.
- **`ref_resolver` only descended into `properties`** (D11-001) — recursive schema walk now visits every dict-valued child (items, additionalProperties, patternProperties, if/then/else, not, contains, propertyNames), matching TypeScript and Rust. `$ref` under array schemas and conditional schemas is now resolved. `src/apcore_cli/ref_resolver.py:142`.
- **`ref_resolver` copy-on-write visited-set** (D11-002) — now uses a single mutable set with remove-on-unwind, allowing diamond `$ref` patterns (two sibling schemas referencing the same `$def`) to resolve correctly. `src/apcore_cli/ref_resolver.py:71`.
- **`AuditLogger._get_user` uses real UID instead of effective UID** (D11-010) — switched from `os.getuid()` to `os.geteuid()` so audit records reflect the privileges the process actually runs with under `sudo` / setuid binaries. Matches Rust (`geteuid`) and TypeScript (`os.userInfo`). `src/apcore_cli/security/audit.py:77`.
- **`check_approval` ignores `APCORE_CLI_APPROVAL_TIMEOUT` env var** (D11-012) — `CliApprovalHandler` and the legacy `check_approval()` wrapper now honor the env var when no explicit timeout is passed (precedence: constructor arg > env var > 60 s default). Matches TypeScript. `src/apcore_cli/approval.py`.
- **`exec --dry-run` crashes with `AttributeError` when executor lacks `validate`** (D11-013) — guarded with `hasattr(executor, "validate")`; falls back to synthetic `{"valid": True}` matching TypeScript. `src/apcore_cli/discovery.py:378`.
- **`CliApprovalHandler.request_approval` missing `requires_approval=False` short-circuit** (D11-014) — now returns `approved/not_required` when the request explicitly carries `requires_approval=False`, matching Rust. `src/apcore_cli/approval.py:66`.
- **CLI brand string in auth error messages** (D11-006) — remediation strings now say `apcli config set auth.api_key` (canonical FE-13 name) instead of `apcore-cli config set auth.api_key`. `src/apcore_cli/security/auth.py`.
- **`reconvert_enum_values` missing from public re-export** (D1-W2) — added to `__init__.py` import block and `__all__`. Embedders can now `from apcore_cli import reconvert_enum_values` for parity with TypeScript and Rust.
- **Ref-resolver error hierarchy missing from public re-export** (D1-W3) — `CircularRefError`, `MaxDepthExceededError`, `UnresolvableRefError`, `RefResolverError` added to `__init__.py` import block and `__all__`. Parity with TypeScript `index.ts:82-84`.
- **`DEFAULT_BUILTIN_GROUP_NAME` missing from public re-export** (D1 re-audit) — added to `__init__.py`. Parity with Rust `lib.rs:190`.
- **Dead exit-code constants removed** (D9-W1) — `EXIT_CONFIG_ENV_PREFIX_CONFLICT` and `EXIT_CONFIG_ENV_MAP_CONFLICT` (both = 78, zero callers) deleted from `src/apcore_cli/exit_codes.py`.
- **Unused `pytest-asyncio` dev dependency removed** (D6) — the package was declared but never exercised; no async tests exist. Removed from `[project.optional-dependencies].dev`.

### Fixed

- **CSV `--format csv` Python-repr bug** — `csv.DictWriter` was called with `{k: str(v) for k, v in row.items()}` which emitted Python repr `{'k': 'v'}` (single quotes) for nested dict/list values. The output was not valid JSON and any downstream JSON parser would fail. Now delegates to `apcore_toolkit.format_csv(rows)` which emits canonical compact JSON. `src/apcore_cli/output.py:149, 378`.
- **CSV heterogeneous-keys data loss** — header is now the union of keys across all rows (was first-row only via `list(rows[0].keys())`).
- **CSV line terminator** — now `\r\n` per RFC 4180.
- **JSONL canonical form** — now compact (no spaces between separators), matching the cross-SDK contract. Tests updated.

### Changed

- **User-visible help/man/completion text no longer leaks the `apcore` framework name** to end users of downstream CLIs built on apcore-cli. Affected strings: `init` group description (`Scaffold new apcore modules` → `Scaffold new modules`, `init_cmd.py:45`), `--extensions-dir` option help (`Path to apcore extensions directory.` → `Path to extensions directory.`, `factory.py:460`), zsh/fish completion descriptions for `exec` (`Execute an apcore module` → `Execute a module`, `shell.py:130, 211`), and man-page `ENVIRONMENT` section text (`shell.py:299, 314, 319, 458`) — drops `apcore` from the descriptive copy (`Path to the apcore extensions directory` → `Path to the extensions directory`, `Global apcore logging verbosity` → `Global logging verbosity`, `API key for authenticating with the apcore registry` → `API key for authenticating with the registry`). Logger names, source comments, module docstrings, and environment-variable identifiers (`APCORE_*`) are unchanged — only descriptive copy that appears in `--help`, shell completion, and `man` output. Cross-SDK parity with TypeScript 0.8.2 and Rust 0.8.1.

### Changed (breaking CLI surface)

- **Global `--verbose` flag renamed to `--all-options`** — The help-display flag is now `--all-options`; use `apcore-cli module --help --all-options` to reveal hidden built-in options. `verbose` is removed from the reserved schema property names set — module schemas may now freely define `verbose: boolean` for runtime output control. Internal API: `set_verbose_help()` renamed to `set_all_options_help()`; module-level global `_verbose_help` renamed to `_all_options_help`. Tracked in [apcore-cli#21](https://github.com/aiperceivable/apcore-cli/issues/21).

### Changed (breaking dependency semantics)

- **`apcore-toolkit` promoted from optional extra to REQUIRED runtime dependency** (`>=0.7.0`). The previous `pip install 'apcore-cli[toolkit]'` extras pattern is retained as a no-op for backward compat with install scripts, but the toolkit is now always installed alongside apcore-cli. All `--format` operations route through the toolkit's reference implementation for csv/jsonl/markdown/skill.

### Why

See ADR-09 in `apcore-cli/docs/tech-design.md` for the byte-equivalent toolkit-delegated tier rationale.


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

### Deprecated

- **`CliModuleNotFoundError` alias** — the symbol still resolves to
  `ModuleNotFoundError` (see D1-002 in Changed) but is scheduled for
  removal in v0.10.0. Update imports to
  `from apcore_cli import ModuleNotFoundError`.

### Security

- **D10-001 — `Sandbox` per-stream output cap** (`sandbox.py:155`). The previous
  implementation summed `stdout + stderr` against a single `max_output_bytes`
  budget — a runaway child writing only to stderr could starve the stdout
  budget and vice versa, and the diagnostic on overflow did not name the
  offending stream. Each stream now has an independent byte budget matching
  Rust and TypeScript; the overflow error names the stream that tripped the
  cap.
- **D11-W2 — `Sandbox` switched from `subprocess.run` to `subprocess.Popen`
  with threaded chunked reads** (`sandbox.py:155`). `capture_output=True`
  buffered the entire child stdio into parent memory before the cap was
  checked, so a child producing GBs of output could OOM the parent before
  the limit was enforced. The new implementation streams stdout/stderr
  through reader threads with bounded buffers and kills the child as soon
  as either stream exceeds its cap. Memory consumption is now bounded by
  `2 × max_output_bytes` regardless of child output volume.
- **D11-003 — `ConfigEncryptor` v1 decryption honours
  `APCORE_CLI_CONFIG_PASSPHRASE`** (`config_encryptor.py:128`). `_aes_decrypt_v1`
  hard-coded the host:user material, so v1 ciphertext encrypted by the Rust
  or TypeScript SDKs under a passphrase failed to decrypt on Python.
  Decryption now tries the passphrase-derived key first when the env var is
  set, falling back to host:user material — matching TypeScript
  `aesDecryptV1`. Cross-SDK config bundles are now portable.
- **D11-008 — `AuditLogger._get_user` fallback chain now includes `LOGNAME`**
  (`audit.py:66`). The canonical chain per `security.md` (D11-W1) is
  `getlogin → pwd.getpwuid → USER → LOGNAME → USERNAME → unknown`. Python
  previously skipped `LOGNAME`, so audit-log `user` fields diverged from
  Rust/TS on hosts where only `LOGNAME` is set (some container runtimes,
  cron jobs).

### Added

- **`builtin_group_name="apcli"` kwarg on `create_cli`** — downstream branded CLIs that embed apcore-cli can now expose the built-in commands under a custom namespace (e.g. `mycorp-cli admin health` instead of `mycorp-cli apcli health`). `ApcliGroup` gains a `name` parameter (with property accessor) threaded through `from_cli_config` / `from_yaml` / `_build`. Default `"apcli"` is unchanged. Validated against `/^[a-z][a-z0-9_-]*$/`; invalid values exit 2. `RESERVED_GROUP_NAMES` collision check now consults `GroupedModuleGroup._reserved_group_names` (instance attribute, defaults to the static frozenset; factory replaces with the resolved name). Env var `APCORE_CLI_APCLI` and config keys `apcli.*` deliberately do NOT rename — they are apcore-cli-internal toggles, not user-facing. Cross-SDK parity with TypeScript `createCli({ builtinGroupName })`. New `DEFAULT_BUILTIN_GROUP_NAME` constant exported from `apcore_cli.builtin_group`.
- **`_exit_on_system_error(e)` helper in `system_cmd.py`** — centralizes the canonical error→exit-code mapping for system-management subcommands, replacing 7 sites that previously used bare `sys.exit(1)` (audit D11-B-002, see Fixed).
- **5 new tests in `tests/test_builtin_group.py`** — `TestBuiltinGroupRename` class covers default name, custom name via both factories, validation of valid/invalid name shapes (5 valid + 6 invalid forms each).
- **D1-001 — 13 `register_*_command` factories + `configure_man_help`
  re-exported from `apcore_cli` package root**. Embedders that compose
  their own root command tree no longer need to reach into private
  submodules (`apcore_cli.commands.list_cmd`, etc.). All TS/Rust
  `register_*` counterparts now have a Python public-API equivalent.
- **D1-003 — `apcore_cli.exit_codes` module** with 24 `EXIT_*` integer
  constants, an `EXIT_CODES` mapping dict, and an `exit_code_for_error()`
  helper. Mirrors TS `errors.ts` `EXIT_CODES` + `exitCodeForError` and
  Rust `src/lib.rs` `EXIT_*` constants. Embedders can now map exceptions
  to documented exit codes without re-implementing the table.
- **D1-007 — `format_module_list`, `format_module_detail`,
  `resolve_format` re-exported from package root**. The
  output-formatter feature spec declares these as Contracts; previously
  only `format_exec_result` was public.
- **D1-W1 — `APCLI_SUBCOMMAND_NAMES` re-exported from `apcore_cli`**.
  Matches Rust `lib.rs` and is now in `__all__` for static-analysis
  tooling.
- **D1-W2 — `ApcliConfig` TypedDict** added to the public surface,
  mirroring the TypeScript type alias and Rust struct so embedders have
  a static contract for the `apcli.*` config block.
- **D1-W3 — `register_config_namespace()` helper + module-level
  `DEFAULTS` constant** in `config.py`. The package still registers the
  namespace at import time, but embedders can now invoke the helper
  explicitly (parity with `apcore-cli-typescript`).
- **D1-W5 — Core dispatcher embedder API re-exported from package
  root**: `build_module_command`, `collect_input`, `validate_module_id`,
  `set_audit_logger`, `set_verbose_help`, `set_docs_url`. Embedders no
  longer have to import from `apcore_cli.cli` directly. Matches Rust
  `lib.rs:186-190` and TS `index.ts:18`. New `tests/test_public_api.py`
  pins the surface against future drift.
- **D1-info-1 — typed `ApcliGroupError` exception**
  (`builtin_group.py:107`). Cross-SDK parity with Rust `ApcliGroupError`;
  embedders previously had no stable error class to match on for
  built-in-group config validation. `ApcliGroupError(ValueError)`
  preserves backwards compat — existing `except ValueError` callers
  still catch it. The invalid-name regex check in `__init__` now raises
  `ApcliGroupError`. Re-exported from `apcore_cli`.

### Fixed

- **D11-B-006 — `discovery.py:208` sort direction inverted**. `apcli list --sort calls|errors|latency` now defaults to DESCENDING (highest call count first) per spec T-LST-04, matching Rust `discovery.rs:209` and TypeScript `discovery.ts:186`. Previously the user's raw `--reverse` flag (default False) was passed directly to `sort_modules_by_usage(..., reverse=...)`, producing ASCENDING output by default — the inverse of the spec. Fix passes `reverse=not reverse` for the data path AND adds a re-sort at the call site for the audit-log-empty fallback so id-fallback continues to default ASCENDING per spec.
- **D11-B-002 — `system_cmd.py` collapsed every error to exit 1**. The 7 `except Exception as e: sys.exit(1)` sites bypassed Python's own `_ERROR_CODE_MAP` (canonical 44/46/47/77) — scripted operators could not distinguish "module not found" from "ACL denied" from generic failure. All 7 sites now route through the new `_exit_on_system_error(e)` helper which calls `exit_code_for_error(e)` from `apcore_cli.exit_codes`. The 4 audit-log entries previously hardcoding `exit_code=1` now log the resolved code.
- **D11-NEW-005 — RESERVED_PROPERTY_NAMES no longer raises generic `ValueError`**. `schema_to_click_options` previously raised `ValueError` when a schema property collided with a built-in CLI option — opaque to scripted callers and inconsistent with the neighbour flag-collision branch (which already exited 48). Now writes a user-facing `Error:` line to stderr and calls `sys.exit(48)` per spec, matching TS `process.exit(EXIT_CODES.SCHEMA_CIRCULAR_REF)` and Rust `CliError::SchemaParserFailure → EXIT_SCHEMA_CIRCULAR_REF`. Tests tightened from `pytest.raises((ValueError, Exception))` to `pytest.raises(SystemExit)` with `code == 48` assertion.
- **D9-NEW-002 — `ref_resolver.py` `allOf required` not deduplicated**. `_resolve_node`'s `allOf` branch concatenated parent `required` + each branch's `required` without dedup, producing duplicate entries in the merged schema's `required` array. JSON Schema validators ignore duplicates so observable validation behaviour was unchanged, but cross-SDK byte-comparison tooling (and the `anyOf`/`oneOf` paths, which already deduped) flagged the divergence. Fix: explicit seen-set dedup preserving first-seen order, matching TS `[...new Set(...)]` and Rust `merge_allof`.
- **D10-003 — `build_module_command` leaked `RefResolverError`
  tracebacks** (`cli.py:538`). The `resolve_refs` catch clause re-raised
  unchanged, so callers saw a Python traceback instead of a clean
  documented exit code. Now translates `CircularRefError` /
  `MaxDepthExceededError` to `sys.exit(48)` and `UnresolvableRefError`
  (plus generic `RefResolverError`) to `sys.exit(45)`, mirroring
  `schema_parser.py:111` and the Rust/TS contracts.
- **D11-NEW-003 — `ref_resolver` `max_depth` over-counted plain nested
  `properties`** (`ref_resolver.py`). `_resolve_node` previously
  incremented `depth + 1` when recursing into nested `properties`
  values, so a schema with >32 levels of nested objects (no `$ref` at
  all) was rejected with `MaxDepthExceededError`. The spec wording is
  "Maximum `$ref` resolution recursion depth" — `$ref` hops along a
  single chain, not total stack depth. `depth` is now only incremented
  on `$ref` traversal, aligning with Rust `ref_resolver.rs:297`. Also
  adds 4 regression tests for `anyOf`/`oneOf` sibling-required
  preservation and `anyOf` overlap dedup.
- **D10-info-1 — `APCORE_CLI_APCLI` env var not trimmed on read**
  (`builtin_group.py:414`). Spec invariant 2 requires the parser to be
  case-insensitive AND trim-on-read. Surrounding whitespace previously
  caused a silent Tier-3/Tier-4 fall-through. Now strips before
  lowercasing, matching Rust/TS.
- **D11-010 — `AuditLogger` write-failure warnings deduplicated**
  (`audit.py:55`). Previously warned on every failed write, flooding
  logs when an audit dir is unwritable. An instance flag now gates the
  warning so it fires once per logger instance, matching the TS
  `writeFailureWarned` flag.

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
- **D1-002 — `CliModuleNotFoundError` renamed to `ModuleNotFoundError`**
  for cross-language port-ability with TS / Rust `ModuleNotFoundError`.
  The class intentionally shadows `builtins.ModuleNotFoundError` inside
  the `apcore_cli` namespace. A deprecation alias
  `CliModuleNotFoundError = ModuleNotFoundError` is kept for backwards
  compatibility and will be removed in v0.10.0. Reverses the D2-001
  rename which predated the cross-SDK parity policy.
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
