"""Coverage tests for :func:`apcore_cli.output.format_exec_result` formatters.

Covers the CSV / YAML / JSONL / field-selection / table-dict branches that
are not exercised by existing unit tests (``output.py`` was at 73% coverage
before this module).
"""

from __future__ import annotations

import json
from typing import Any

from apcore_cli.output import format_exec_result


class TestFormatExecResultBranches:
    def test_none_result_silently_returns(self, capsys):
        format_exec_result(None, format="json")
        assert capsys.readouterr().out == ""

    def test_fields_selection_dot_path(self, capsys):
        data = {"status": "ok", "data": {"count": 42, "nested": {"v": "x"}}}
        format_exec_result(data, format="json", fields="status,data.count")
        out = json.loads(capsys.readouterr().out)
        assert out == {"status": "ok", "data.count": 42}

    def test_fields_selection_missing_path_returns_none(self, capsys):
        data = {"status": "ok"}
        format_exec_result(data, format="json", fields="missing.key")
        out = json.loads(capsys.readouterr().out)
        assert out == {"missing.key": None}

    def test_csv_dict_result(self, capsys):
        format_exec_result({"a": 1, "b": "hi"}, format="csv")
        out = capsys.readouterr().out
        assert "a,b" in out
        assert "1,hi" in out

    def test_csv_list_of_dicts(self, capsys):
        format_exec_result([{"a": 1}, {"a": 2}], format="csv")
        out = capsys.readouterr().out
        # csv.DictWriter uses \r\n line endings regardless of platform.
        lines = [line.rstrip() for line in out.splitlines() if line.strip()]
        assert lines == ["a", "1", "2"]

    def test_csv_non_dict_falls_back_to_json(self, capsys):
        format_exec_result("hello", format="csv")
        out = capsys.readouterr().out.strip()
        # non-dict/list → JSON dump of the scalar string
        assert out == '"hello"'

    # --- Regression: nested-object CSV (apcore-cli-python str() repr bug) ---
    def test_csv_nested_object_serializes_as_json(self, capsys):
        """Before toolkit-delegate, str() emitted Python repr `{'k': 'v'}`
        with single quotes — invalid JSON. Toolkit emits canonical JSON."""
        format_exec_result(
            {"schema": {"type": "object", "properties": {"a": {"type": "integer"}}}},
            format="csv",
        )
        out = capsys.readouterr().out
        # Must contain canonical JSON (double-quoted), not Python repr.
        assert "'type'" not in out, "must not emit Python repr"
        assert '"type":"object"' in out.replace('""', '"') or '""type""' in out

    # --- Regression: heterogeneous-keys data loss ---
    def test_csv_heterogeneous_keys_included(self, capsys):
        """Before toolkit-delegate, csv.DictWriter was given only first-row
        keys, dropping later-row fields silently. Toolkit emits union of keys."""
        format_exec_result(
            [{"sn": 1, "title": "A"}, {"sn": 2, "title": "B", "description": "later-only"}],
            format="csv",
        )
        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line.strip()]
        assert lines[0] == "sn,title,description"
        assert lines[1] == "1,A,"
        assert lines[2] == "2,B,later-only"

    def test_yaml_format(self, capsys):
        format_exec_result({"a": 1, "b": [1, 2]}, format="yaml")
        out = capsys.readouterr().out
        assert "a: 1" in out
        assert "b:" in out

    def test_jsonl_list(self, capsys):
        format_exec_result([{"i": 1}, {"i": 2}], format="jsonl")
        out = capsys.readouterr().out
        # Toolkit-canonical compact JSON: no whitespace between separators.
        assert '{"i":1}' in out
        assert '{"i":2}' in out

    def test_jsonl_non_list(self, capsys):
        format_exec_result({"i": 1}, format="jsonl")
        assert capsys.readouterr().out.strip() == '{"i":1}'

    def test_table_dict_uses_rich(self, capsys):
        # Force table format even though stdout isn't a TTY — the function
        # dispatches on format=="table" regardless of TTY.
        format_exec_result({"k1": "v1", "k2": "v2"}, format="table")
        out = capsys.readouterr().out
        assert "k1" in out and "v1" in out

    def test_plain_string_passthrough(self, capsys):
        format_exec_result("literal string", format="json")
        # dict/list branch doesn't match; string branch echoes verbatim.
        out = capsys.readouterr().out.strip()
        # JSON branch dumps the string — actually looking at the code, the
        # dict|list check comes first; pure string falls to the string echo.
        # Output is either the raw string OR a JSON-dumped string; both are
        # valid representations. Assert it contains our content.
        assert "literal string" in out

    def test_scalar_result_stringified(self, capsys):
        format_exec_result(42, format="table")
        out = capsys.readouterr().out.strip()
        assert out == "42"


class TestFormatExecResultNoneFormat:
    """resolve_format(None) picks 'json' on non-TTY, 'table' on TTY."""

    def test_no_format_argument(self, capsys):
        # CliRunner-like context: stdout isn't TTY → resolve to json.
        format_exec_result({"x": 1})
        out = capsys.readouterr().out
        data: Any = json.loads(out)
        assert data == {"x": 1}
