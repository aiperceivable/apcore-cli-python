"""Cross-SDK parity: host hooks for embedded module commands.

Mirrors the TypeScript ``buildModuleCommand`` options (onError / exitCodes /
exit / errorStream / extraInput) and the docs-URL behaviour.
"""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import MagicMock

import click
import pytest
from click.testing import CliRunner

from apcore_cli.cli import build_module_command, set_docs_url
from apcore_cli.discovery import register_exec_command
from apcore_cli.exit import exit_with, reset_exit_handler, set_exit_handler


def _make_module_def(module_id: str = "series.asset.add", input_schema=None):
    m = MagicMock()
    m.module_id = module_id
    m.canonical_id = module_id
    m.description = "desc"
    m.tags = []
    m.annotations = None
    m.input_schema = input_schema or {"type": "object", "properties": {}, "required": []}
    m.output_schema = None
    m.deprecated = False
    m.enabled = True
    m.metadata = {}
    m.requires_approval = False
    return m


def _wrap(cmd: click.Command) -> click.Group:
    @click.group()
    def root() -> None:
        pass

    root.add_command(cmd, name=cmd.name)
    return root


class TestDocsUrl:
    def teardown_method(self) -> None:
        set_docs_url(None)

    def test_normalises_trailing_slash_and_keeps_full_module_path(self):
        set_docs_url("https://docs.example.com/cli///")
        cmd = build_module_command(
            _make_module_def("series.asset.add"),
            MagicMock(),
            cmd_name="add",
        )
        result = CliRunner().invoke(cmd, ["--help"])
        assert "https://docs.example.com/cli/commands/series.asset.add" in result.output
        assert "//commands" not in result.output
        assert "/commands/add" not in result.output


