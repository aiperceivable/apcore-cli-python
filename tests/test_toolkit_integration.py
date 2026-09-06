"""Tests for :func:`apcore_cli.factory._apply_toolkit_integration` (SDK parity).

Covers the three fixes that brought the Python CLI up to
``../apcore-cli-typescript/src/main.ts::loadBindingDisplayOverlay``:

1. ``BindingLoader`` pipeline is invoked when ``--binding`` is set (with
   or without ``--commands-dir``) — previously a no-op standalone.
2. ``allowed_prefixes`` is forwarded to ``RegistryWriter.write``.
3. ``--annotation paginated`` (apcore >= 0.19.0) is a valid filter choice.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from apcore_cli.factory import _apply_toolkit_integration


@pytest.fixture(autouse=True)
def _silence_env(monkeypatch):
    monkeypatch.delenv("APCORE_CLI_APCLI", raising=False)
    monkeypatch.delenv("APCORE_CLI_LOGGING_LEVEL", raising=False)


# ---------------------------------------------------------------------------
# _apply_toolkit_integration — behaviour matrix
# ---------------------------------------------------------------------------


class TestApplyToolkitIntegration:
    def test_noop_when_neither_path_given(self):
        registry = MagicMock()
        _apply_toolkit_integration(
            registry,
            commands_dir=None,
            binding_path=None,
            allowed_prefixes=None,
        )
        registry.register.assert_not_called()

    def test_binding_path_alone_uses_binding_loader(self):
        """Standalone --binding: BindingLoader.load + RegistryWriter.write.

        This path was a no-op before Fix 1 — the TS CLI's equivalent in
        ``main.ts:loadBindingDisplayOverlay`` has always done this.
        """
        fake_module_a = MagicMock()
        fake_module_a.module_id = "foo.bar"
        fake_loader = MagicMock()
        fake_loader.load.return_value = [fake_module_a]
        fake_loader_cls = MagicMock(return_value=fake_loader)

        fake_resolver = MagicMock()
        fake_resolver.resolve.side_effect = lambda mods, **kw: mods
        fake_resolver_cls = MagicMock(return_value=fake_resolver)

        fake_writer = MagicMock()
        fake_writer.write.return_value = []
        fake_writer_cls = MagicMock(return_value=fake_writer)

        with (
            patch("apcore_cli.factory.BindingLoader", fake_loader_cls),
            patch("apcore_cli.factory.DisplayResolver", fake_resolver_cls),
            patch("apcore_cli.factory.RegistryWriter", fake_writer_cls),
        ):
            registry = MagicMock()
            _apply_toolkit_integration(
                registry,
                commands_dir=None,
                binding_path="/fake/bindings/",
                allowed_prefixes=None,
            )

        fake_loader.load.assert_called_once_with("/fake/bindings/")
        fake_resolver.resolve.assert_called_once()
        fake_writer.write.assert_called_once()
        # Second positional arg is the registry; allowed_prefixes defaults to None.
        call_args = fake_writer.write.call_args
        assert call_args.args[0] == [fake_module_a]
        assert call_args.kwargs["allowed_prefixes"] is None

    def test_allowed_prefixes_forwarded_to_writer(self):
        """Fix 2: --allowed-prefix values land on RegistryWriter.write()."""
        fake_module = MagicMock()
        fake_module.module_id = "ok.mod"
        fake_loader = MagicMock()
        fake_loader.load.return_value = [fake_module]

        fake_writer = MagicMock()
        fake_writer.write.return_value = []

        with (
            patch("apcore_cli.factory.BindingLoader", MagicMock(return_value=fake_loader)),
            patch("apcore_cli.factory.DisplayResolver", MagicMock(return_value=MagicMock(resolve=lambda m, **kw: m))),
            patch("apcore_cli.factory.RegistryWriter", MagicMock(return_value=fake_writer)),
        ):
            _apply_toolkit_integration(
                MagicMock(),
                commands_dir=None,
                binding_path="/bindings",
                allowed_prefixes=["myapp", "mylib.plugins"],
            )

        assert fake_writer.write.call_args.kwargs["allowed_prefixes"] == ["myapp", "mylib.plugins"]

    def test_commands_dir_and_binding_path_merge(self):
        """Both paths contribute modules to a single RegistryWriter.write call."""
        scan_module = MagicMock(module_id="scanned.a")
        bind_module = MagicMock(module_id="bound.b")

        fake_scanner = MagicMock()
        fake_scanner.scan.return_value = [scan_module]
        fake_scanner_cls = MagicMock(return_value=fake_scanner)

        fake_loader = MagicMock()
        fake_loader.load.return_value = [bind_module]

        fake_writer = MagicMock()
        fake_writer.write.return_value = []

        with (
            patch("apcore_cli.factory.ConventionScanner", fake_scanner_cls),
            patch("apcore_cli.factory.BindingLoader", MagicMock(return_value=fake_loader)),
            patch("apcore_cli.factory.DisplayResolver", MagicMock(return_value=MagicMock(resolve=lambda m, **kw: m))),
            patch("apcore_cli.factory.RegistryWriter", MagicMock(return_value=fake_writer)),
        ):
            _apply_toolkit_integration(
                MagicMock(),
                commands_dir="/cmds",
                binding_path="/bindings",
                allowed_prefixes=None,
            )

        # Both modules should reach the writer in a single call.
        written = fake_writer.write.call_args.args[0]
        assert scan_module in written
        assert bind_module in written

    # Removed in 0.10.0 (resolves 6.2): the prior graceful-fallback paths
    # for "apcore-toolkit missing" and "apcore-toolkit < 0.5.0 (no
    # BindingLoader)" no longer exist — apcore-toolkit >= 0.7.0 is a hard
    # runtime dependency declared in pyproject.toml, so a missing or
    # too-old toolkit is a ModuleNotFoundError at import time, not a
    # silently logged WARNING. The two former tests
    # (`test_toolkit_missing_logs_warning_and_returns` and
    # `test_binding_loader_missing_warns_but_continues`) are intentionally
    # deleted — they asserted behaviour that the fix removed. The
    # import-time failure mode is already covered by Python's standard
    # ModuleNotFoundError contract and does not need a bespoke test.

    def test_binding_loader_failure_is_soft(self, caplog):
        """BindingLoader.load() raises → WARN, don't crash create_cli."""
        import logging as pylogging

        class BoomError(Exception):
            pass

        fake_loader = MagicMock()
        fake_loader.load.side_effect = BoomError("malformed yaml")

        with (
            caplog.at_level(pylogging.WARNING, logger="apcore_cli"),
            patch("apcore_cli.factory.BindingLoader", MagicMock(return_value=fake_loader)),
            patch("apcore_cli.factory.DisplayResolver", MagicMock()),
            patch("apcore_cli.factory.RegistryWriter", MagicMock()),
        ):
            _apply_toolkit_integration(
                MagicMock(),
                commands_dir=None,
                binding_path="/bindings",
                allowed_prefixes=None,
            )
        assert "BindingLoader failed" in caplog.text

    def test_no_modules_skips_writer(self):
        """When neither source produced modules, RegistryWriter is not invoked."""
        fake_loader = MagicMock()
        fake_loader.load.return_value = []

        fake_writer = MagicMock()

        with (
            patch("apcore_cli.factory.BindingLoader", MagicMock(return_value=fake_loader)),
            patch("apcore_cli.factory.DisplayResolver", MagicMock()),
            patch("apcore_cli.factory.RegistryWriter", MagicMock(return_value=fake_writer)),
        ):
            _apply_toolkit_integration(
                MagicMock(),
                commands_dir=None,
                binding_path="/empty",
                allowed_prefixes=None,
            )
        fake_writer.write.assert_not_called()


