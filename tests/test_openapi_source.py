"""FE-15a source loading and proxy-hazard detection.

Covers the T-OAPI-* rows that sit below the command layer: 02 (content
sniffing), 11 (external $ref), 12 (non-3.x), 15 (missing HTTP extra), 16/17
(hazard detection) and the §6 error-message table.
"""

from __future__ import annotations

import json

import pytest
from apcore_toolkit import OpenAPIScanner

from apcore_cli.openapi_source import (
    Hazard,
    detect_proxy_hazards,
    load_openapi_source,
    parse_headers,
)

_PETSTORE_YAML = """\
openapi: "3.1.0"
info:
  title: Petstore
  version: "1.0.0"
paths:
  /pets:
    get:
      operationId: listPets
      summary: List all pets
      parameters:
        - name: limit
          in: query
          schema: {type: integer}
      responses:
        "200":
          description: ok
    post:
      operationId: createPets
      summary: Create a pet
      parameters:
        - name: dryRun
          in: query
          schema: {type: boolean}
        - name: notify
          in: query
          schema: {type: boolean}
      responses:
        "201":
          description: created
"""


def _write(tmp_path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# load_openapi_source (§4.1)
# ---------------------------------------------------------------------------


class TestLoadOpenapiSource:
    def test_loads_yaml(self, tmp_path):
        spec = load_openapi_source(_write(tmp_path, "openapi.yaml", _PETSTORE_YAML))
        assert spec["openapi"] == "3.1.0"
        assert "/pets" in spec["paths"]

    def test_loads_json_by_content_sniffing_not_extension(self, tmp_path):
        """T-OAPI-02: a JSON body in a .yaml file still parses as JSON."""
        doc = {"openapi": "3.0.3", "info": {"title": "x", "version": "1"}, "paths": {}}
        spec = load_openapi_source(_write(tmp_path, "openapi.yaml", json.dumps(doc)))
        assert spec == doc

    def test_missing_file_exits_47(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc:
            load_openapi_source(str(tmp_path / "nope.yaml"))
        assert exc.value.code == 47
        assert "Cannot read OpenAPI source" in capsys.readouterr().err

    def test_malformed_yaml_exits_47(self, tmp_path, capsys):
        path = _write(tmp_path, "bad.yaml", "openapi: '3.1.0'\n  bad: [indent\n")
        with pytest.raises(SystemExit) as exc:
            load_openapi_source(path)
        assert exc.value.code == 47
        assert "Cannot parse OpenAPI source" in capsys.readouterr().err

    def test_malformed_json_exits_47(self, tmp_path, capsys):
        path = _write(tmp_path, "bad.json", '{"openapi": "3.1.0",}')
        with pytest.raises(SystemExit) as exc:
            load_openapi_source(path)
        assert exc.value.code == 47
        assert "Cannot parse OpenAPI source" in capsys.readouterr().err

    def test_missing_http_extra_names_the_extra(self, monkeypatch, capsys):
        """T-OAPI-15: never a bare ImportError."""
        import apcore_toolkit

        def _boom(*_args, **_kwargs):
            raise ImportError("No module named 'httpx'")

        monkeypatch.setattr(apcore_toolkit, "load_spec", _boom)
        with pytest.raises(SystemExit) as exc:
            load_openapi_source("https://example.invalid/openapi.json")
        assert exc.value.code == 47
        err = capsys.readouterr().err
        assert "http-proxy" in err
        assert "apcore-toolkit[http-proxy]" in err

    def test_http_status_error_reports_status(self, monkeypatch, capsys):
        import apcore_toolkit

        class _Response:
            status_code = 404

        class _HttpError(Exception):
            __module__ = "httpx._exceptions"

            def __init__(self):
                super().__init__("boom")
                self.response = _Response()

        def _boom(*_args, **_kwargs):
            raise _HttpError()

        monkeypatch.setattr(apcore_toolkit, "load_spec", _boom)
        with pytest.raises(SystemExit) as exc:
            load_openapi_source("https://example.invalid/openapi.json")
        assert exc.value.code == 47
        assert "HTTP 404" in capsys.readouterr().err

    def test_headers_are_forwarded_to_load_spec(self, monkeypatch, tmp_path):
        import apcore_toolkit

        seen: dict = {}

        def _capture(source, *, headers=None, timeout=30.0, **_kwargs):
            seen["headers"] = headers
            seen["timeout"] = timeout
            return {"openapi": "3.1.0", "paths": {}}

        monkeypatch.setattr(apcore_toolkit, "load_spec", _capture)
        load_openapi_source("https://example.invalid/x", headers=["Authorization: Bearer t"], timeout=5.0)
        assert seen["headers"] == {"Authorization": "Bearer t"}
        assert seen["timeout"] == 5.0


class TestParseHeaders:
    def test_parses_key_value(self):
        assert parse_headers(["A: 1", "B:2"]) == {"A": "1", "B": "2"}

    def test_none_is_empty(self):
        assert parse_headers(None) == {}

    def test_malformed_header_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc:
            parse_headers(["no-colon"])
        assert exc.value.code == 2
        assert "expected 'Key: Value'" in capsys.readouterr().err

    def test_empty_name_exits_2(self):
        with pytest.raises(SystemExit) as exc:
            parse_headers([": value"])
        assert exc.value.code == 2


# ---------------------------------------------------------------------------
# detect_proxy_hazards (§4.3)
# ---------------------------------------------------------------------------


class TestDetectProxyHazards:
    def test_post_with_query_params_is_a_hazard(self, tmp_path):
        """T-OAPI-16."""
        spec = load_openapi_source(_write(tmp_path, "o.yaml", _PETSTORE_YAML))
        modules = OpenAPIScanner().scan(spec)
        hazards = detect_proxy_hazards(spec, modules)
        assert len(hazards) == 1
        hazard = hazards[0]
        assert hazard.module_id == "createPets"
        assert hazard.http_method == "POST"
        assert hazard.url_path == "/pets"
        assert hazard.parameters == ("dryRun", "notify")

    def test_get_with_query_params_is_not_a_hazard(self, tmp_path):
        """T-OAPI-17: query is the correct location for GET."""
        spec = load_openapi_source(_write(tmp_path, "o.yaml", _PETSTORE_YAML))
        modules = OpenAPIScanner().scan(spec)
        assert all(h.http_method != "GET" for h in detect_proxy_hazards(spec, modules))

    @pytest.mark.parametrize("method", ["put", "patch"])
    def test_put_and_patch_are_body_methods(self, method):
        spec = {
            "openapi": "3.1.0",
            "paths": {
                "/x": {
                    method: {
                        "operationId": "opX",
                        "parameters": [{"name": "q", "in": "query"}],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
        modules = OpenAPIScanner().scan(spec)
        hazards = detect_proxy_hazards(spec, modules)
        assert [h.http_method for h in hazards] == [method.upper()]

    def test_path_and_header_params_are_not_hazards(self):
        spec = {
            "openapi": "3.1.0",
            "paths": {
                "/x/{id}": {
                    "post": {
                        "operationId": "opX",
                        "parameters": [
                            {"name": "id", "in": "path"},
                            {"name": "X-Trace", "in": "header"},
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
        modules = OpenAPIScanner().scan(spec)
        assert detect_proxy_hazards(spec, modules) == []

    def test_ref_parameters_are_resolved(self):
        spec = {
            "openapi": "3.1.0",
            "components": {"parameters": {"DryRun": {"name": "dryRun", "in": "query"}}},
            "paths": {
                "/x": {
                    "post": {
                        "operationId": "opX",
                        "parameters": [{"$ref": "#/components/parameters/DryRun"}],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
        modules = OpenAPIScanner().scan(spec)
        hazards = detect_proxy_hazards(spec, modules)
        assert hazards[0].parameters == ("dryRun",)

    def test_resolve_ref_raising_yields_no_hazard_not_an_exception(self, monkeypatch):
        """openapi_source.py:185 — ``_parameter_entries`` called
        ``apcore_toolkit.openapi.resolve_ref`` with no exception handling,
        contradicting its own docstring's "a malformed entry yields nothing
        rather than an exception" guarantee. Against the currently pinned
        apcore-toolkit, ``resolve_ref`` happens to be a total function (it
        returns ``{}`` rather than raising for every unresolvable pointer,
        including an external ``$ref``) so this cannot be reproduced with a
        real spec today — this test mocks ``resolve_ref`` to raise, so the
        CLI-side defensive guard is exercised regardless of what any given
        toolkit version actually does internally.
        """
        import apcore_toolkit.openapi as toolkit_openapi

        def _boom(*_args, **_kwargs):
            raise RuntimeError("simulated unresolvable $ref")

        monkeypatch.setattr(toolkit_openapi, "resolve_ref", _boom)

        spec = {
            "openapi": "3.1.0",
            "paths": {
                "/x": {
                    "post": {
                        "operationId": "opX",
                        "parameters": [{"$ref": "./external.yaml#/components/parameters/DryRun"}],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
        modules = OpenAPIScanner().scan(spec)
        # Must not raise, and the unresolvable entry contributes no hazard.
        assert detect_proxy_hazards(spec, modules) == []

    def test_malformed_entries_yield_no_hazard_not_an_exception(self):
        spec = {
            "openapi": "3.1.0",
            "paths": {
                "/x": {
                    "post": {
                        "operationId": "opX",
                        "parameters": ["not-a-mapping", {"$ref": "#/nope/missing"}, {"in": "query"}],
                        "responses": {"200": {"description": "ok"}},
                    }
                },
                "/bad": "not-a-path-item",
            },
        }
        modules = OpenAPIScanner().scan(spec)
        assert detect_proxy_hazards(spec, modules) == []

    def test_filtered_out_operations_produce_no_hazard(self, tmp_path):
        """An operation with no module is nothing FE-15b could misroute."""
        spec = load_openapi_source(_write(tmp_path, "o.yaml", _PETSTORE_YAML))
        modules = OpenAPIScanner().scan(spec, include="^listPets$")
        assert detect_proxy_hazards(spec, modules) == []

    def test_empty_and_non_dict_specs_are_safe(self):
        assert detect_proxy_hazards({}, []) == []
        assert detect_proxy_hazards({"paths": "nope"}, []) == []
        assert detect_proxy_hazards("nope", []) == []  # type: ignore[arg-type]

    def test_hazard_serialization(self):
        hazard = Hazard(module_id="m", http_method="POST", url_path="/p", parameters=("a",))
        assert hazard.to_dict() == {
            "module_id": "m",
            "http_method": "POST",
            "url_path": "/p",
            "parameters": ["a"],
        }
        assert "1 `in: query` parameter (a)" in hazard.describe()
