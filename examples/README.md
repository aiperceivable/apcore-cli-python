# Examples

8 example modules demonstrating apcore-cli usage.

## Quick Start

```bash
# From the project root
pip install -e .
export APCORE_EXTENSIONS_ROOT=examples/extensions

# Run a module
apcore-cli math add --a 5 --b 10
# {"sum": 15}

# List all modules
apcore-cli apcli list

# Run all examples at once
bash examples/run_examples.sh
```

## Available Modules

| Module | Description | Example |
|--------|-------------|---------|
| `math.add` | Add two integers | `apcore-cli math add --a 5 --b 10` |
| `math.multiply` | Multiply two integers | `apcore-cli math multiply --a 6 --b 7` |
| `text.upper` | Uppercase a string | `apcore-cli text upper --text hello` |
| `text.reverse` | Reverse a string | `apcore-cli text reverse --text abcdef` |
| `text.wordcount` | Count words/chars/lines | `apcore-cli text wordcount --text "hello world"` |
| `sysutil.info` | System information | `apcore-cli sysutil info` |
| `sysutil.env` | Read an env variable | `apcore-cli sysutil env --name HOME` |
| `sysutil.disk` | Disk usage stats | `apcore-cli sysutil disk --path /` |

## Writing Your Own Module

Modules are plain Python files with `Input`/`Output` pydantic models and an
`execute()` method. apcore-cli auto-discovers them — no decorator required.

```
extensions/
└── greet/
    └── hello.py
```

### Step 1: Create the module file

```python
# extensions/greet/hello.py
from pydantic import BaseModel


class Input(BaseModel):
    name: str
    greeting: str = "Hello"


class Output(BaseModel):
    message: str


class GreetHello:
    """Greet someone by name."""

    input_schema = Input
    output_schema = Output
    description = "Greet someone by name"

    def execute(self, inputs, context=None):
        return {"message": f"{inputs['greeting']}, {inputs['name']}!"}
```

The module ID is derived from the file path: `extensions/greet/hello.py`
becomes `greet.hello`. With the default `group_depth=1` (v0.6.0+), it is
invoked as `greet hello` (split on the first dot), not as a single
dotted token.

### Step 2: Run it

```bash
apcore-cli --extensions-dir ./extensions greet hello --name World
# {"message": "Hello, World!"}

apcore-cli --extensions-dir ./extensions greet hello --name Alice --greeting Hi
# {"message": "Hi, Alice!"}

# Auto-generated help from input_schema
apcore-cli --extensions-dir ./extensions greet hello --help
```

### How It Works

```
apcore-cli greet hello --name World
    │
    ├── 1. apcore Registry discovers extensions/greet/hello.py
    ├── 2. Click options are auto-generated from Input's JSON Schema
    ├── 3. --name World is parsed and validated against Input
    └── 4. apcore Executor calls GreetHello.execute(inputs={"name": "World", ...})
              │
              └── {"message": "Hello, World!"}
```

apcore-cli is a pure adapter on top of apcore's `Registry` + `Executor` — see
the project README for the full architecture.

## STDIN Piping

```bash
# Pipe JSON input directly
echo '{"a": 100, "b": 200}' | apcore-cli math add --input -
# {"sum": 300}

# CLI flags override STDIN values
echo '{"a": 1, "b": 2}' | apcore-cli math add --input - --a 999
# {"sum": 1001}

# Chain with other tools
apcore-cli sysutil info | jq '.hostname'
```
