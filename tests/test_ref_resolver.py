"""Tests for $ref resolver (FE-02)."""

import pytest

from apcore_cli.ref_resolver import resolve_refs


class TestResolveRefs:
    """Task 5: $ref resolution."""

    def test_resolve_simple_ref(self):
        schema = {
            "properties": {
                "address": {"$ref": "#/$defs/Address"},
            },
            "$defs": {
                "Address": {
                    "type": "object",
                    "properties": {
                        "street": {"type": "string"},
                        "city": {"type": "string"},
                    },
                },
            },
        }
        result = resolve_refs(schema, module_id="test")
        # Address should be inlined
        addr = result["properties"]["address"]
        assert "properties" in addr
        assert "street" in addr["properties"]

    def test_resolve_nested_ref(self):
        schema = {
            "properties": {
                "user": {"$ref": "#/$defs/User"},
            },
            "$defs": {
                "User": {
                    "properties": {
                        "address": {"$ref": "#/$defs/Address"},
                    },
                },
                "Address": {
                    "properties": {
                        "city": {"type": "string"},
                    },
                },
            },
        }
        result = resolve_refs(schema, module_id="test")
        user = result["properties"]["user"]
        addr = user["properties"]["address"]
        assert "city" in addr["properties"]

    def test_resolve_circular_ref(self):
        from apcore_cli.ref_resolver import CircularRefError

        schema = {
            "properties": {
                "node": {"$ref": "#/$defs/A"},
            },
            "$defs": {
                "A": {"properties": {"next": {"$ref": "#/$defs/B"}}},
                "B": {"properties": {"next": {"$ref": "#/$defs/A"}}},
            },
        }
        with pytest.raises(CircularRefError):
            resolve_refs(schema, module_id="test")

    def test_resolve_depth_exceeded(self):
        from apcore_cli.ref_resolver import MaxDepthExceededError

        # Build a chain of 33 refs
        defs = {}
        for i in range(33):
            next_key = f"R{i + 1}" if i < 32 else "R32"
            defs[f"R{i}"] = {"$ref": f"#/$defs/{next_key}"}
        defs["R32"] = {"type": "string"}
        schema = {
            "properties": {"field": {"$ref": "#/$defs/R0"}},
            "$defs": defs,
        }
        with pytest.raises(MaxDepthExceededError):
            resolve_refs(schema, max_depth=32, module_id="test")

    def test_resolve_unresolvable_ref(self):
        from apcore_cli.ref_resolver import UnresolvableRefError

        schema = {
            "properties": {
                "field": {"$ref": "#/$defs/Missing"},
            },
            "$defs": {},
        }
        with pytest.raises(UnresolvableRefError):
            resolve_refs(schema, module_id="test")

    def test_resolve_no_refs(self):
        schema = {
            "properties": {
                "name": {"type": "string"},
            },
        }
        result = resolve_refs(schema, module_id="test")
        assert result["properties"]["name"]["type"] == "string"

    def test_resolve_removes_defs(self):
        schema = {
            "properties": {"name": {"type": "string"}},
            "$defs": {"Foo": {"type": "integer"}},
        }
        result = resolve_refs(schema, module_id="test")
        assert "$defs" not in result
        assert "definitions" not in result


