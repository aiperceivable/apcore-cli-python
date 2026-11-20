"""Diagnostics produced from toolkit's normalized OpenAPI schema inputs."""

import json
import os
from pathlib import Path

import pytest
from apcore_toolkit import OpenAPIScanner, ScannedModule

from apcore_cli.openapi_source import detect_effective_proxy_hazards, detect_proxy_hazards


def test_cli_reexports_toolkit_proxy_diagnostics() -> None:
    from apcore_toolkit import detect_effective_proxy_hazards as toolkit_detector

    assert detect_effective_proxy_hazards is toolkit_detector


def test_selected_modules_report_collisions_and_inherited_body_query():
    spec = {
        "openapi": "3.1.0",
        "components": {"parameters": {"Term": {"name": "term", "in": "query"}}},
        "paths": {
            "/bad/{id}": {
                "parameters": [
                    {"name": "id", "in": "path", "required": True},
                    {"$ref": "#/components/parameters/Term"},
                ],
                "post": {
                    "operationId": "bad",
                    "requestBody": {"content": {"application/json": {"schema": {"properties": {"id": {}}}}}},
                },
            },
            "/collision/{id}": {
                "parameters": [{"name": "id", "in": "path", "required": True}],
                "get": {
                    "operationId": "collision",
                    "parameters": [{"name": "id", "in": "query"}],
                },
            },
            "/good": {"get": {"operationId": "good"}},
        },
    }
    scanner = OpenAPIScanner()
    assert detect_proxy_hazards(spec, scanner.scan(spec)) == []
    modules = scanner.scan_with_effective_parameters(spec, base_path_prefix="api")
    hazards = detect_effective_proxy_hazards(modules)
    assert {hazard.module_id for hazard in hazards} == {"api.bad", "api.collision"}
    assert [(hazard.kind, hazard.parameters, hazard.locations) for hazard in hazards] == [
        ("query_on_body_method", ("term",), ("query",)),
        ("name_collision", ("id",), ("path", "body")),
        ("name_collision", ("id",), ("path", "query")),
    ]
    assert "api.bad (POST /bad/{id})" in hazards[0].describe()
    assert hazards[1].to_dict()["kind"]["locations"] == ["path", "body"]
    assert detect_effective_proxy_hazards([module for module in modules if module.module_id == "api.good"]) == []


def test_diagnostic_reads_transformed_inputs_and_flags_missing_fields():
    spec = {
        "openapi": "3.1.0",
        "paths": {
            "/items": {
                "post": {"operationId": "items", "parameters": [{"name": "old", "in": "query"}]},
            }
        },
    }
    scanner = OpenAPIScanner()

    def transform_operation(path, method, operation):
        return {**operation, "parameters": [{"name": "new", "in": "query"}]}

    def transform_module(module):
        module.input_schema["properties"].clear()
        return module

    legacy = scanner.scan(spec)
    assert detect_proxy_hazards(spec, legacy)[0].parameters == ("old",)
    assert detect_effective_proxy_hazards(legacy)[0].kind == "missing_effective_inputs"
    [module] = scanner.scan_with_effective_parameters(
        spec, transform_operation=transform_operation, transform_module=transform_module
    )
    query, mismatch = detect_effective_proxy_hazards([module])
    assert (query.kind, query.parameters) == ("query_on_body_method", ("new",))
    assert (mismatch.kind, mismatch.parameters) == ("input_schema_mismatch", ("new",))
    module.metadata["openapi"]["proxy_inputs"]["query"] = "invalid"
    assert detect_effective_proxy_hazards([module])[0].kind == "invalid_effective_inputs"


def test_diagnostic_follows_framework_route_overrides():
    [module] = OpenAPIScanner().scan_with_effective_parameters(
        {
            "openapi": "3.1.0",
            "paths": {"/metadata": {"get": {"operationId": "route", "parameters": [{"name": "term", "in": "query"}]}}},
        }
    )
    module.http_method = "POST"
    module.url_path = "/override"
    [hazard] = detect_effective_proxy_hazards([module])
    assert hazard.to_dict() == {
        "module_id": module.module_id,
        "http_method": "POST",
        "url_path": "/override",
        "kind": {"code": "query_on_body_method", "parameters": ["term"]},
    }

    module.http_method = "post"
    [hazard] = detect_effective_proxy_hazards([module])
    assert hazard.to_dict()["http_method"] == "post"
    assert hazard.to_dict()["kind"] == {"code": "unsupported_http_method", "method": "post"}


def _load_shared_fixture(name):
    default_spec_repo = Path(__file__).resolve().parents[2] / "apcore-cli"
    explicit_spec_repo = os.environ.get("APCORE_CLI_SPEC_REPO")
    spec_repo = Path(explicit_spec_repo) if explicit_spec_repo is not None else default_spec_repo
    fixture = spec_repo / "conformance" / "fixtures" / name
    if not fixture.is_file():
        if explicit_spec_repo is not None:
            pytest.fail(f"APCORE_CLI_SPEC_REPO is missing shared conformance fixture: {fixture}")
        pytest.skip("shared apcore-cli conformance fixture is not available in this checkout")
    data = json.loads(fixture.read_text(encoding="utf-8"))
    assert data["version"] == 1
    return data["cases"]


def test_effective_proxy_hazard_conformance():
    for case in _load_shared_fixture("openapi_effective_hazards.json"):
        module = ScannedModule(description="", output_schema={}, tags=[], target="", **case["module"])
        actual = [hazard.to_dict() for hazard in detect_effective_proxy_hazards([module])]
        for found, expected in zip(actual, case["expected"], strict=False):
            if expected["kind"]["code"] == "invalid_effective_inputs" and "detail" not in expected["kind"]:
                assert found["kind"].pop("detail").strip(), case["id"]
        assert actual == case["expected"], case["id"]


def test_effective_openapi_document_conformance():
    scanner = OpenAPIScanner()
    for case in _load_shared_fixture("openapi_effective_documents.json"):
        modules = scanner.scan_with_effective_parameters(case["document"], include=case.get("include"))
        expected = case["expected"]
        assert [module.module_id for module in modules] == [module["id"] for module in expected["modules"]], case["id"]
        for module, expected_module in zip(modules, expected["modules"], strict=True):
            for key in ("properties", "required"):
                if key in expected_module:
                    assert module.input_schema[key] == expected_module[key], case["id"]
            if "output_schema" in expected_module:
                assert module.output_schema == expected_module["output_schema"], case["id"]
        actual = [hazard.to_dict() for hazard in detect_effective_proxy_hazards(modules)]
        assert actual == expected["diagnostics"], case["id"]
