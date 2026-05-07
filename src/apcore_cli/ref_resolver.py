"""$ref resolution and schema composition (FE-02)."""

from __future__ import annotations

import copy
from typing import Any


class RefResolverError(Exception):
    """Base class for $ref resolution errors."""


class CircularRefError(RefResolverError):
    """Raised when a circular $ref is detected."""


class UnresolvableRefError(RefResolverError):
    """Raised when a $ref target cannot be found."""


class MaxDepthExceededError(RefResolverError):
    """Raised when $ref resolution depth exceeds the configured maximum."""


def resolve_refs(schema: dict, max_depth: int = 32, module_id: str = "") -> dict:
    """Resolve all $ref references in a JSON Schema.

    Returns a fully inlined schema with $defs/definitions removed.
    """
    schema = copy.deepcopy(schema)
    defs = schema.get("$defs", schema.get("definitions", {}))
    result = _resolve_node(schema, defs, visited=set(), depth=0, max_depth=max_depth, module_id=module_id)

    # Remove definition keys
    result.pop("$defs", None)
    result.pop("definitions", None)
    return result


def _resolve_node(
    node: Any,
    defs: dict,
    visited: set,
    depth: int,
    max_depth: int,
    module_id: str = "",
) -> Any:
    """Recursively resolve $ref, allOf, anyOf, oneOf in a schema node."""
    if not isinstance(node, dict):
        return node

    # Handle $ref
    if "$ref" in node:
        ref_path = node["$ref"]

        if depth >= max_depth:
            raise MaxDepthExceededError(
                f"$ref resolution depth exceeded maximum of {max_depth} for module '{module_id}'."
            )

        if ref_path in visited:
            raise CircularRefError(f"Circular $ref detected in schema for module '{module_id}' at path '{ref_path}'.")

        # Parse ref target: extract key from "#/$defs/Address" → "Address"
        parts = ref_path.split("/")
        key = parts[-1]

        if key not in defs:
            raise UnresolvableRefError(f"Unresolvable $ref '{ref_path}' in schema for module '{module_id}'.")

        visited = visited | {ref_path}
        return _resolve_node(defs[key], defs, visited, depth + 1, max_depth, module_id)

    # Handle allOf
    if "allOf" in node:
        merged: dict[str, Any] = {"properties": {}, "required": []}
        # Merge sibling properties/required from the composing node itself first
        # so the composition branches can extend (not overwrite) them.
        if isinstance(node.get("properties"), dict):
            merged["properties"].update(node["properties"])
        if isinstance(node.get("required"), list):
            merged["required"].extend(node["required"])
        for sub_schema in node["allOf"]:
            resolved = _resolve_node(sub_schema, defs, visited, depth + 1, max_depth, module_id)
            if "properties" in resolved:
                merged["properties"].update(resolved["properties"])
            if "required" in resolved:
                merged["required"].extend(resolved["required"])
        # Copy remaining non-composition keys (skip already-handled ones)
        for k, v in node.items():
            if k not in ("allOf", "properties", "required") and k not in merged:
                merged[k] = v
        return merged

    # Handle anyOf / oneOf
    for keyword in ("anyOf", "oneOf"):
        if keyword in node:
            merged = {"properties": {}, "required": []}
            # Merge sibling properties from the composing node first.
            if isinstance(node.get("properties"), dict):
                merged["properties"].update(node["properties"])
            sibling_required: list[str] = list(node["required"]) if isinstance(node.get("required"), list) else []
            all_required_sets: list[set[str]] = []
            for sub_schema in node[keyword]:
                resolved = _resolve_node(sub_schema, defs, visited, depth + 1, max_depth, module_id)
                if "properties" in resolved:
                    merged["properties"].update(resolved["properties"])
                if "required" in resolved:
                    all_required_sets.append(set(resolved["required"]))
            # Required = sibling required ∪ intersection of all branches
            branch_required = list(set.intersection(*all_required_sets)) if all_required_sets else []
            seen: set[str] = set()
            combined_required: list[str] = []
            for r in sibling_required + branch_required:
                if r not in seen:
                    seen.add(r)
                    combined_required.append(r)
            merged["required"] = combined_required
            # Copy remaining non-composition keys
            for k, v in node.items():
                if k not in (keyword, "properties", "required") and k not in merged:
                    merged[k] = v
            return merged

    # Recursively process nested properties.
    # Audit D11-NEW-003 (2026-05-08): max_depth counts $ref hops only — plain
    # nested-properties recursion does NOT increment `depth`. Previously
    # incrementing here caused valid deeply-nested schemas (>32 levels of
    # nested `properties`) to be rejected even when no $ref chain existed.
    # Aligned with Rust's interpretation of the spec ("Maximum $ref
    # resolution recursion depth", schema-parser.md §Contract).
    if "properties" in node:
        for prop_name, prop_schema in node["properties"].items():
            node["properties"][prop_name] = _resolve_node(prop_schema, defs, visited, depth, max_depth, module_id)

    return node