class TestComposition:
    """Task 6: allOf, anyOf, oneOf flattening."""

    def test_allof_merge_properties(self):
        schema = {
            "allOf": [
                {"properties": {"a": {"type": "string"}}, "required": ["a"]},
                {"properties": {"b": {"type": "integer"}}, "required": ["b"]},
            ],
        }
        result = resolve_refs(schema, module_id="test")
        assert "a" in result["properties"]
        assert "b" in result["properties"]
        assert "a" in result["required"]
        assert "b" in result["required"]

    def test_allof_later_overrides(self):
        schema = {
            "allOf": [
                {"properties": {"x": {"type": "string"}}},
                {"properties": {"x": {"type": "integer"}}},
            ],
        }
        result = resolve_refs(schema, module_id="test")
        assert result["properties"]["x"]["type"] == "integer"

    def test_anyof_union_properties(self):
        schema = {
            "anyOf": [
                {"properties": {"a": {"type": "string"}}},
                {"properties": {"b": {"type": "integer"}}},
            ],
        }
        result = resolve_refs(schema, module_id="test")
        assert "a" in result["properties"]
        assert "b" in result["properties"]

    def test_anyof_required_intersection(self):
        schema = {
            "anyOf": [
                {"properties": {"a": {"type": "string"}, "b": {"type": "string"}}, "required": ["a", "b"]},
                {"properties": {"a": {"type": "string"}, "c": {"type": "string"}}, "required": ["a", "c"]},
            ],
        }
        result = resolve_refs(schema, module_id="test")
        # Only "a" is required in BOTH branches
        assert "a" in result["required"]
        assert "b" not in result["required"]
        assert "c" not in result["required"]

    def test_oneof_same_as_anyof(self):
        schema = {
            "oneOf": [
                {"properties": {"x": {"type": "string"}}},
                {"properties": {"y": {"type": "integer"}}},
            ],
        }
        result = resolve_refs(schema, module_id="test")
        assert "x" in result["properties"]
        assert "y" in result["properties"]

    def test_nested_composition(self):
        schema = {
            "allOf": [
                {"$ref": "#/$defs/Base"},
                {"properties": {"extra": {"type": "string"}}},
            ],
            "$defs": {
                "Base": {
                    "properties": {"id": {"type": "integer"}},
                    "required": ["id"],
                },
            },
        }
        result = resolve_refs(schema, module_id="test")
        assert "id" in result["properties"]
        assert "extra" in result["properties"]
        assert "id" in result["required"]

    def test_allof_preserves_sibling_properties(self):
        """W2/W12: sibling 'properties' on the same node as allOf must not be
        dropped — they are valid per JSON Schema and expected by click-option
        generation (missing sibling props → missing Click flags)."""
        schema = {
            "allOf": [{"$ref": "#/$defs/Base"}],
            "properties": {"extra": {"type": "string"}},
            "required": ["extra"],
            "$defs": {"Base": {"properties": {"id": {"type": "integer"}}}},
        }
        result = resolve_refs(schema, module_id="test")
        assert "id" in result["properties"], "allOf-resolved property must be present"
        assert "extra" in result["properties"], "sibling property must not be dropped"
        assert "extra" in result["required"], "sibling required must not be dropped"

    def test_anyof_preserves_sibling_properties(self):
        """W12: same sibling-drop bug exists for anyOf."""
        schema = {
            "anyOf": [{"properties": {"a": {"type": "string"}}}],
            "properties": {"extra": {"type": "boolean"}},
            "required": ["extra"],
        }
        result = resolve_refs(schema, module_id="test")
        assert "extra" in result["properties"], "sibling property must not be dropped by anyOf"
        assert "a" in result["properties"], "anyOf-resolved property must be present"

    def test_oneof_preserves_sibling_properties(self):
        """W12: same sibling-drop bug exists for oneOf."""
        schema = {
            "oneOf": [{"properties": {"x": {"type": "integer"}}}],
            "properties": {"flag": {"type": "boolean"}},
        }
        result = resolve_refs(schema, module_id="test")
        assert "flag" in result["properties"], "sibling property must not be dropped by oneOf"
        assert "x" in result["properties"], "oneOf-resolved property must be present"


