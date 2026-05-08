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
            "properties": {"enable_debug": {"type": "boolean"}},
            "required": [],
        }
        options = schema_to_click_options(schema)
        opt = options[0]
        assert opt.is_flag is True
        all_opts = opt.opts + getattr(opt, "secondary_opts", [])
        assert "--enable-debug" in all_opts
        assert "--no-enable-debug" in all_opts

    def test_boolean_default_true(self):
        schema = {
            "properties": {"enable_debug": {"type": "boolean", "default": True}},
            "required": [],
        }
        options = schema_to_click_options(schema)
        assert options[0].default is True

    def test_enum_choice(self):
        schema = {
            "properties": {
                "output_format": {"type": "string", "enum": ["json", "csv"]},
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

    def test_help_truncation_default(self):
        long_text = "x" * 1100
        result = _extract_help({"description": long_text})
        assert len(result) == 1000
        assert result.endswith("...")

    def test_help_no_truncation_within_limit(self):
        text = "x" * 999
        result = _extract_help({"description": text})
        assert result == text

    def test_help_truncation_custom_max(self):
        long_text = "x" * 300
        result = _extract_help({"description": long_text}, max_length=200)
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


class TestReservedPropertyNames:
    """D10-008: schema_parser must reject reserved property names."""

    def test_schema_parser_rejects_format_property(self):
        """Property 'format' must exit 48 because it collides with --format flag."""
        # Audit D11-NEW-005 (2026-05-08): cross-SDK parity — reserved-name uses
        # exit 48 (SCHEMA_CIRCULAR_REF), not a generic ValueError.
        schema = {"properties": {"format": {"type": "string", "description": "Output format"}}}
        with pytest.raises(SystemExit) as exc_info:
            schema_to_click_options(schema)
        assert exc_info.value.code == 48

    def test_schema_parser_rejects_input_property(self):
        """Property 'input' must exit 48 (conflicts with --input flag)."""
        schema = {"properties": {"input": {"type": "string"}}}
        with pytest.raises(SystemExit) as exc_info:
            schema_to_click_options(schema)
        assert exc_info.value.code == 48

    def test_reserved_names_constant_exists(self):
        """RESERVED_PROPERTY_NAMES frozenset must exist and contain 'format' and 'input'."""
        from apcore_cli import schema_parser as _schema_parser

        assert hasattr(_schema_parser, "RESERVED_PROPERTY_NAMES"), "RESERVED_PROPERTY_NAMES must be defined"
        assert "format" in _schema_parser.RESERVED_PROPERTY_NAMES
        assert "input" in _schema_parser.RESERVED_PROPERTY_NAMES


class TestNoFlagCollision:
    """D11-005: boolean properties auto-generate ``--no-<flag>`` and that
    name must be tracked in the collision map so a sibling property that
    normalizes to the same name is rejected at parse time. Cross-SDK
    parity with apcore-cli-rust/src/schema_parser.rs:272.
    """

    def test_bool_then_no_form_collision_raises_exit_48(self):
        """force: bool + no_force: str — the boolean processed first,
        then the non-boolean's ``--no-force`` collides with the auto-form."""
        from apcore_cli.schema_parser import schema_to_click_options

        schema = {
            "properties": {
                "force": {"type": "boolean"},
                "no_force": {"type": "string"},
            }
        }
        with pytest.raises(SystemExit) as exc_info:
            schema_to_click_options(schema)
        assert exc_info.value.code == 48

    def test_no_form_then_bool_collision_raises_exit_48(self):
        """no_force: str + force: bool — the non-boolean processed first,
        then the boolean's auto-generated ``--no-force`` collides."""
        from apcore_cli.schema_parser import schema_to_click_options

        schema = {
            "properties": {
                "no_force": {"type": "string"},
                "force": {"type": "boolean"},
            }
        }
        with pytest.raises(SystemExit) as exc_info:
            schema_to_click_options(schema)
        assert exc_info.value.code == 48

    def test_unrelated_no_prefixed_property_still_works(self):
        """A property that happens to start with ``no_`` is fine when there
        is no corresponding boolean — common case must keep working."""
        from apcore_cli.schema_parser import schema_to_click_options

        schema = {
            "properties": {
                "no_color": {"type": "boolean"},
                "color": {"type": "string"},
            }
        }
        # color and no_color share no auto-generated negation overlap (the
        # boolean is no_color → --no-color / --no-no-color; the string
        # color → --color). Should produce 2 options without exiting.
        result = schema_to_click_options(schema)
        assert len(result) == 2

    def test_two_unrelated_booleans_no_collision(self):
        from apcore_cli.schema_parser import schema_to_click_options

        schema = {
            "properties": {
                "force": {"type": "boolean"},
                "verbose_logs": {"type": "boolean"},
            }
        }
        result = schema_to_click_options(schema)
        assert len(result) == 2
