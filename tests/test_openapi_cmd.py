"""FE-15a ``apcli openapi`` subcommand group.

Covers T-OAPI-01..14, 18..27 — everything the CLI layer owns. Module-ID
derivation itself is the toolkit's conformance corpus and is deliberately not
re-tested here (§1.2): these assertions check that the CLI forwards options
verbatim and renders what comes back.
"""

from __future__ import annotations

import json

import click
import pytest
import yaml
from click.testing import CliRunner

from apcore_cli.openapi_cmd import register_openapi_command

_PETSTORE = """\
openapi: "3.1.0"
info:
  title: Petstore
  version: "1.0.0"
paths:
  /pets:
    get:
      operationId: listPets
      summary: List all pets
      tags: [pets]
      responses:
        "200":
          description: ok
    post:
      operationId: createPets
      summary: Create a pet
      tags: [pets]
      parameters:
        - name: dryRun
          in: query
          schema: {type: boolean}
      responses:
        "201":
          description: created
  /pets/{petId}:
    delete:
      tags: [pets]
      responses: {}
    get:
      operationId: showPetById
      deprecated: true
      tags: [pets]
      responses:
        "200":
          description: ok
"""

_SWAGGER_2 = """\
swagger: "2.0"
info:
  title: Old
  version: "1.0.0"
paths: {}
"""


@pytest.fixture
def cli() -> click.Group:
    @click.group()
    def apcli() -> None:
        pass

    register_openapi_command(apcli)
    return apcli


@pytest.fixture
def petstore(tmp_path) -> str:
    path = tmp_path / "openapi.yaml"
    path.write_text(_PETSTORE, encoding="utf-8")
    return str(path)


def _scan_json(cli: click.Group, source: str, *extra: str) -> dict:
    result = CliRunner().invoke(cli, ["openapi", "scan", source, "--format", "json", *extra])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


# ---------------------------------------------------------------------------
# apcli openapi scan (§4.2)
# ---------------------------------------------------------------------------