# ---------------------------------------------------------------------------
# --allowed-prefix CLI flag (standalone-only gating)
# ---------------------------------------------------------------------------


class TestAllowedPrefixFlag:
    def test_flag_registered_in_standalone(self, tmp_path):
        from apcore_cli.factory import create_cli

        cli = create_cli(extensions_dir=str(tmp_path), prog_name="apcore-cli")
        assert "allowed_prefix_opt" in [p.name for p in cli.params]

    def test_flag_absent_in_embedded(self):
        from apcore_cli.factory import create_cli

        cli = create_cli(registry=MagicMock(), executor=MagicMock(), prog_name="branded")
        assert "allowed_prefix_opt" not in [p.name for p in cli.params]


# ---------------------------------------------------------------------------
# --annotation paginated (apcore 0.19.0 field)
# ---------------------------------------------------------------------------


class TestPaginatedAnnotationFilter:
    def test_paginated_is_a_valid_choice(self):
        import click
        from click.testing import CliRunner

        from apcore_cli.discovery import register_list_command

        registry = MagicMock()

        class Ann:
            def __init__(self, paginated=False):
                self.paginated = paginated

        class Mod:
            def __init__(self, module_id: str, paginated: bool):
                self.module_id = module_id
                self.canonical_id = module_id
                self.description = ""
                self.tags: list = []
                self.annotations = Ann(paginated=paginated)
                self.input_schema = {"properties": {}, "required": []}
                self.deprecated = False
                self.enabled = True
                self.metadata: dict = {}

        mods = [Mod("a", True), Mod("b", False)]
        registry.list.return_value = [m.module_id for m in mods]
        defs = {m.module_id: m for m in mods}
        registry.get_definition.side_effect = lambda mid, **kw: defs.get(mid)

        @click.group()
        def cli() -> None:
            pass

        register_list_command(cli, registry)
        result = CliRunner().invoke(cli, ["list", "--flat", "--format", "json", "-a", "paginated"])
        assert result.exit_code == 0, result.output
        import json

        ids = [d["id"] for d in json.loads(result.output)]
        assert ids == ["a"]