class TestRefResolverExceptions:
    """D10-006: resolve_refs must raise typed exceptions instead of sys.exit."""

    def test_resolve_refs_raises_circular_ref(self):
        """resolve_refs must raise CircularRefError (not sys.exit) for circular schemas."""
        from apcore_cli.ref_resolver import CircularRefError
        from apcore_cli.ref_resolver import resolve_refs as _resolve_refs

        schema = {
            "properties": {"node": {"$ref": "#/$defs/A"}},
            "$defs": {
                "A": {"properties": {"next": {"$ref": "#/$defs/B"}}},
                "B": {"properties": {"next": {"$ref": "#/$defs/A"}}},
            },
        }
        with pytest.raises(CircularRefError):
            _resolve_refs(schema, module_id="test")

    def test_resolve_refs_raises_unresolvable_ref(self):
        """resolve_refs must raise UnresolvableRefError for missing $ref targets."""
        from apcore_cli.ref_resolver import UnresolvableRefError
        from apcore_cli.ref_resolver import resolve_refs as _resolve_refs

        schema = {
            "properties": {"field": {"$ref": "#/$defs/Missing"}},
            "$defs": {},
        }
        with pytest.raises(UnresolvableRefError):
            _resolve_refs(schema, module_id="test")

    def test_resolve_refs_raises_max_depth(self):
        """resolve_refs must raise MaxDepthExceededError when depth limit is hit."""
        from apcore_cli.ref_resolver import MaxDepthExceededError
        from apcore_cli.ref_resolver import resolve_refs as _resolve_refs

        defs = {}
        for i in range(33):
            next_key = f"R{i + 1}" if i < 32 else "R32"
            defs[f"R{i}"] = {"$ref": f"#/$defs/{next_key}"}
        defs["R32"] = {"type": "string"}
        schema = {
            "properties": {"field": {"$ref": "#/$defs/R0"}},
            "$defs": defs,
        }
        with pytest.raises(MaxDepthExceededError):
            _resolve_refs(schema, max_depth=32, module_id="test")

    def test_ref_resolver_error_classes_exist(self):
        """RefResolverError hierarchy must exist in ref_resolver module."""
        from apcore_cli import ref_resolver as _ref_resolver

        assert hasattr(_ref_resolver, "RefResolverError")
        assert hasattr(_ref_resolver, "CircularRefError")
        assert hasattr(_ref_resolver, "UnresolvableRefError")
        assert hasattr(_ref_resolver, "MaxDepthExceededError")

    # Audit D11-NEW-001 (2026-05-08): a parent's `required` applies in
    # addition to anyOf/oneOf branch intersection — sibling required must
    # not be silently dropped. Cross-SDK parity locks this in.
    def test_anyof_preserves_parent_sibling_required(self):
        schema = {
            "type": "object",
            "required": ["x"],
            "anyOf": [
                {"properties": {"a": {"type": "string"}}, "required": ["a"]},
                {"properties": {"a": {"type": "integer"}}, "required": ["a"]},
            ],
        }
        result = resolve_refs(schema, module_id="test")
        # Sibling-first ordering: parent "x" before branch-intersection "a".
        assert result["required"] == ["x", "a"]

    def test_oneof_preserves_parent_sibling_required(self):
        schema = {
            "type": "object",
            "required": ["host", "port"],
            "oneOf": [
                {"properties": {"mode": {"const": "http"}}, "required": ["scheme"]},
                {"properties": {"mode": {"const": "tcp"}}, "required": ["scheme"]},
            ],
        }
        result = resolve_refs(schema, module_id="test")
        assert result["required"] == ["host", "port", "scheme"]

    def test_anyof_dedupes_overlap_between_sibling_and_branch_intersection(self):
        schema = {
            "type": "object",
            "required": ["a"],
            "anyOf": [
                {"required": ["a", "b"]},
                {"required": ["a", "c"]},
            ],
        }
        result = resolve_refs(schema, module_id="test")
        assert result["required"] == ["a"]

    # Audit D11-NEW-003 (2026-05-08): max_depth counts $ref hops only;
    # plain nested-properties recursion does NOT increment depth. A
    # deeply-nested non-ref schema must resolve cleanly.
    # D11-001: walk must descend into all dict-valued children, not just
    # "properties". Previously $ref under items/additionalProperties/
    # patternProperties was silently left unresolved.
    def test_resolve_ref_inside_array_items(self):
        schema = {
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/Tag"},
                },
            },
            "$defs": {
                "Tag": {"type": "object", "properties": {"label": {"type": "string"}}},
            },
        }
        result = resolve_refs(schema, module_id="test")
        items = result["properties"]["tags"]["items"]
        assert "properties" in items
        assert "label" in items["properties"]
        # $ref must have been replaced.
        assert "$ref" not in items

    def test_resolve_ref_inside_additional_properties(self):
        schema = {
            "type": "object",
            "additionalProperties": {"$ref": "#/$defs/Value"},
            "$defs": {
                "Value": {"type": "object", "properties": {"v": {"type": "integer"}}},
            },
        }
        result = resolve_refs(schema, module_id="test")
        ap = result["additionalProperties"]
        assert "properties" in ap
        assert "v" in ap["properties"]
        assert "$ref" not in ap

    def test_resolve_ref_inside_pattern_properties(self):
        schema = {
            "type": "object",
            "patternProperties": {
                "^x_": {"$ref": "#/$defs/Ext"},
            },
            "$defs": {
                "Ext": {"type": "object", "properties": {"ext": {"type": "string"}}},
            },
        }
        result = resolve_refs(schema, module_id="test")
        ext = result["patternProperties"]["^x_"]
        assert "properties" in ext
        assert "ext" in ext["properties"]
        assert "$ref" not in ext

    # D11-002: diamond $ref pattern — two sibling properties reference the
    # same $def. Previously copy-on-write visited-set with reuse across
    # siblings could trigger MaxDepthExceededError on schemas that share defs.
    def test_diamond_ref_pattern_resolves_without_max_depth(self):
        schema = {
            "type": "object",
            "properties": {
                "first": {"$ref": "#/$defs/Shared"},
                "second": {"$ref": "#/$defs/Shared"},
            },
            "$defs": {
                "Shared": {"type": "object", "properties": {"v": {"type": "string"}}},
            },
        }
        # Use a small max_depth to ensure stale visited entries would matter.
        result = resolve_refs(schema, max_depth=5, module_id="test")
        assert "v" in result["properties"]["first"]["properties"]
        assert "v" in result["properties"]["second"]["properties"]

    def test_deep_nested_properties_does_not_count_against_max_depth(self):
        nested: dict = {"type": "string"}
        for _ in range(50):
            nested = {"type": "object", "properties": {"inner": nested}}
        schema = {"type": "object", "properties": {"root": nested}}
        # Pre-fix this raised MaxDepthExceededError at the 32nd level.
        result = resolve_refs(schema, max_depth=32, module_id="test")
        # Spot-check that the 50-level chain made it through.
        cur = result["properties"]["root"]
        depth_seen = 0
        while isinstance(cur, dict) and "properties" in cur and "inner" in cur["properties"]:
            cur = cur["properties"]["inner"]
            depth_seen += 1
        assert depth_seen == 50


class TestOptionalTypeRecovery:
    """Optional[X] (Pydantic v2 anyOf-null) must recover the scalar `type` so the
    schema parser does not fall through to a "no type" warning."""

    def test_optional_scalar_recovers_type(self):
        result = resolve_refs(
            {
                "type": "object",
                "properties": {"name": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
            }
        )
        assert result["properties"]["name"]["type"] == "string"

    def test_optional_list_form_type_recovers_string(self):
        result = resolve_refs(
            {
                "type": "object",
                "properties": {"name": {"anyOf": [{"type": ["string", "null"]}, {"type": "null"}]}},
            }
        )
        assert result["properties"]["name"]["type"] == "string"
