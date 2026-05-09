"""Conformance — Algorithm C-SNAKE: snake_case multi-word kwargs flow.

Cross-language fixture lives at
``../apcore-cli/conformance/fixtures/snake-case-kwargs/cases.json`` and is
shared verbatim with the TypeScript and Rust SDK runners.

Targets the per-module ``click.Command`` produced by
:func:`apcore_cli.cli.build_module_command`. For every case the runner
parses ``args``, lets the command flow into ``Sandbox.execute`` (which calls
``executor.call(module_id, merged)``), and asserts that the captured
``merged`` dict contains every expected snake_case key with the expected
value. Click natively maps ``--has-solution`` to ``has_solution`` so the
Python SDK is the parity reference for the TypeScript fix.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from apcore_cli.cli import build_module_command

_DEFAULT_SPEC_REPO = Path(__file__).resolve().parents[3] / "apcore-cli"
SPEC_REPO_ROOT = Path(
    os.environ.get("APCORE_CLI_SPEC_REPO", str(_DEFAULT_SPEC_REPO))
)
FIXTURE_PATH = (
    SPEC_REPO_ROOT
    / "conformance"
    / "fixtures"
    / "snake-case-kwargs"
    / "cases.json"
)


def _load_fixture() -> dict[str, Any]:
    if not FIXTURE_PATH.is_file():
        pytest.skip(f"fixture not found: {FIXTURE_PATH}")
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


_FIXTURE = _load_fixture()


def _make_module_def(module_id: str, input_schema: dict[str, Any]) -> Any:
    module_def = MagicMock()
    module_def.module_id = module_id
    module_def.description = "Snake-case kwargs conformance fixture"
    module_def.input_schema = input_schema
    module_def.annotations = None
    module_def.tags = []
    return module_def


@pytest.mark.parametrize(
    "case",
    _FIXTURE["test_cases"],
    ids=[c["id"] for c in _FIXTURE["test_cases"]],
)
def test_snake_case_kwargs_flow(case: dict[str, Any]) -> None:
    module_def = _make_module_def(
        _FIXTURE["module_id"], _FIXTURE["input_schema"]
    )
    captured: dict[str, Any] = {}

    def _capture_call(module_id: str, input_data: dict[str, Any]) -> Any:
        captured["module_id"] = module_id
        captured["input"] = input_data
        return {"ok": True}

    executor = MagicMock()
    executor.call.side_effect = _capture_call

    cmd = build_module_command(module_def, executor)

    runner = CliRunner()
    args = ["--format", "json", "-y", *case["args"]]
    result = runner.invoke(cmd, args, catch_exceptions=False)
    assert result.exit_code == 0, (
        f"args={case['args']} exit_code={result.exit_code} "
        f"stdout={result.output!r}"
    )

    actual = captured.get("input", {})
    for key, expected in case["expected_input"].items():
        assert key in actual, (
            f"missing key '{key}' in input; full input={actual!r}"
        )
        assert actual[key] == expected, (
            f"input['{key}'] = {actual[key]!r}, expected {expected!r}; "
            f"full input={actual!r}"
        )
