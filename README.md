<div align="center">
  <img src="https://raw.githubusercontent.com/aiperceivable/apcore-cli/main/apcore-cli-logo.svg" alt="apcore-cli logo" width="200"/>
</div>

# apcore-cli

Terminal adapter for apcore. Execute AI-Perceivable modules from the command line.

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://python.org)
[![Tests](https://github.com/aiperceivable/apcore-cli-python/actions/workflows/ci.yml/badge.svg)](https://github.com/aiperceivable/apcore-cli-python/actions/workflows/ci.yml)

| | |
|---|---|
| **Python SDK** | [github.com/aiperceivable/apcore-cli-python](https://github.com/aiperceivable/apcore-cli-python) |
| **Spec repo** | [github.com/aiperceivable/apcore-cli](https://github.com/aiperceivable/apcore-cli) |
| **apcore core** | [github.com/aiperceivable/apcore](https://github.com/aiperceivable/apcore) |

**apcore-cli** turns any [apcore](https://github.com/aiperceivable/apcore)-based project into a fully featured CLI tool — with **zero code changes** to your existing modules.

```
┌──────────────────┐
│  django-apcore   │  <- your existing apcore project (unchanged)
│  flask-apcore    │
│  ...             │
└────────┬─────────┘
         │  extensions directory
         v
┌──────────────────┐
│   apcore-cli     │  <- just install & point to extensions dir
└───┬──────────┬───┘
    │          │
    v          v
 Terminal    Unix
 Commands    Pipes
```

## Design Philosophy

- **Zero intrusion** -- your apcore project needs no code changes, no imports, no dependencies on apcore-cli
- **Zero configuration** -- point to an extensions directory, everything is auto-discovered
- **Pure adapter** -- apcore-cli reads from the apcore Registry; it never modifies your modules
- **Unix-native** -- JSON output for pipes, rich tables for terminals, STDIN input, shell completions

## Installation

```bash
pip install apcore-cli
```

Requires Python 3.11+, `apcore >= 0.21.0`, and `apcore-toolkit >= 0.7.0` (now a **required** runtime dependency as of v0.9.0 — previously optional; see the [tech-design ADR-09](https://github.com/aiperceivable/apcore-cli/blob/main/docs/tech-design.md) for the byte-equivalent toolkit-delegated tier rationale). The `[toolkit]` extras group is retained as a no-op for backward compat:

```bash
pip install "apcore-cli[toolkit]"   # equivalent to plain `pip install apcore-cli`
```

## Quick Start

### Try it now

The repo includes 8 example modules you can run immediately:

```bash
git clone https://github.com/aiperceivable/apcore-cli-python.git
cd apcore-cli-python
pip install -e ".[dev]"

# Run a module
apcore-cli --extensions-dir examples/extensions math add --a 5 --b 10
# {"sum": 15}

# List all modules
apcore-cli --extensions-dir examples/extensions apcli list --format json

# Run all examples
bash examples/run_examples.sh
```

See [Examples](#examples) for the full list of example modules and usage patterns.

### Zero-code approach

If you already have an apcore-based project with an extensions directory:

```bash
# Execute a module
apcore-cli --extensions-dir ./extensions math add --a 42 --b 58

# Or set the env var once
export APCORE_EXTENSIONS_ROOT=./extensions
apcore-cli math add --a 42 --b 58
```

All modules are auto-discovered. CLI flags are auto-generated from each module's JSON Schema.

### Programmatic approach (Python API)

```python
from apcore_cli import create_cli

# Build the CLI from an extensions directory (auto-discovers modules)
cli = create_cli(extensions_dir="./extensions")
cli(standalone_mode=True)
```

#### Pre-populated registry

Frameworks that register modules at runtime (e.g. apflow's bridge) can pass a pre-populated `Registry` directly, skipping filesystem discovery entirely:

```python
from apcore_cli import create_cli

# registry is already populated by your framework
cli = create_cli(registry=registry, prog_name="myapp")
cli(standalone_mode=True)

# Executor is auto-built from the registry if omitted.
# You can also provide your own:
cli = create_cli(registry=registry, executor=executor, prog_name="myapp")
```

#### Python API

The `apcore_cli` package re-exports the following public surface (see [`src/apcore_cli/__init__.py`](src/apcore_cli/__init__.py) for the canonical `__all__`):

| Export | Description |
|--------|-------------|
| `__version__` | Package version string |
| `create_cli(...)` | Factory that builds a ready-to-invoke `click.Group`. See the [`create_cli` reference](https://github.com/aiperceivable/apcore-cli) in the docs site for the full 14-parameter signature: `extensions_dir`, `prog_name`, `commands_dir`, `binding_path`, `registry`, `executor`, `extra_commands`, `app`, `expose`, **`apcli`** (FE-13 P0 break), `allowed_prefixes`, `version`, `description`, **`builtin_group_name`** (default `"apcli"`, v0.8.0+). |
| `ApcliGroup`, `ApcliMode`, `RESERVED_GROUP_NAMES` | FE-13 built-in `apcli` group surface (P0 break in v0.8.0). |
| `ExposureFilter` | Declarative filter controlling which modules are exposed by the CLI (FE-12). |
| `CliApprovalHandler`, `check_approval` | TTY-aware HITL approval handler / helper (FE-11). |
| `ConfigResolver` | 4-tier config precedence resolver (CLI flag > env var > config file > default). |
| `AuditLogger` | Append-only JSON Lines audit logger (`~/.apcore-cli/audit.jsonl`). |
| `AuthProvider` | API-key authentication provider (keyring-backed). |
| `ConfigEncryptor` | AES-256-GCM helper for encrypted config blobs. |
| `Sandbox` | Subprocess sandbox for isolated module execution. |
| `resolve_refs`, `schema_to_click_options`, `format_exec_result` | Schema/output helper functions. |
| `ApprovalDeniedError`, `ApprovalTimeoutError`, `AuthenticationError`, `ConfigDecryptionError`, `ModuleExecutionError`, `CliModuleNotFoundError`, `SchemaValidationError` | Typed error classes raised by the SDK (mapped to documented exit codes). |

**Exposure filtering** — restrict which modules are visible at the CLI layer without touching the underlying registry:

```python
from apcore_cli import create_cli, ExposureFilter

# Only expose admin.* modules
cli = create_cli(
    registry=registry,
    executor=executor,
    expose=ExposureFilter(mode="include", include=["admin.*"]),
)

# Or pass a config dict (equivalent)
cli = create_cli(
    registry=registry,
    executor=executor,
    expose={"mode": "exclude", "exclude": ["debug.*", "test.*"]},
)
```

`ExposureFilter` supports `mode="all"` (default), `"include"`, and `"exclude"`, plus `ExposureFilter.from_config(config)` for dict-based construction, `.is_exposed(module_id)`, and `.filter_modules(module_ids)`.

**Injecting custom commands** — pass `extra_commands=[...]` to attach arbitrary Click commands alongside the auto-generated module commands (FE-11 extension point):

```python
cli = create_cli(registry=registry, executor=executor, extra_commands=[my_custom_click_cmd])
```

Or build the CLI with a pre-populated registry and executor and full control over the `apcli` built-in group:

```python
from apcore import Registry, Executor
from apcore_cli import create_cli, ApcliMode

registry = Registry(extensions_dir="./extensions")
registry.discover()
executor = Executor(registry)

cli = create_cli(
    registry=registry,
    executor=executor,
    apcli=ApcliMode.ALL,  # or "all" / "none" / {"mode": "include", "include": [...]}
)
cli(standalone_mode=True)
```

> **Note:** `apcore_cli.cli.LazyModuleGroup` and `GroupedModuleGroup` are advanced internal classes used by `create_cli`. They are subject to change between releases — prefer `create_cli(...)` for stable embedding.

## Adding Custom Commands

### Fastest way (30 seconds)

```bash
apcore-cli apcli init module ops.deploy -d "Deploy to environment"
# Edit the generated file, add your logic
```

### Zero-import way (convention discovery)

Drop a plain Python function into `commands/`:

```python
# commands/deploy.py
def deploy(env: str, tag: str = "latest") -> dict:
    """Deploy the app to the given environment."""
    return {"status": "deployed", "env": env}
```

Then run with `--commands-dir commands/`:

```bash
apcore-cli --commands-dir commands/ deploy deploy --env prod
```

The `init module` command supports three styles via `--style`:
- **convention** (default) — generates a plain Python function in the commands directory
- **decorator** — generates a `@module`-decorated function in the extensions directory
- **binding** — generates a `.binding.yaml` file

## Integration with Existing Projects

### Typical apcore project structure

```
your-project/
├── extensions/          <- modules live here
│   ├── math/
│   │   └── add.py
│   ├── text/
│   │   └── upper.py
│   └── ...
├── your_app.py          <- your existing code (untouched)
└── ...
```

### Adding CLI support

No changes to your project. Just install and run:

```bash
pip install apcore-cli
apcore-cli --extensions-dir ./extensions apcli list
apcore-cli --extensions-dir ./extensions math add --a 5 --b 10
```

### STDIN piping (Unix pipes)

```bash
# Pipe JSON input
echo '{"a": 100, "b": 200}' | apcore-cli math add --input -
# {"sum": 300}

# CLI flags override STDIN values
echo '{"a": 1, "b": 2}' | apcore-cli math add --input - --a 999
# {"sum": 1001}

# Chain with other tools
apcore-cli sysutil info | jq '.os, .hostname'
```

## CLI Reference

```
apcore-cli [OPTIONS] COMMAND [ARGS]
```

### Global Options

| Option | Default | Description |
|--------|---------|-------------|
| `--extensions-dir` | `./extensions` | Path to apcore extensions directory |
| `--log-level` | `WARNING` | Logging: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--version` | | Show version and exit |
| `--help` | | Show help and exit |
| `--all-options` | | Show hidden built-in options in `--help` output |
| `--man` | | Print man page to stdout (use with `--help`) |

### Built-in Commands

apcore-cli ships with 13 built-in commands, all accessible under the `apcli` subgroup (e.g. `apcore-cli apcli list`). Root-level shims were removed in v0.9.0 (they emitted deprecation warnings in v0.8.x). Use `apcore-cli apcli <subcommand>`.

**Module invocation**

| Command | Description |
|---------|-------------|
| `apcli exec <module_id>` | Execute a module (delegates to `Executor.call()`) |
| `apcli list` | List registered modules (supports `--tag`/`--search`/`--status`/`--annotation`/`--sort`/`--reverse`/`--deprecated`/`--deps` filters) |
| `apcli describe <module_id>` | Show full module metadata and schema |

**System management** (FE-11, v0.6.0)

| Command | Description |
|---------|-------------|
| `apcli config get/set <key>` | Read or update runtime config values |
| `apcli health [<module_id>]` | Show module health status |
| `apcli usage [<module_id>]` | Show module usage statistics |
| `apcli enable <module_id>` | Enable a disabled module |
| `apcli disable <module_id>` | Disable a module at runtime |
| `apcli reload <module_id>` | Hot-reload a module from disk |

**Workflow**

| Command | Description |
|---------|-------------|
| `apcli validate <module_id>` | Preflight-check a module without executing it (`--dry-run`) |
| `apcli describe-pipeline` | Show execution pipeline steps for a strategy (FE-11) |
| `apcli init module <module_id>` | Scaffold a new module (`--style decorator\|convention\|binding`) |

**Shell integration**

| Command | Description |
|---------|-------------|
| `apcli completion <shell>` | Generate shell completion script (bash/zsh/fish) |

The full-program man page is reachable via the root flag combination `apcore-cli --help --man` (powered by `configure_man_help()`); there is no standalone `apcli man` subcommand.

### Module Execution Options

When executing a module (e.g. `apcore-cli math add`), these built-in options are available (hidden by default; pass `--help --all-options` to display them):

| Option | Description |
|--------|-------------|
| `--input -` | Read JSON input from STDIN |
| `--yes` / `-y` | Bypass approval prompts |
| `--large-input` | Allow STDIN input larger than 10MB |
| `--format` | Output format: `{json, table, csv, yaml, jsonl, markdown, skill}`. **v0.9.0:** `csv` and `jsonl` are byte-identical across SDKs (delegated to `apcore-toolkit.format_csv` / `format_jsonl`); fixes the prior `str(v)` Python-repr bug for nested values. |
| `--sandbox` | Run module in subprocess sandbox *(not yet implemented)* |
| `--dry-run` | Run preflight checks without executing (FE-11, v0.6.0) |
| `--trace` | Emit execution pipeline trace (v0.6.0) |
| `--stream` | Stream results line-by-line for stream-capable modules (v0.6.0) |
| `--strategy <name>` | Override execution strategy: `standard`/`internal`/`testing`/`performance`/`minimal` (v0.6.0) |
| `--fields <csv>` | Select specific output fields using dot-path notation (v0.6.0) |
| `--approval-timeout <seconds>` | Override approval timeout (default 60) (v0.6.0) |
| `--approval-token <token>` | Provide a pre-obtained approval token (v0.6.0) |

Schema-generated flags (e.g. `--a`, `--b`) are added automatically from the module's `input_schema`.

The `list` command gained several discovery filters in v0.6.0: `--search`, `--status`, `--annotation`, `--sort`, `--reverse`, `--deprecated`, and `--deps`.

### Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Module execution error |
| `2` | Invalid CLI input |
| `44` | Module not found / disabled / load error |
| `45` | Schema validation error |
| `46` | Approval denied or timed out |
| `47` | Configuration error |
| `48` | Schema circular reference |
| `77` | ACL denied |
| `130` | Execution cancelled (Ctrl+C) |

## Configuration

apcore-cli uses a 4-tier configuration precedence:

1. **CLI flag** (highest): `--extensions-dir ./custom`
2. **Environment variable**: `APCORE_EXTENSIONS_ROOT=./custom`
3. **Config file**: `apcore.yaml`
4. **Default** (lowest): `./extensions`

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `APCORE_EXTENSIONS_ROOT` | Path to extensions directory | `./extensions` |
| `APCORE_CLI_AUTO_APPROVE` | Set to `1` to bypass all approval prompts | *(unset)* |
| `APCORE_CLI_LOGGING_LEVEL` | CLI-specific log level (takes priority over `APCORE_LOGGING_LEVEL`) | `WARNING` |
| `APCORE_LOGGING_LEVEL` | Global apcore log level (fallback when `APCORE_CLI_LOGGING_LEVEL` is unset) | `WARNING` |
| `APCORE_AUTH_API_KEY` | API key for remote registry authentication | *(unset)* |
| `APCORE_CLI_SANDBOX` | Set to `1` to enable subprocess sandboxing | *(unset)* |
| `APCORE_CLI_HELP_TEXT_MAX_LENGTH` | Maximum characters for CLI option help text before truncation | `1000` |
| `APCORE_CLI_APPROVAL_TIMEOUT` | Approval-gate timeout in seconds (v0.6.0) | `60` |
| `APCORE_CLI_STRATEGY` | Default execution strategy (v0.6.0) | `standard` |
| `APCORE_CLI_GROUP_DEPTH` | Depth of automatic command grouping by `.` segments (v0.6.0) | `1` |
| `APCORE_CLI_APCLI` | FE-13 visibility for the `apcli` built-in group: `all`, `none`, `include`, `exclude`, or `auto` (v0.8.0). Overrides the `apcli:` block in `apcore.yaml`. | `auto` |

### Config File (`apcore.yaml`)

```yaml
extensions:
  root: ./extensions
logging:
  level: DEBUG
sandbox:
  enabled: false
cli:
  help_text_max_length: 1000
  approval_timeout: 60     # v0.6.0
  strategy: standard       # v0.6.0
  group_depth: 1           # v0.6.0
apcli:                     # v0.8.0 (FE-13)
  mode: all                # all | none | include | exclude
  include: []              # used when mode == include
  exclude: []              # used when mode == exclude
```

### `apcli` built-in group visibility (FE-13)

> **P0 breaking change in v0.8.0** — all built-ins were moved under the `apcli` group; the `BUILTIN_COMMANDS` constant has been retired.

The visibility of the `apcli` group is resolved through a 4-tier decision chain:

1. **`create_cli(apcli=...)` kwarg** (highest) — accepts an `ApcliMode` enum, a string (`"all"`, `"none"`, `"include"`, `"exclude"`), or a dict (`{"mode": "include", "include": ["list", "describe"]}`).
2. **`APCORE_CLI_APCLI` env var** — `all`, `none`, `include`, `exclude`, or `auto`.
3. **`apcli:` block in `apcore.yaml`** — see the example above.
4. **Embedded vs. standalone default** (lowest) — standalone `apcore-cli` defaults to `all`; CLIs embedded via `create_cli(app=...)` default to `none` so that host applications opt in explicitly. The `auto` sentinel selects this default and is intended for env/config use only — it is not a user-facing `ApcliMode` value.

The four user-visible modes are:

| Mode | Behavior |
|------|----------|
| `all` | All 13 `apcli` subcommands are exposed. |
| `none` | The `apcli` group is hidden entirely. |
| `include` | Only the subcommands listed in `include` are exposed. |
| `exclude` | All subcommands except those listed in `exclude` are exposed. |

## Features

- **Auto-discovery** -- all modules in the extensions directory are found and exposed as CLI commands
- **Display overlay** -- `metadata["display"]["cli"]` controls CLI command names, descriptions, and guidance per module (§5.13); set via `binding_path` in `create_cli()` / `fastapi-apcore`
- **Grouped commands** -- modules with dots in their names are auto-grouped into nested subcommands (`apcore-cli product list` instead of `apcore-cli product.list`); `display.cli.group` in binding.yaml overrides the auto-detected group
- **Auto-generated flags** -- JSON Schema `input_schema` is converted to `--flag value` CLI options with type validation
- **Boolean flag pairs** -- `--verbose` / `--no-verbose` from `"type": "boolean"` schema properties
- **Enum choices** -- `"enum": ["json", "csv"]` becomes `--format json` with Click validation
- **STDIN piping** -- `--input -` reads JSON from STDIN, CLI flags override for duplicate keys
- **TTY-adaptive output** -- rich tables for terminals, JSON for pipes (configurable via `--format`)
- **Approval gate** -- TTY-aware HITL prompts for modules with `requires_approval: true`, with `--yes` bypass and 60s timeout
- **Schema validation** -- inputs validated against JSON Schema before execution, with `$ref`/`allOf`/`anyOf`/`oneOf` resolution
- **Security** -- API key auth (keyring + AES-256-GCM), append-only audit logging, subprocess sandboxing
- **Shell completions** -- `apcore-cli apcli completion bash|zsh|fish` generates completion scripts with dynamic module ID completion
- **Man pages** -- `apcore-cli --help --man` prints a full-program roff man page to stdout, powered by `configure_man_help()` (also exposed for embedded CLIs to opt in)
- **Documentation URL** -- `set_docs_url()` sets a base URL; per-command help shows `Docs: {url}/commands/{name}`, man page SEE ALSO links to the full docs site
- **Audit logging** -- all executions logged to `~/.apcore-cli/audit.jsonl` with SHA-256 input hashing

## How It Works

### Mapping: apcore to CLI

| apcore | CLI |
|--------|-----|
| `metadata["display"]["cli"]["alias"]` or `module_id` | Command name — auto-grouped by first `.` segment (`apcore-cli product get`) |
| `metadata["display"]["cli"]["description"]` or `description` | `--help` text |
| `input_schema.properties` | CLI flags (`--a`, `--b`) |
| `input_schema.required` | Validated post-collection via `jsonschema.validate()` (required fields shown as `[required]` in `--help`) |
| `annotations.requires_approval` | HITL approval prompt |

### Architecture

```
User / AI Agent (terminal)
    |
    v
apcore-cli (the adapter)
    |
    +-- ConfigResolver            4-tier config precedence
    +-- GroupedModuleGroup (default)  Multi-level command grouping (wraps LazyModuleGroup)
    +-- LazyModuleGroup           Lazy per-module Click command construction (base class)
    +-- ExposureFilter            Declarative module exposure filtering (FE-12)
    +-- CliApprovalHandler        Async approval handler protocol implementation (FE-11)
    +-- system_cmd                Runtime system-management (health/usage/enable/disable/reload/config)
    +-- strategy                  Execution strategy dispatch (--strategy flag and describe-pipeline)
    +-- init_cmd                  Module scaffolding (init subcommand)
    +-- set_verbose_help          Toggle built-in option visibility (internal name; controls --all-options behaviour)
    +-- set_docs_url              Set base URL for online docs
    +-- build_program_man_page    Full-program roff man page
    +-- configure_man_help        Add --help --man support to any CLI
    +-- schema_parser             JSON Schema -> Click options
    +-- ref_resolver              $ref / allOf / anyOf / oneOf
    +-- approval                  TTY-aware HITL approval
    +-- output                    TTY-adaptive JSON/table output
    +-- AuditLogger               JSON Lines execution logging
    +-- Sandbox                   Subprocess isolation
    |
    v
apcore Registry + Executor (your modules, unchanged)
```

## Examples

The `examples/extensions/` directory contains 8 runnable modules:

| Module | Description | Usage |
|--------|-------------|-------|
| `math.add` | Add two integers | `apcore-cli math add --a 5 --b 10` |
| `math.multiply` | Multiply two integers | `apcore-cli math multiply --a 6 --b 7` |
| `text.upper` | Uppercase a string | `apcore-cli text upper --text hello` |
| `text.reverse` | Reverse a string | `apcore-cli text reverse --text abcdef` |
| `text.wordcount` | Count words/chars/lines | `apcore-cli text wordcount --text "hello world"` |
| `sysutil.info` | OS, hostname, Python version | `apcore-cli sysutil info` |
| `sysutil.env` | Read environment variables | `apcore-cli sysutil env --name HOME` |
| `sysutil.disk` | Disk usage statistics | `apcore-cli sysutil disk --path /` |

### Running examples

```bash
# Set extensions path (one time)
export APCORE_EXTENSIONS_ROOT=examples/extensions

# Execute modules
apcore-cli math add --a 42 --b 58
apcore-cli text upper --text "hello apcore"
apcore-cli sysutil info
apcore-cli sysutil disk --path /

# Discovery
apcore-cli apcli list --format json
apcore-cli apcli list --tag math --format json
apcore-cli apcli describe math.add --format json

# STDIN piping
echo '{"a": 100, "b": 200}' | apcore-cli math add --input -

# Shell completion
apcore-cli apcli completion bash >> ~/.bashrc
apcore-cli apcli completion zsh >> ~/.zshrc
apcore-cli apcli completion fish > ~/.config/fish/completions/apcore-cli.fish

# Man pages (full-program roff output)
apcore-cli --help --man | man -l -

# Run all examples at once
bash examples/run_examples.sh
```

### Writing your own module

Create a Python file in your extensions directory:

```python
# extensions/greet/hello.py
from pydantic import BaseModel

class Input(BaseModel):
    name: str
    greeting: str = "Hello"

class Output(BaseModel):
    message: str

class GreetHello:
    input_schema = Input
    output_schema = Output
    description = "Greet someone by name"

    def execute(self, inputs, context=None):
        return {"message": f"{inputs['greeting']}, {inputs['name']}!"}
```

Then run it:

```bash
apcore-cli --extensions-dir ./extensions greet hello --name World
# {"message": "Hello, World!"}

apcore-cli --extensions-dir ./extensions greet hello --name Alice --greeting Hi
# {"message": "Hi, Alice!"}
```

## Development

The conformance suite under `tests/conformance/` reads shared fixtures
from the **spec repo** (`aiperceivable/apcore-cli`). Clone it as a sibling
of this repo, or point `APCORE_CLI_SPEC_REPO` at an existing checkout:

```bash
# One-time: clone both repos side by side
git clone https://github.com/aiperceivable/apcore-cli.git
git clone https://github.com/aiperceivable/apcore-cli-python.git

cd apcore-cli-python
pip install -e ".[dev]"
pytest                           # reads fixtures from ../apcore-cli/conformance/
pytest --cov                     # with coverage report
bash examples/run_examples.sh    # run all examples
```

Alternative layout (spec repo checked out elsewhere):

```bash
export APCORE_CLI_SPEC_REPO=/path/to/apcore-cli
pytest
```

Without the spec repo the conformance tests are skipped (pytest reports
them as `skipped`, not `failed`). CI clones the spec repo automatically —
see `.github/workflows/ci.yml`.

## License

Apache-2.0
