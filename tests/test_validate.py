"""Tests for apcore_cli.validate (FE-11 — D9-005).

Covers the pure helpers extracted from cli.py during the cross-SDK leanness
pass to mirror apcore-cli-rust/src/validate.rs:

* ``first_failed_exit_code`` — canonical check-name → exit-code mapping.
* ``format_preflight_result`` — JSON / TTY-table renderer for PreflightResult.

The dispatch path that actually invokes Registry/Executor and calls
``sys.exit`` is exercised via the e2e/integration tests; here we focus on
the pure logic so each test is self-contained and independent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from io import StringIO
from typing import Any
from unittest.mock import patch

import pytest

from apcore_cli.validate import first_failed_exit_code, format_preflight_result


@dataclass
class _Check:
    """Mimics the PreflightCheck dataclass surface used by validate.py."""

    check: str
    passed: bool | None
    error: Any = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Result:
    """Mimics the PreflightResult dataclass surface."""

    valid: bool
    requires_approval: bool
    checks: list[_Check]


class TestFirstFailedExitCode:
    """The exit-code mapper must return the canonical code for the FIRST failed check."""

    @pytest.mark.parametrize(
        ("check_name", "expected"),
        [
            ("module_id", 2),
            ("module_lookup", 44),
            ("call_chain", 1),
            ("acl", 77),
            ("schema", 45),
            ("approval", 46),
            ("module_preflight", 1),
        ],
    )
    def test_each_known_check_maps_to_its_exit_code(self, check_name: str, expected: int) -> None:
        result = _Result(valid=False, requires_approval=False, checks=[_Check(check_name, passed=False)])
        assert first_failed_exit_code(result) == expected

    def test_returns_first_failure_not_subsequent(self) -> None:
        """When multiple checks fail, the FIRST failed check determines the exit code."""
        result = _Result(
            valid=False,
            requires_approval=False,
            checks=[
                _Check("module_id", passed=True),
                _Check("schema", passed=False),  # first failure → 45
                _Check("acl", passed=False),  # would be 77 if first
            ],
        )
        assert first_failed_exit_code(result) == 45

    def test_unknown_check_name_falls_back_to_one(self) -> None:
        """An unrecognized check name must default to generic exit code 1."""
        result = _Result(valid=False, requires_approval=False, checks=[_Check("brand_new_check", passed=False)])
        assert first_failed_exit_code(result) == 1

    def test_all_passed_returns_one(self) -> None:
        """If no check failed (all True), the helper still returns 1 — caller
        is expected to gate on result.valid before invoking this. This pins
        the documented fallback semantics."""
        result = _Result(
            valid=True,
            requires_approval=False,
            checks=[_Check("module_id", passed=True), _Check("schema", passed=True)],
        )
        assert first_failed_exit_code(result) == 1

    def test_empty_checks_list_returns_one(self) -> None:
        result = _Result(valid=True, requires_approval=False, checks=[])
        assert first_failed_exit_code(result) == 1

    def test_handles_object_without_checks_attribute(self) -> None:
        """getattr-with-default protects against unexpected shapes."""

        class _Bare:
            valid = True
            requires_approval = False

        assert first_failed_exit_code(_Bare()) == 1


class TestFormatPreflightResultJson:
    """JSON output path — emitted when format='json' OR stdout is non-TTY."""

    def _capture_json(self, result: _Result, fmt: str | None = "json") -> dict[str, Any]:
        buf = StringIO()
        with patch("sys.stdout", buf), patch("apcore_cli.output.resolve_format", return_value=fmt or "json"):
            format_preflight_result(result, fmt)
        # click.echo writes to sys.stdout patched above
        return json.loads(buf.getvalue())

    def test_json_payload_includes_top_level_fields(self) -> None:
        result = _Result(
            valid=True,
            requires_approval=False,
            checks=[_Check("module_id", passed=True)],
        )
        payload = self._capture_json(result)
        assert payload["valid"] is True
        assert payload["requires_approval"] is False
        assert isinstance(payload["checks"], list)

    def test_json_omits_error_when_none_and_includes_when_set(self) -> None:
        result = _Result(
            valid=False,
            requires_approval=False,
            checks=[
                _Check("module_id", passed=True),  # no error → key absent
                _Check("schema", passed=False, error="missing required field 'name'"),
            ],
        )
        payload = self._capture_json(result)
        assert "error" not in payload["checks"][0]
        assert payload["checks"][1]["error"] == "missing required field 'name'"

    def test_json_includes_warnings_when_non_empty(self) -> None:
        result = _Result(
            valid=True,
            requires_approval=True,
            checks=[
                _Check("schema", passed=True, warnings=["deprecated field 'foo' will be removed in v2"]),
                _Check("acl", passed=True),  # empty warnings → key absent
            ],
        )
        payload = self._capture_json(result)
        assert payload["checks"][0]["warnings"] == ["deprecated field 'foo' will be removed in v2"]
        assert "warnings" not in payload["checks"][1]

    def test_json_emitted_when_stdout_not_tty_even_without_explicit_format(self) -> None:
        """Pipelines (non-TTY) should always receive JSON regardless of fmt."""
        result = _Result(valid=True, requires_approval=False, checks=[_Check("module_id", passed=True)])
        buf = StringIO()  # StringIO is not a TTY — isatty() returns False
        with patch("sys.stdout", buf), patch("apcore_cli.output.resolve_format", return_value="table"):
            format_preflight_result(result, fmt=None)
        # Output must still be valid JSON since stdout.isatty() is False.
        parsed = json.loads(buf.getvalue())
        assert parsed["valid"] is True


class TestFormatPreflightResultTty:
    """TTY-table format — only when format!='json' AND stdout.isatty()."""

    def _capture_tty(self, result: _Result, fmt: str | None = "table") -> str:
        buf = StringIO()
        # Force a TTY-like stdout so the TTY branch is taken.
        with (
            patch("sys.stdout") as mock_stdout,
            patch("apcore_cli.output.resolve_format", return_value=fmt or "table"),
            patch("click.echo", side_effect=lambda *args, **kwargs: buf.write((args[0] if args else "") + "\n")),
        ):
            mock_stdout.isatty.return_value = True
            format_preflight_result(result, fmt)
        return buf.getvalue()

    def test_tty_output_renders_pass_summary(self) -> None:
        result = _Result(
            valid=True,
            requires_approval=False,
            checks=[_Check("module_id", passed=True), _Check("schema", passed=True)],
        )
        out = self._capture_tty(result)
        assert "PASS" in out
        assert "0 error(s)" in out
        assert "0 warning(s)" in out

    def test_tty_output_renders_fail_summary_with_error_count(self) -> None:
        result = _Result(
            valid=False,
            requires_approval=False,
            checks=[
                _Check("module_id", passed=True),
                _Check("schema", passed=False, error="missing 'name'"),
            ],
        )
        out = self._capture_tty(result)
        assert "FAIL" in out
        assert "1 error(s)" in out
        # The error detail string must appear in the rendered line.
        assert "missing 'name'" in out

    def test_tty_output_includes_warning_lines(self) -> None:
        result = _Result(
            valid=True,
            requires_approval=False,
            checks=[_Check("schema", passed=True, warnings=["deprecated"])],
        )
        out = self._capture_tty(result)
        assert "Warning: deprecated" in out
        assert "1 warning(s)" in out

    def test_tty_handles_skipped_check_with_passed_none(self) -> None:
        """A check with passed=None represents a skipped step (○ symbol path)."""
        result = _Result(
            valid=True,
            requires_approval=False,
            checks=[_Check("approval", passed=None)],
        )
        out = self._capture_tty(result)
        # passed=None is treated as "not passed" by the summary counter (errors+=1)
        # AND falls through to the ○/Skipped branch in the per-check renderer.
        assert "approval" in out