# ---------------------------------------------------------------------------
# End-to-end: --binding alone actually registers modules into the registry
# ---------------------------------------------------------------------------


class TestBindingPathStandaloneE2E:
    def test_binding_only_writes_to_registry(self, tmp_path):
        """End-to-end: create_cli + `--binding path` with NO --commands-dir
        invokes BindingLoader and writes resolved modules to the registry.
        Pre-fix #1 this path was a silent no-op."""
        from apcore_cli.factory import create_cli

        # Patch at the toolkit symbols the factory helper imports.
        scanned = MagicMock(module_id="e2e.mod")
        fake_loader = MagicMock()
        fake_loader.load.return_value = [scanned]

        fake_writer = MagicMock()
        fake_writer.write.return_value = []

        with (
            patch("apcore_cli.factory.BindingLoader", MagicMock(return_value=fake_loader)),
            patch("apcore_cli.factory.DisplayResolver", MagicMock(return_value=MagicMock(resolve=lambda m, **kw: m))),
            patch("apcore_cli.factory.RegistryWriter", MagicMock(return_value=fake_writer)),
        ):
            cli = create_cli(
                extensions_dir=str(tmp_path),
                prog_name="apcore-cli",
                binding_path=str(tmp_path / "bindings"),
                allowed_prefixes=["sandbox"],
            )

        assert cli is not None
        fake_loader.load.assert_called_once()
        # allowed_prefixes propagated end-to-end.
        assert fake_writer.write.call_args.kwargs["allowed_prefixes"] == ["sandbox"]


class TestCommandsDirReadmeExampleE2E:
    """README.md's "Zero-import way" (`--commands-dir`) example, exercised
    end-to-end with the REAL apcore-toolkit classes — no mocking.

    apcore-toolkit's ``ConventionScanner`` sets ``ScannedModule.target`` to a
    raw file path (``"commands/deploy.py:deploy"``) rather than a dotted
    module path. ``RegistryWriter._to_function_module`` resolves ``target``
    via ``resolve_target()``, which expects ``'module.path:qualname'`` and
    calls ``importlib.import_module(module_path)`` — raising
    ``ModuleNotFoundError`` for a path containing ``/`` and ``.py``.
    ``RegistryWriter.write()`` catches per-module write failures and logs a
    WARNING rather than crashing, so the net effect is silent: `deploy.deploy`
    is simply never registered, and running the README's own example --
    `apcore-cli --commands-dir commands/ deploy deploy --env prod` -- fails
    with Click's "No such command 'deploy'" (exit 2).

    Confirmed to reproduce against the pinned apcore-toolkit (0.11.1), both
    via the real ``apcore-cli`` console script in a scratch directory and via
    this in-process ``CliRunner`` invocation. The root cause lives entirely
    in apcore-toolkit's ``ConventionScanner`` / ``resolve_target`` — it is not
    fixable from this repo. Marked ``xfail(strict=True)`` so this is caught
    automatically the moment apcore-toolkit fixes it (a strict xfail that
    starts passing fails the suite, prompting someone to un-xfail it).
    """

    @pytest.mark.xfail(
        reason=(
            "apcore-toolkit's ConventionScanner sets ScannedModule.target to a "
            "raw file path ('commands/deploy.py:deploy') instead of a dotted "
            "module path; RegistryWriter._to_function_module's resolve_target() "
            "call then raises ModuleNotFoundError, which RegistryWriter.write() "
            "logs as a WARNING and swallows -- silently never registering the "
            "convention-scanned command. Tracked upstream in apcore-toolkit; "
            "not fixable from apcore-cli-python. See tests/test_toolkit_"
            "integration.py::TestCommandsDirReadmeExampleE2E."
        ),
        strict=True,
    )
    def test_commands_dir_convention_module_is_invocable(self, tmp_path):
        from click.testing import CliRunner

        from apcore_cli.factory import create_cli

        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        (commands_dir / "deploy.py").write_text(
            'def deploy(env: str, tag: str = "latest") -> dict:\n'
            '    """Deploy the app to the given environment."""\n'
            '    return {"status": "deployed", "env": env}\n',
            encoding="utf-8",
        )
        extensions_dir = tmp_path / "extensions"
        extensions_dir.mkdir()

        cli = create_cli(extensions_dir=str(extensions_dir), commands_dir=str(commands_dir))
        result = CliRunner().invoke(cli, ["deploy", "--env", "prod"])

        assert result.exit_code == 0, result.output
        assert "deployed" in result.output