class TestOpenapiScan:
    def test_one_module_per_operation(self, cli, petstore):
        """T-OAPI-01."""
        payload = _scan_json(cli, petstore)
        assert len(payload["modules"]) == 4
        assert payload["openapi_version"] == "3.1.0"
        assert payload["source"] == petstore

    def test_json_source_matches_yaml(self, cli, tmp_path, petstore):
        """T-OAPI-02."""
        doc = yaml.safe_load(_PETSTORE)
        json_path = tmp_path / "openapi.json"
        json_path.write_text(json.dumps(doc), encoding="utf-8")
        assert _scan_json(cli, str(json_path))["modules"] == _scan_json(cli, petstore)["modules"]

    def test_operation_id_case_is_preserved(self, cli, petstore):
        """T-OAPI-03: the CLI never post-processes the toolkit's IDs."""
        ids = [m["module_id"] for m in _scan_json(cli, petstore)["modules"]]
        assert "listPets" in ids
        assert "createPets" in ids

    def test_path_and_method_algorithm_without_operation_id(self, cli, petstore):
        """T-OAPI-04."""
        ids = [m["module_id"] for m in _scan_json(cli, petstore)["modules"]]
        assert "pets.petid.delete" in ids

    def test_prefix_is_applied(self, cli, petstore):
        """T-OAPI-05."""
        ids = [m["module_id"] for m in _scan_json(cli, petstore, "--prefix", "api")["modules"]]
        assert all(mid.startswith("api.") for mid in ids)

    def test_include_filter(self, cli, petstore):
        """T-OAPI-06."""
        ids = [m["module_id"] for m in _scan_json(cli, petstore, "--include", "^pets")["modules"]]
        assert ids == ["pets.petid.delete"]

    def test_exclude_filter(self, cli, petstore):
        ids = [m["module_id"] for m in _scan_json(cli, petstore, "--exclude", "^pets")["modules"]]
        assert "pets.petid.delete" not in ids

    @pytest.mark.parametrize("flag", ["--include", "--exclude"])
    def test_invalid_regex_exits_2(self, cli, petstore, flag):
        """T-OAPI-07."""
        result = CliRunner().invoke(cli, ["openapi", "scan", petstore, flag, "([unclosed"])
        assert result.exit_code == 2
        assert f"Invalid regex for {flag}" in result.output

    def test_no_deprecated_omits_deprecated_operations(self, cli, petstore):
        """T-OAPI-08."""
        ids = [m["module_id"] for m in _scan_json(cli, petstore, "--no-deprecated")["modules"]]
        assert "showPetById" not in ids
        assert "listPets" in ids

    def test_string_false_deprecated_is_not_deprecated(self, cli, tmp_path):
        """T-OAPI-09: `deprecated: "false"` is a malformed boolean, not True."""
        doc = {
            "openapi": "3.1.0",
            "info": {"title": "x", "version": "1"},
            "paths": {
                "/x": {
                    "get": {
                        "operationId": "opX",
                        "deprecated": "false",
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
        path = tmp_path / "o.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        ids = [m["module_id"] for m in _scan_json(cli, str(path), "--no-deprecated")["modules"]]
        assert ids == ["opX"]

    def test_no_2xx_response_warns_but_keeps_the_module(self, cli, petstore):
        """T-OAPI-10."""
        payload = _scan_json(cli, petstore)
        delete = next(m for m in payload["modules"] if m["module_id"] == "pets.petid.delete")
        assert any("no 2xx response" in w for w in delete["warnings"])

    def test_external_ref_warning_is_rendered_and_not_fetched(self, cli, tmp_path):
        """T-OAPI-11."""
        doc = {
            "openapi": "3.1.0",
            "info": {"title": "x", "version": "1"},
            "paths": {
                "/x": {
                    "get": {
                        "operationId": "opX",
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {"application/json": {"schema": {"$ref": "./common.yaml#/Error"}}},
                            }
                        },
                    }
                }
            },
        }
        path = tmp_path / "o.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        payload = _scan_json(cli, str(path))
        warnings = payload["modules"][0]["warnings"]
        assert any("external $ref not fetched" in w and "./common.yaml#/Error" in w for w in warnings)

        table = CliRunner().invoke(cli, ["openapi", "scan", str(path)])
        assert "external $ref not fetched" in table.output

    def test_swagger_2_exits_47(self, cli, tmp_path):
        """T-OAPI-12: the toolkit's message, verbatim."""
        path = tmp_path / "swagger.yaml"
        path.write_text(_SWAGGER_2, encoding="utf-8")
        result = CliRunner().invoke(cli, ["openapi", "scan", str(path)])
        assert result.exit_code == 47
        assert "swagger" in result.output
        assert "3.0.x or 3.1.x" in result.output

    def test_json_shape_carries_warnings_and_top_level_hazards(self, cli, petstore):
        """T-OAPI-13."""
        payload = _scan_json(cli, petstore)
        assert all("warnings" in m for m in payload["modules"])
        assert payload["hazards"] == [
            {
                "module_id": "createPets",
                "http_method": "POST",
                "url_path": "/pets",
                "parameters": ["dryRun"],
            }
        ]

    @pytest.mark.parametrize("style", ["markdown", "skill"])
    def test_toolkit_styles_render(self, cli, petstore, style):
        """T-OAPI-14: ScannedModule goes straight to format_modules."""
        result = CliRunner().invoke(cli, ["openapi", "scan", petstore, "--format", style])
        assert result.exit_code == 0
        assert "listPets" in result.output

    @pytest.mark.parametrize("style", ["csv", "yaml", "jsonl"])
    def test_tabular_styles_render(self, cli, petstore, style):
        result = CliRunner().invoke(cli, ["openapi", "scan", petstore, "--format", style])
        assert result.exit_code == 0
        assert "listPets" in result.output

    def test_table_renders_banner_warnings_and_hazards(self, cli, petstore):
        result = CliRunner().invoke(cli, ["openapi", "scan", petstore], env={"COLUMNS": "200"})
        assert result.exit_code == 0
        assert "4 operations from" in result.output
        assert "OpenAPI 3.1.0, Petstore 1.0.0" in result.output
        assert "GET /pets" in result.output
        assert "1 warning" in result.output
        assert "cannot be proxied by FE-15b" in result.output
        assert "createPets" in result.output

    def test_hazards_do_not_change_the_exit_code(self, cli, petstore):
        """A partially-understood document is still a successful scan."""
        assert CliRunner().invoke(cli, ["openapi", "scan", petstore]).exit_code == 0


# ---------------------------------------------------------------------------
# apcli openapi generate (§4.4)
# ---------------------------------------------------------------------------


class TestOpenapiGenerate:
    def test_writes_one_binding_per_module(self, cli, petstore, tmp_path):
        """T-OAPI-18."""
        out = tmp_path / "out"
        result = CliRunner().invoke(cli, ["openapi", "generate", petstore, "-o", str(out)])
        assert result.exit_code == 0, result.output
        written = sorted(p.name for p in out.glob("*.binding.yaml"))
        assert written == [
            "createPets.binding.yaml",
            "listPets.binding.yaml",
            "pets.petid.delete.binding.yaml",
            "showPetById.binding.yaml",
        ]

    def test_dry_run_lists_paths_and_writes_nothing(self, cli, petstore, tmp_path):
        """T-OAPI-19."""
        out = tmp_path / "out"
        result = CliRunner().invoke(cli, ["openapi", "generate", petstore, "-o", str(out), "--dry-run"])
        assert result.exit_code == 0
        assert "listPets.binding.yaml" in result.output
        assert not out.exists()

    def test_artifact_carries_an_intact_routing_contract(self, cli, petstore, tmp_path):
        """T-OAPI-20: target + metadata survive a BindingLoader round-trip."""
        from apcore_toolkit import BindingLoader

        out = tmp_path / "out"
        CliRunner().invoke(cli, ["openapi", "generate", petstore, "-o", str(out)])

        raw = yaml.safe_load((out / "createPets.binding.yaml").read_text(encoding="utf-8"))
        binding = raw["bindings"][0]
        assert binding["target"] == "POST /pets"
        assert binding["metadata"]["http_method"] == "POST"
        assert binding["metadata"]["url_path"] == "/pets"
        assert binding["metadata"]["openapi"]["operation_id"] == "createPets"

        reloaded = BindingLoader().load(str(out / "createPets.binding.yaml"))
        assert reloaded[0].metadata["http_method"] == "POST"
        assert reloaded[0].metadata["url_path"] == "/pets"

    def test_no_base_url_is_written(self, cli, petstore, tmp_path):
        out = tmp_path / "out"
        CliRunner().invoke(cli, ["openapi", "generate", petstore, "-o", str(out)])
        for path in out.glob("*.binding.yaml"):
            assert "base_url" not in path.read_text(encoding="utf-8")

    def test_existing_file_is_skipped_without_force(self, cli, petstore, tmp_path):
        """T-OAPI-21: non-destructive default, matching `apcli init`."""
        out = tmp_path / "out"
        CliRunner().invoke(cli, ["openapi", "generate", petstore, "-o", str(out)])
        target = out / "listPets.binding.yaml"
        target.write_text("SENTINEL", encoding="utf-8")

        result = CliRunner().invoke(cli, ["openapi", "generate", petstore, "-o", str(out)])
        assert result.exit_code == 0
        assert "already exists" in result.output
        assert target.read_text(encoding="utf-8") == "SENTINEL"

    def test_force_overwrites(self, cli, petstore, tmp_path):
        """T-OAPI-22."""
        out = tmp_path / "out"
        CliRunner().invoke(cli, ["openapi", "generate", petstore, "-o", str(out)])
        target = out / "listPets.binding.yaml"
        target.write_text("SENTINEL", encoding="utf-8")

        result = CliRunner().invoke(cli, ["openapi", "generate", petstore, "-o", str(out), "--force"])
        assert result.exit_code == 0
        assert target.read_text(encoding="utf-8") != "SENTINEL"

    def test_there_is_no_writer_flag(self, cli, petstore, tmp_path):
        """§4.4: binding YAML is the only output, so no `--writer` exists.

        T-OAPI-23 is withdrawn. Every toolkit source writer resolves `target`
        as a `module.path:callable` import path and an OpenAPI target is
        always a route descriptor, so a source writer could never succeed for
        any input this command produces — and a flag that always fails is
        worse than no flag. Asserted rather than merely deleted so a
        re-introduction has to be deliberate.
        """
        out = tmp_path / "out"
        for value in ("native", "yaml"):
            result = CliRunner().invoke(cli, ["openapi", "generate", petstore, "-o", str(out), "--writer", value])
            assert result.exit_code == 2
            assert "no such option" in result.output.lower()
        assert not out.exists()

        help_text = CliRunner().invoke(cli, ["openapi", "generate", "--help"]).output
        assert "--writer" not in help_text

    def test_header_credentials_never_reach_disk(self, cli, tmp_path, monkeypatch):
        """T-OAPI-24."""
        import apcore_toolkit

        doc = yaml.safe_load(_PETSTORE)
        monkeypatch.setattr(apcore_toolkit, "load_spec", lambda *_a, **_k: doc)

        out = tmp_path / "out"
        result = CliRunner().invoke(
            cli,
            [
                "openapi",
                "generate",
                "https://example.invalid/openapi.json",
                "-o",
                str(out),
                "--header",
                "Authorization: Bearer SECRET-TOKEN",
            ],
        )
        assert result.exit_code == 0, result.output
        for path in out.glob("*"):
            body = path.read_text(encoding="utf-8")
            assert "SECRET-TOKEN" not in body
            assert "Authorization" not in body

    def test_security_schemes_are_not_copied(self, cli, tmp_path):
        """T-OAPI-25."""
        doc = yaml.safe_load(_PETSTORE)
        doc["components"] = {
            "securitySchemes": {
                "apiKey": {"type": "apiKey", "name": "X-Api-Key", "in": "header"},
            }
        }
        source = tmp_path / "o.json"
        source.write_text(json.dumps(doc), encoding="utf-8")
        out = tmp_path / "out"
        CliRunner().invoke(cli, ["openapi", "generate", str(source), "-o", str(out)])
        for path in out.glob("*.binding.yaml"):
            body = path.read_text(encoding="utf-8")
            assert "securitySchemes" not in body
            assert "X-Api-Key" not in body

    def test_generate_reports_the_same_hazards_as_scan(self, cli, petstore, tmp_path):
        """T-OAPI-26."""
        scan = CliRunner().invoke(cli, ["openapi", "scan", petstore], env={"COLUMNS": "200"})
        gen = CliRunner().invoke(
            cli, ["openapi", "generate", petstore, "-o", str(tmp_path / "out")], env={"COLUMNS": "200"}
        )
        assert "createPets" in scan.output
        assert "cannot be proxied by FE-15b" in gen.output
        assert "createPets" in gen.output

    def test_missing_source_exits_47(self, cli, tmp_path):
        result = CliRunner().invoke(
            cli, ["openapi", "generate", str(tmp_path / "nope.yaml"), "-o", str(tmp_path / "out")]
        )
        assert result.exit_code == 47
        assert "Cannot read OpenAPI source" in result.output

    def test_output_dir_is_required(self, cli, petstore):
        result = CliRunner().invoke(cli, ["openapi", "generate", petstore])
        assert result.exit_code == 2


class TestOpenapiGenerateFilenameCollisions:
    """openapi_cmd.py:268 — ``_output_filename(module_id)`` had no in-batch
    dedup, so two module IDs that sanitize to the same base filename (e.g.
    ``'GET /a/b'`` and ``'GET /a:b'`` both -> ``a_b``) computed the SAME
    planned path. If a file of that name already existed on disk (no
    --force), both modules' `exists()` check saw the identical path and BOTH
    were skipped — silently dropping the second module's artifact entirely.

    ``OpenAPIScanner`` itself already deduplicates module IDs it derives
    (``BaseScanner.deduplicate_ids``, appending ``_2``/``_3``...) using the
    same safe-character allowlist as this module's own sanitizer, so a real
    ``.scan()`` result cannot currently be made to produce two entries whose
    module_id collides after `_output_filename`'s sanitization — verified by
    hand before writing these tests. These tests therefore patch
    ``_scan_or_exit`` to hand back two hand-built ``ScannedModule`` instances
    with colliding-after-sanitization IDs directly, exercising the CLI's own
    defensive dedup (`_planned_output_paths`) as a hardening concern
    independent of whether today's scanner happens to prevent the input from
    arising.
    """

    @staticmethod
    def _colliding_modules():
        from apcore_toolkit.types import ScannedModule

        return [
            ScannedModule(
                module_id="a/b",
                description="first",
                input_schema={},
                output_schema={},
                tags=[],
                target="GET /x",
                metadata={"http_method": "GET", "url_path": "/x"},
            ),
            ScannedModule(
                module_id="a:b",
                description="second",
                input_schema={},
                output_schema={},
                tags=[],
                target="GET /y",
                metadata={"http_method": "GET", "url_path": "/y"},
            ),
        ]

    def test_planned_output_paths_dedups_directly(self, tmp_path):
        """Unit-level: the dedup helper itself gives distinct, ordered paths."""
        from apcore_cli.openapi_cmd import _planned_output_paths

        planned = _planned_output_paths(self._colliding_modules(), tmp_path)
        paths = [p.name for _module, p in planned]
        assert paths == ["a_b.binding.yaml", "a_b_1.binding.yaml"]

    def test_both_modules_written_to_distinct_files(self, cli, petstore, tmp_path, monkeypatch):
        import apcore_cli.openapi_cmd as openapi_cmd

        monkeypatch.setattr(openapi_cmd, "_scan_or_exit", lambda *_a, **_k: self._colliding_modules())
        out = tmp_path / "out"

        result = CliRunner().invoke(cli, ["openapi", "generate", petstore, "-o", str(out)])
        assert result.exit_code == 0, result.output
        written = sorted(p.name for p in out.glob("*.binding.yaml"))
        assert written == ["a_b.binding.yaml", "a_b_1.binding.yaml"]
        assert "2 file(s) written" in result.output

    def test_second_module_is_not_silently_dropped_when_bare_name_preexists(self, cli, petstore, tmp_path, monkeypatch):
        """The bug: BOTH modules' planned path used to be identical, so a
        single pre-existing file at that shared path caused both `exists()`
        checks to hit — the loop ran twice (once per module) and printed the
        identical "already exists" warning TWICE, and neither module was ever
        handed to the writer: ``0 file(s) written ... 2 skipped``, silently
        dropping the second module's artifact with no trace it ever existed.

        Post-fix, module 1's distinct planned path collides with the
        pre-existing file (correctly skipped, ONE warning) while module 2's
        distinct suffixed path does not, so it is handed to the writer:
        ``1 file(s) written ... 1 skipped``.

        Note (disclosed limitation, shared with the TS/Rust siblings):
        ``YAMLWriter.write()`` recomputes its OWN collision suffix fresh for
        whatever subset it is actually given, with no way to pass an explicit
        target filename — so when module 1 is the one skipped, the writer's
        single-item batch for module 2 assigns it the freed-up BARE name
        (not this command's planned ``_1`` suffix), which lands back on the
        pre-existing file and overwrites it. That is a real residual quirk
        of the upstream writer API, not something this CLI layer can fully
        resolve without a toolkit API change — asserted on here via the
        command's own success/skip counts rather than the exact on-disk
        filename, which is what this fix can actually guarantee.
        """
        import apcore_cli.openapi_cmd as openapi_cmd

        monkeypatch.setattr(openapi_cmd, "_scan_or_exit", lambda *_a, **_k: self._colliding_modules())
        out = tmp_path / "out"
        out.mkdir()
        (out / "a_b.binding.yaml").write_text("SENTINEL", encoding="utf-8")

        result = CliRunner().invoke(cli, ["openapi", "generate", petstore, "-o", str(out)])
        assert result.exit_code == 0, result.output
        # Exactly one module collided with the pre-existing file — not both.
        assert result.output.count("already exists") == 1
        # The second module must actually be written -- not silently dropped
        # with zero trace, which is what "both get skipped" produced pre-fix.
        assert "1 file(s) written" in result.output
        assert "1 skipped" in result.output


class TestOpenapiGenerateDryRunAndVerification:
    """openapi_cmd.py:394/:425 — two related bugs in ``generate``:

    (a) ``--dry-run`` listed every planned path as "would be written"
        unconditionally, never computing which paths would actually be
        SKIPPED (pre-existing file, no ``--force``) first — over-reporting.
    (b) The real write path never inspected any success/failure signal from
        the writer's results (only `getattr(result, "path", None)`), so a
        verification failure was silently counted as written and the command
        always exited 0.
    """

    def test_dry_run_does_not_list_a_preexisting_file_as_would_write(self, cli, petstore, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        (out / "listPets.binding.yaml").write_text("SENTINEL", encoding="utf-8")

        result = CliRunner().invoke(cli, ["openapi", "generate", petstore, "-o", str(out), "--dry-run"])
        assert result.exit_code == 0, result.output
        would_write_lines = [line for line in result.output.splitlines() if line.startswith("Would write")]
        assert not any("listPets.binding.yaml" in line for line in would_write_lines)
        assert any("createPets.binding.yaml" in line for line in would_write_lines)
        assert "3 file(s) would be written" in result.output
        assert "1 skipped" in result.output
        # --dry-run still writes nothing.
        assert (out / "listPets.binding.yaml").read_text(encoding="utf-8") == "SENTINEL"
        assert not (out / "createPets.binding.yaml").exists()

    def test_verification_failure_warns_and_fails_and_is_excluded_from_count(
        self, cli, petstore, tmp_path, monkeypatch
    ):
        import apcore_toolkit

        from apcore_cli.openapi_cmd import EXIT_WRITE_ERROR

        class _FakeWriter:
            def write(self, modules, output_dir, **_kwargs):
                from apcore_toolkit import WriteResult

                results = []
                for i, module in enumerate(modules):
                    path = f"{output_dir}/{module.module_id}.binding.yaml"
                    if i == 0:
                        results.append(
                            WriteResult(
                                module_id=module.module_id,
                                path=path,
                                verified=False,
                                verification_error="truncated write",
                            )
                        )
                    else:
                        results.append(WriteResult(module_id=module.module_id, path=path))
                return results

        monkeypatch.setattr(apcore_toolkit, "YAMLWriter", _FakeWriter)
        out = tmp_path / "out"

        result = CliRunner().invoke(cli, ["openapi", "generate", petstore, "-o", str(out)])
        assert result.exit_code == EXIT_WRITE_ERROR, result.output
        assert "WARNING" in result.output
        assert "truncated write" in result.output
        # 4 petstore modules total, 1 failed verification -> 3 counted as written.
        assert "3 file(s) written" in result.output
        assert "1 failed" in result.output


# ---------------------------------------------------------------------------
# Registration (§4.7)
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_registrar_needs_neither_registry_nor_executor(self, cli, petstore):
        """T-OAPI-27: neither command touches the registry."""
        group = cli.commands["openapi"]
        assert sorted(group.commands) == ["generate", "scan"]
        assert CliRunner().invoke(cli, ["openapi", "scan", petstore, "--format", "json"]).exit_code == 0

    def test_openapi_is_in_the_canonical_subcommand_set(self):
        from apcore_cli.builtin_group import APCLI_SUBCOMMAND_NAMES

        assert "openapi" in APCLI_SUBCOMMAND_NAMES
        assert "acl" in APCLI_SUBCOMMAND_NAMES
        assert len(APCLI_SUBCOMMAND_NAMES) == 15
