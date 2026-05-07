"""Coverage tests for ``apcli list`` filter flags (FE-04 + FE-13).

Targets the filter branches on :func:`register_list_command` that aren't
exercised by existing smoke tests: ``--tag``, ``--search``, ``--status``,
``--deprecated``, ``--annotation``, ``--sort`` warning path, and tag
validation error.
"""

from __future__ import annotations

import inspect
import json
from unittest.mock import MagicMock

import click
from apcore_cli.discovery import register_list_command
from click.testing import CliRunner

# mix_stderr was removed in Click 8.2; from that version stderr is always separate.
_RUNNER_KWARGS: dict = {"mix_stderr": False} if "mix_stderr" in inspect.signature(CliRunner.__init__).parameters else {}


class _Mod:
    def __init__(
        self,
        module_id: str,
        description: str = "",
        tags: list | None = None,
        enabled: bool = True,
        deprecated: bool = False,
        annotations=None,
    ):
        self.module_id = module_id
        self.canonical_id = module_id
        self.description = description
        self.tags = tags or []
        self.enabled = enabled
        self.deprecated = deprecated
        self.annotations = annotations
        self.input_schema = {"properties": {}, "required": []}
        self.metadata: dict = {}


def _build_cli(modules):
    registry = MagicMock()
    registry.list.return_value = [m.module_id for m in modules]
    defs = {m.module_id: m for m in modules}
    registry.get_definition.side_effect = lambda mid, **kw: defs.get(mid)

    @click.group()
    def cli() -> None:
        pass

    register_list_command(cli, registry)
    return cli


class TestListFilters:
    def test_tag_filter_intersects(self):
        mods = [
            _Mod("a.one", tags=["x"]),
            _Mod("b.two", tags=["x", "y"]),
            _Mod("c.three", tags=["z"]),
        ]
        cli = _build_cli(mods)
        result = CliRunner().invoke(cli, ["list", "--flat", "--format", "json", "--tag", "x"])
        assert result.exit_code == 0, result.output
        ids = [d["id"] for d in json.loads(result.output)]
        assert set(ids) == {"a.one", "b.two"}

    def test_tag_filter_invalid_format_exits_2(self):
        cli = _build_cli([_Mod("a.one")])
        result = CliRunner().invoke(cli, ["list", "--tag", "INVALID!"])
        assert result.exit_code == 2
        assert "Invalid tag format" in result.output

    def test_search_filter_matches_id_or_description(self):
        mods = [_Mod("foo.bar", description="Does X"), _Mod("baz", description="Does Y")]
        cli = _build_cli(mods)
        result = CliRunner().invoke(cli, ["list", "--flat", "--format", "json", "-s", "does x"])
        ids = [d["id"] for d in json.loads(result.output)]
        assert ids == ["foo.bar"]

    def test_status_disabled_filter(self):
        mods = [_Mod("a.one", enabled=True), _Mod("b.two", enabled=False)]
        cli = _build_cli(mods)
        result = CliRunner().invoke(cli, ["list", "--flat", "--format", "json", "--status", "disabled"])
        ids = [d["id"] for d in json.loads(result.output)]
        assert ids == ["b.two"]

    def test_status_all_returns_everyone(self):
        mods = [_Mod("a.one", enabled=True), _Mod("b.two", enabled=False)]
        cli = _build_cli(mods)
        result = CliRunner().invoke(cli, ["list", "--flat", "--format", "json", "--status", "all"])
        ids = sorted(d["id"] for d in json.loads(result.output))
        assert ids == ["a.one", "b.two"]

    def test_deprecated_excluded_by_default(self):
        mods = [_Mod("a.one"), _Mod("b.two", deprecated=True)]
        cli = _build_cli(mods)
        result = CliRunner().invoke(cli, ["list", "--flat", "--format", "json"])
        ids = [d["id"] for d in json.loads(result.output)]
        assert ids == ["a.one"]

    def test_deprecated_included_when_flag(self):
        mods = [_Mod("a.one"), _Mod("b.two", deprecated=True)]
        cli = _build_cli(mods)
        result = CliRunner().invoke(cli, ["list", "--flat", "--format", "json", "--deprecated"])
        ids = sorted(d["id"] for d in json.loads(result.output))
        assert ids == ["a.one", "b.two"]

    def test_annotation_filter_destructive(self):
        ann_yes = MagicMock(destructive=True)
        ann_no = MagicMock(destructive=False)
        mods = [_Mod("a", annotations=ann_yes), _Mod("b", annotations=ann_no)]
        cli = _build_cli(mods)
        result = CliRunner().invoke(cli, ["list", "--flat", "--format", "json", "-a", "destructive"])
        ids = [d["id"] for d in json.loads(result.output)]
        assert ids == ["a"]

    def test_sort_calls_emits_user_visible_note(self, tmp_path, monkeypatch):
        # Issue #17: when audit log has no entries in the period window the
        # discovery layer falls back to id-sort and emits a user-visible note
        # to stderr (not a buried logger.warning, which most users miss).
        from apcore_cli.security.audit import AuditLogger

        # Force the system_usage reader to look at an empty file.
        monkeypatch.setattr(AuditLogger, "DEFAULT_PATH", tmp_path / "audit.jsonl")

        mods = [_Mod("a"), _Mod("b")]
        cli = _build_cli(mods)
        result = CliRunner(**_RUNNER_KWARGS).invoke(cli, ["list", "--flat", "--format", "json", "--sort", "calls"])
        assert result.exit_code == 0
        assert "no usage data available" in (result.stderr or "")

    def test_reverse_sort(self):
        mods = [_Mod("b"), _Mod("a"), _Mod("c")]
        cli = _build_cli(mods)
        result = CliRunner().invoke(cli, ["list", "--flat", "--format", "json", "--reverse"])
        ids = [d["id"] for d in json.loads(result.output)]
        assert ids == ["c", "b", "a"]

    def test_exposure_all_column(self):
        """--exposure all produces the exposed column and shows every module."""
        from apcore_cli.exposure import ExposureFilter

        mods = [_Mod("admin.users"), _Mod("public.search")]
        registry = MagicMock()
        registry.list.return_value = [m.module_id for m in mods]
        defs = {m.module_id: m for m in mods}
        registry.get_definition.side_effect = lambda mid, **kw: defs.get(mid)

        @click.group()
        @click.pass_context
        def cli(ctx) -> None:
            ctx.ensure_object(dict)
            ctx.obj["exposure_filter"] = ExposureFilter(mode="include", include=["admin.*"])

        register_list_command(cli, registry)
        result = CliRunner().invoke(cli, ["list", "--flat", "--format", "json", "--exposure", "all"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        exposed_map = {d["id"]: d.get("exposed") for d in data}
        assert exposed_map["admin.users"] is True
        assert exposed_map["public.search"] is False
