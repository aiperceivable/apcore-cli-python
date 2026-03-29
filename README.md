<div align="center">
  <img src="https://raw.githubusercontent.com/aiperceivable/apcore-cli/main/apcore-cli-logo.svg" alt="apcore-cli logo" width="200"/>
</div>

# apcore-cli

Terminal adapter for apcore. Execute AI-Perceivable modules from the command line.

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://python.org)
[![Tests](https://img.shields.io/badge/tests-319%2B%20passed-brightgreen.svg)]()

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

Requires Python 3.11+ and `apcore >= 0.14.0`.

## Quick Start

### Try it now

The repo includes 8 example modules you can run immediately:

```bash
git clone https://github.com/aiperceivable/apcore-cli-python.git
cd apcore-cli-python
pip install -e ".[dev]"

# Run a module
apcore-cli --extensions-dir examples/extensions math.add --a 5 --b 10
# {"sum": 15}

# List all modules
apcore-cli --extensions-dir examples/extensions list --format json

# Run all examples
bash examples/run_examples.sh
```

See [Examples](#examples) for the full list of example modules and usage patterns.

### Zero-code approach

If you already have an apcore-based project with an extensions directory:

```bash
# Execute a module
apcore-cli --extensions-dir ./extensions math.add --a 42 --b 58

# Or set the env var once
export APCORE_EXTENSIONS_ROOT=./extensions
apcore-cli math.add --a 42 --b 58
```

All modules are auto-discovered. CLI flags are auto-generated from each module's JSON Schema.

### Programmatic approach (Python API)

```python
from apcore import Registry, Executor
from apcore_cli.__main__ import create_cli

# Build the CLI from your registry
cli = create_cli(extensions_dir="./extensions")
cli(standalone_mode=True)
```

Or use the `LazyModuleGroup` directly with Click:

```python
import click
from apcore import Registry, Executor
from apcore_cli.cli import LazyModuleGroup

registry = Registry(extensions_dir="./extensions")
registry.discover()
executor = Executor(registry)

@click.group(cls=LazyModuleGroup, registry=registry, executor=executor)
def cli():
    pass

cli()
```

## Adding Custom Commands

### Fastest way (30 seconds)

```bash
apcore-cli init module ops.deploy -d "Deploy to environment"
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
apcore-cli --extensions-dir ./extensions list
apcore-cli --extensions-dir ./extensions math.add --a 5 --b 10
```

### STDIN piping (Unix pipes)

```bash
# Pipe JSON input
echo '{"a": 100, "b": 200}' | apcore-cli math.add --input -
# {"sum": 300}

# CLI flags override STDIN values
echo '{"a": 1, "b": 2}' | apcore-cli math.add --input - --a 999
# {"sum": 1001}

# Chain with other tools
apcore-cli sysutil.info | jq '.os, .hostname'
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
| `--verbose` | | Show hidden built-in options in `--help` output |
| `--man` | | Print man page to stdout (use with `--help`) |

### Built-in Commands

| Command | Description |
|---------|-------------|
| `list` | List available modules with optional tag filtering |
| `describe <module_id>` | Show full module metadata and schemas |
| `completion <shell>` | Generate shell completion script (bash/zsh/fish) |
| `man <command>` | Generate man page in roff format |

### Module Execution Options

When executing a module (e.g. `apcore-cli math.add`), these built-in options are available (hidden by default; pass `--help --verbose` to display them):

| Option | Description |
|--------|-------------|
| `--input -` | Read JSON input from STDIN |
| `--yes` / `-y` | Bypass approval prompts |
| `--large-input` | Allow STDIN input larger than 10MB |
| `--format` | Output format: `json` or `table` |
| `--sandbox` | Run module in subprocess sandbox *(not yet implemented)* |

Schema-generated flags (e.g. `--a`, `--b`) are added automatically from the module's `input_schema`.

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
```

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
- **Shell completions** -- `apcore-cli completion bash|zsh|fish` generates completion scripts with dynamic module ID completion
- **Man pages** -- `apcore-cli man <command>` generates per-command man pages; `--help --man` prints a full-program man page via `configure_man_help()`
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
    +-- ConfigResolver       4-tier config precedence
    +-- LazyModuleGroup      Dynamic Click command generation
    +-- set_verbose_help     Toggle built-in option visibility
    +-- set_docs_url         Set base URL for online docs
    +-- build_program_man_page  Full-program roff man page
    +-- configure_man_help   Add --help --man support to any CLI
    +-- schema_parser        JSON Schema -> Click options
    +-- ref_resolver         $ref / allOf / anyOf / oneOf
    +-- approval             TTY-aware HITL approval
    +-- output               TTY-adaptive JSON/table output
    +-- AuditLogger          JSON Lines execution logging
    +-- Sandbox              Subprocess isolation
    |
    v
apcore Registry + Executor (your modules, unchanged)
```

## Examples

The `examples/extensions/` directory contains 8 runnable modules:

| Module | Description | Usage |
|--------|-------------|-------|
| `math.add` | Add two integers | `apcore-cli math.add --a 5 --b 10` |
| `math.multiply` | Multiply two integers | `apcore-cli math.multiply --a 6 --b 7` |
| `text.upper` | Uppercase a string | `apcore-cli text.upper --text hello` |
| `text.reverse` | Reverse a string | `apcore-cli text.reverse --text abcdef` |
| `text.wordcount` | Count words/chars/lines | `apcore-cli text.wordcount --text "hello world"` |
| `sysutil.info` | OS, hostname, Python version | `apcore-cli sysutil.info` |
| `sysutil.env` | Read environment variables | `apcore-cli sysutil.env --name HOME` |
| `sysutil.disk` | Disk usage statistics | `apcore-cli sysutil.disk --path /` |

### Running examples

```bash
# Set extensions path (one time)
export APCORE_EXTENSIONS_ROOT=examples/extensions

# Execute modules
apcore-cli math.add --a 42 --b 58
apcore-cli text.upper --text "hello apcore"
apcore-cli sysutil.info
apcore-cli sysutil.disk --path /

# Discovery
apcore-cli list --format json
apcore-cli list --tag math --format json
apcore-cli describe math.add --format json

# STDIN piping
echo '{"a": 100, "b": 200}' | apcore-cli math.add --input -

# Shell completion
apcore-cli completion bash >> ~/.bashrc
apcore-cli completion zsh >> ~/.zshrc
apcore-cli completion fish > ~/.config/fish/completions/apcore-cli.fish

# Man pages
apcore-cli man list | man -l -

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
apcore-cli --extensions-dir ./extensions greet.hello --name World
# {"message": "Hello, World!"}

apcore-cli --extensions-dir ./extensions greet.hello --name Alice --greeting Hi
# {"message": "Hi, Alice!"}
```

## Development

```bash
git clone https://github.com/aiperceivable/apcore-cli-python.git
cd apcore-cli-python
pip install -e ".[dev]"
pytest                           # 319+ tests
pytest --cov                     # with coverage report
bash examples/run_examples.sh   # run all examples
```

## License

Apache-2.0
