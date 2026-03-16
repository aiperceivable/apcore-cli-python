"""Tests for Schema Parser (FE-02)."""

import logging

import click
import pytest

from apcore_cli.schema_parser import (
    _BOOLEAN_FLAG,
    _extract_help,
    _map_type,
    reconvert_enum_values,
    schema_to_click_options,
)


class TestMapType:
    """Task 1: Basic type mapping."""

    def test_map_type_string(self):
        assert _map_type("name", {"type": "string"}) is click.STRING

    def test_map_type_integer(self):
        assert _map_type("count", {"type": "integer"}) is click.INT

    def test_map_type_number(self):
        assert _map_type("rate", {"type": "number"}) is click.FLOAT

    def test_map_type_boolean(self):
        assert _map_type("verbose", {"type": "boolean"}) is _BOOLEAN_FLAG

    def test_map_type_object(self):
        assert _map_type("data", {"type": "object"}) is click.STRING

    def test_map_type_array(self):
        assert _map_type("items", {"type": "array"}) is click.STRING

    def test_map_type_unknown(self, caplog):
        with caplog.at_level(logging.WARNING, logger="apcore_cli.schema_parser"):
            result = _map_type("field", {"type": "foobar"})
        assert result is click.STRING
        assert "Unknown schema type 'foobar'" in caplog.text

    def test_map_type_missing(self, caplog):
        with caplog.at_level(logging.WARNING, logger="apcore_cli.schema_parser"):
            result = _map_type("field", {})
        assert result is click.STRING
        assert "No type specified" in caplog.text

    def test_map_type_file_convention(self):
        result = _map_type("input_file", {"type": "string"})
        assert isinstance(result, click.Path)

    def test_map_type_x_cli_file(self):
        result = _map_type("source", {"type": "string", "x-cli-file": True})
        assert isinstance(result, click.Path)


class TestSchemaToClickOptions:
    """Task 2: Basic property mapping."""

    def test_schema_to_options_simple(self):
        schema = {
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": [],
        }
        options = schema_to_click_options(schema)
        assert len(options) == 2
        names = [opt.name for opt in options]
        assert "name" in names
        assert "count" in names

    def test_schema_to_options_underscore_to_hyphen(self):
        schema = {
            "properties": {"input_file": {"type": "string"}},
            "required": [],
        }
        options = schema_to_click_options(schema)
        assert len(options) == 1
        # The flag should be --input-file
        opt = options[0]
        flag_names = [d for d in opt.opts]
        assert any("--input-file" in f for f in flag_names)

    def test_schema_to_options_required(self):
        schema = {
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        options = schema_to_click_options(schema)
        # required=False at the Click level — required validation is done by jsonschema
        # in the callback after merging STDIN + CLI flags (so --input - works correctly).
        assert options[0].required is False
        # Help text must still signal the field is required for user clarity.
        assert "[required]" in (options[0].help or "")

    def test_schema_to_options_default(self):
        schema = {
            "properties": {"count": {"type": "integer", "default": 42}},
            "required": [],
        }
        options = schema_to_click_options(schema)
        assert options[0].default == 42

    def test_schema_to_options_empty_schema(self):
        schema = {"properties": {}, "required": []}
        options = schema_to_click_options(schema)
        assert options == []


class TestBooleanAndEnum:
    """Task 3: Boolean flag pairs and enum choices."""

    def test_boolean_flag_pair(self):
        schema = {
            "properties": {"verbose": {"type": "boolean"}},
            "required": [],
        }
        options = schema_to_click_options(schema)
        opt = options[0]
        assert opt.is_flag is True
        all_opts = opt.opts + getattr(opt, "secondary_opts", [])
        assert "--verbose" in all_opts
        assert "--no-verbose" in all_opts

    def test_boolean_default_true(self):
        schema = {
            "properties": {"verbose": {"type": "boolean", "default": True}},
            "required": [],
        }
        options = schema_to_click_options(schema)
        assert options[0].default is True

    def test_enum_choice(self):
        schema = {
            "properties": {
                "format": {"type": "string", "enum": ["json", "csv"]},
            },
            "required": [],
        }
        options = schema_to_click_options(schema)
        opt = options[0]
        assert isinstance(opt.type, click.Choice)
        assert "json" in opt.type.choices
        assert "csv" in opt.type.choices

    def test_enum_integer_reconversion(self):
        schema = {
            "properties": {
                "level": {"type": "integer", "enum": [1, 2, 3]},
            },
            "required": [],
        }
        options = schema_to_click_options(schema)
        opt = options[0]
        assert isinstance(opt.type, click.Choice)
        assert "1" in opt.type.choices
        # Test reconversion
        result = reconvert_enum_values({"level": "1"}, options)
        assert result["level"] == 1
        assert isinstance(result["level"], int)

    def test_enum_empty_warning(self, caplog):
        schema = {
            "properties": {
                "mode": {"type": "string", "enum": []},
            },
            "required": [],
        }
        with caplog.at_level(logging.WARNING, logger="apcore_cli.schema_parser"):
            options = schema_to_click_options(schema)
        assert "Empty enum" in caplog.text
        # Should fall through to standard string option
        assert not isinstance(options[0].type, click.Choice)

    def test_boolean_with_enum_true(self):
        schema = {
            "properties": {
                "flag": {"type": "boolean", "enum": [True]},
            },
            "required": [],
        }
        options = schema_to_click_options(schema)
        # Should be treated as boolean flag, not enum
        opt = options[0]
        assert opt.is_flag is True


class TestHelpAndCollisions:
    """Task 4: Help text extraction and flag collision detection."""

    def test_help_from_x_llm_description(self):
        result = _extract_help(
            {
                "x-llm-description": "LLM help text",
                "description": "Regular help",
            }
        )
        assert result == "LLM help text"

    def test_help_from_description(self):
        result = _extract_help({"description": "Regular help"})
        assert result == "Regular help"

    def test_help_truncation(self):
        long_text = "x" * 250
        result = _extract_help({"description": long_text})
        assert len(result) == 200
        assert result.endswith("...")

    def test_help_none(self):
        result = _extract_help({})
        assert result is None

    def test_flag_collision_detection(self):
        schema = {
            "properties": {
                "foo_bar": {"type": "string"},
                "foo-bar": {"type": "string"},
            },
            "required": [],
        }
        with pytest.raises(SystemExit) as exc_info:
            schema_to_click_options(schema)
        assert exc_info.value.code == 48