class TestHostHooks:
    def test_on_error_exit_codes_and_exit_replace_the_failure_path(self):
        seen: list[object] = []

        def _exit(code: int) -> None:
            raise SystemExit(code)

        cmd = build_module_command(
            _make_module_def(
                "fetch.data",
                {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            ),
            MagicMock(),
            on_error=seen.append,
            exit_codes={"SCHEMA_VALIDATION_ERROR": 2},
            exit=_exit,
        )
        result = CliRunner().invoke(_wrap(cmd), ["fetch.data"])
        assert result.exit_code == 2
        assert seen, "on_error was not called"
        context = seen[0]
        assert context.code == "SCHEMA_VALIDATION_ERROR"
        assert context.exit_code == 2
        assert context.module_id == "fetch.data"

    def test_extra_input_is_merged_before_validation_and_execution(self):
        executor = MagicMock()
        executor.call.return_value = {"ok": True}
        cmd = build_module_command(
            _make_module_def(
                "profile.set",
                {
                    "type": "object",
                    "properties": {"url": {"type": "string"}, "json": {"type": "boolean"}},
                },
            ),
            executor,
            extra_input=lambda: {"json": True},
        )
        result = CliRunner().invoke(_wrap(cmd), ["profile.set", "--url", "https://example.com"])
        assert result.exit_code == 0, result.output
        _module_id, inputs = executor.call.call_args[0][:2]
        assert inputs == {"url": "https://example.com", "json": True}

    def test_error_stream_stdout(self):
        cmd = build_module_command(
            _make_module_def(
                "fetch.data",
                {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            ),
            MagicMock(),
            error_stream="stdout",
            exit=lambda code: (_ for _ in ()).throw(SystemExit(code)),
        )
        result = CliRunner().invoke(_wrap(cmd), ["fetch.data"])
        assert result.exit_code == 45
        assert '"error": true' in result.stdout
        assert '"code": "SCHEMA_VALIDATION_ERROR"' in result.stdout

    def test_custom_exit_exception_unwinds_after_one_validation_failure(self):
        class HostExitError(Exception):
            pass

        seen = []
        exits = []
        executor = MagicMock()
        stop = HostExitError()

        def stop_dispatch(code: int) -> None:
            exits.append(code)
            raise stop

        cmd = build_module_command(
            _make_module_def(
                "fetch.data",
                {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            ),
            executor,
            on_error=seen.append,
            exit_codes={"SCHEMA_VALIDATION_ERROR": 7},
            exit=stop_dispatch,
            error_stream="stdout",
        )

        result = CliRunner().invoke(cmd, ["--format", "json"])

        assert result.exception is stop
        assert [(failure.code, failure.exit_code) for failure in seen] == [("SCHEMA_VALIDATION_ERROR", 7)]
        assert exits == [7]
        assert json.loads(result.stdout)["exit_code"] == 7
        assert result.stderr == ""
        executor.call.assert_not_called()

    def test_missing_input_file_uses_host_hooks(self, tmp_path):
        seen = []
        executor = MagicMock()
        cmd = build_module_command(
            _make_module_def("fetch.data"),
            executor,
            on_error=seen.append,
            exit_codes={"GENERAL_INVALID_INPUT": 7},
            error_stream="stdout",
        )

        result = CliRunner().invoke(cmd, ["--format", "json", "--input", str(tmp_path / "missing.json")])

        assert result.exit_code == 7
        assert result.stderr == ""
        assert [(failure.code, failure.exit_code) for failure in seen] == [("GENERAL_INVALID_INPUT", 7)]
        assert json.loads(result.stdout)["exit_code"] == 7
        executor.call.assert_not_called()

    @pytest.mark.parametrize(
        ("module_id", "flags", "wire_code", "fallback"),
        [
            ("missing.module", [], "MODULE_NOT_FOUND", 44),
            ("INVALID", [], "GENERAL_INVALID_INPUT", 2),
            ("fetch.data", ["--input", "{"], "GENERAL_INVALID_INPUT", 2),
            ("fetch.data", ["--input", "[]"], "GENERAL_INVALID_INPUT", 2),
            ("fetch.data", ["--input", "-"], "GENERAL_INVALID_INPUT", 2),
            ("fetch.data", ["--sandbox", "--trace"], "GENERAL_INVALID_INPUT", 2),
        ],
    )
    @pytest.mark.parametrize("translate", [False, True])
    def test_exec_reports_early_failures_once(self, module_id, flags, wire_code, fallback, translate):
        exit_code = 7 if translate else fallback
        events = []
        registry = MagicMock()
        module = _make_module_def("fetch.data")
        registry.get_definition.side_effect = lambda name: module if name == module.module_id else None
        executor = MagicMock()

        def on_error(failure) -> None:
            events.append(("hook", failure.module_id, failure.code, failure.exit_code))

        class Output(StringIO):
            def write(self, text: str) -> int:
                events.append(("write",))
                return super().write(text)

        def stop_dispatch(code: int) -> None:
            events.append(("exit", code))
            raise SystemExit(code)

        output = Output()
        group = click.Group("apcli")
        register_exec_command(
            group,
            registry,
            executor,
            on_error=on_error,
            exit_codes={} if exit_code == fallback else {wire_code: exit_code},
            exit=stop_dispatch,
            error_stream=output,
        )
        result = CliRunner().invoke(group, ["exec", module_id, "--format", "json", *flags], input="{")

        assert result.exit_code == exit_code
        assert result.output == ""
        assert events == [("hook", module_id, wire_code, exit_code), ("write",), ("exit", exit_code)]
        payload = json.loads(output.getvalue())
        assert (payload["error"], payload["code"], payload["exit_code"]) == (True, wire_code, exit_code)
        executor.call.assert_not_called()


class TestExitHandler:
    def teardown_method(self) -> None:
        reset_exit_handler()

    def test_host_handler_replaces_system_exit(self):
        class HostExitError(Exception):
            def __init__(self, code: int) -> None:
                self.code = code

        def handler(code: int) -> None:
            raise HostExitError(code)

        set_exit_handler(handler)
        with pytest.raises(HostExitError) as exc:
            exit_with(77)
        assert exc.value.code == 77

    def test_returning_handler_still_stops_the_caller(self):
        set_exit_handler(lambda _code: None)
        with pytest.raises(SystemExit):
            exit_with(3)
