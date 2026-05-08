"""Tests for `markdown` and `skill` output formats (FE-08, issue #20).

Both formats delegate to apcore_toolkit.format_module(s) — these tests assert
the CLI wrapper produces output byte-identical to the toolkit primitive when
fed an adapted ScannedModule.
"""

from unittest.mock import MagicMock

import pytest

from apcore_cli.output import (
    _descriptor_to_scanned,
    format_module_detail,
    format_module_list,
)

format_module = pytest.importorskip("apcore_toolkit").format_module
format_modules = pytest.importorskip("apcore_toolkit").format_modules


def _mock_descriptor(
    module_id="math.add",
    description="Add two numbers.",
    tags=None,
    input_schema=None,
    output_schema=None,
    annotations=None,
    metadata=None,
):
    m = MagicMock(
        spec=[
            "module_id",
            "canonical_id",
            "description",
            "tags",
            "input_schema",
            "output_schema",
            "annotations",
            "examples",
            "metadata",
            "version",
            "documentation",
        ]
    )
    m.module_id = module_id
    m.canonical_id = module_id
    m.description = description
    m.tags = tags or []
    m.input_schema = input_schema or {}
    m.output_schema = output_schema or {}
    m.annotations = annotations
    m.examples = []
    m.metadata = metadata or {}
    m.version = None
    m.documentation = None
    return m


class TestDescriptorAdapter:
    def test_adapter_maps_canonical_id_to_module_id(self):
        d = _mock_descriptor(module_id="math.add")
        scanned = _descriptor_to_scanned(d)
        assert scanned.module_id == "math.add"

    def test_adapter_propagates_display_overlay_from_metadata(self):
        d = _mock_descriptor(metadata={"display": {"alias": "add"}})
        scanned = _descriptor_to_scanned(d)
        assert scanned.display == {"alias": "add"}

    def test_adapter_handles_missing_optional_fields(self):
        d = _mock_descriptor(input_schema=None, output_schema=None)
        scanned = _descriptor_to_scanned(d)
        assert scanned.input_schema == {}
        assert scanned.output_schema == {}


class TestListMarkdownSkill:
    def test_list_markdown_matches_toolkit(self, capsys):
        d = _mock_descriptor(
            input_schema={
                "type": "object",
                "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                "required": ["a", "b"],
            },
        )
        format_module_list([d], format="markdown")
        captured = capsys.readouterr().out
        expected = format_modules([_descriptor_to_scanned(d)], style="markdown") + "\n"
        assert captured == expected

    def test_list_skill_matches_toolkit(self, capsys):
        d = _mock_descriptor()
        format_module_list([d], format="skill")
        captured = capsys.readouterr().out
        expected = format_modules([_descriptor_to_scanned(d)], style="skill") + "\n"
        assert captured == expected

    def test_list_markdown_empty_modules(self, capsys):
        format_module_list([], format="markdown")
        captured = capsys.readouterr().out
        assert captured == "\n"  # empty toolkit output + click.echo trailing newline


class TestDescribeMarkdownSkill:
    def test_describe_markdown_matches_toolkit(self, capsys):
        d = _mock_descriptor(description="Add two integers.")
        format_module_detail(d, format="markdown")
        captured = capsys.readouterr().out
        expected = format_module(_descriptor_to_scanned(d), style="markdown") + "\n"
        assert captured == expected

    def test_describe_skill_has_yaml_frontmatter(self, capsys):
        d = _mock_descriptor()
        format_module_detail(d, format="skill")
        captured = capsys.readouterr().out
        assert captured.startswith("---\n")
        assert "name: math.add" in captured.splitlines()[1]
        assert captured.split("\n", 4)[3] == "---"

    def test_describe_skill_matches_toolkit(self, capsys):
        d = _mock_descriptor(tags=["math"])
        format_module_detail(d, format="skill")
        captured = capsys.readouterr().out
        expected = format_module(_descriptor_to_scanned(d), style="skill") + "\n"
        assert captured == expected
