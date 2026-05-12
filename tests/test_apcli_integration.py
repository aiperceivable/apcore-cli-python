"""End-to-end integration tests for FE-13 (Built-in Command Group).

Covers the T-APCLI-01..41 verification matrix from
``../apcore-cli/docs/features/builtin-group.md`` §8. Tests drive the real
Click dispatch surface via ``create_cli`` + ``CliRunner`` — unit-level
behavior of :class:`ApcliGroup` lives in :mod:`tests.test_builtin_group`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import MagicMock

import click
import pytest
from click.testing import CliRunner

from apcore_cli.builtin_group import ApcliGroup
from apcore_cli.factory import create_cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Ensure env-var tests start from a known baseline."""
    monkeypatch.delenv("APCORE_CLI_APCLI", raising=False)
    monkeypatch.delenv("APCORE_CLI_LOGGING_LEVEL", raising=False)
    monkeypatch.delenv("APCORE_LOGGING_LEVEL", raising=False)


class _ModuleDef:
    """Plain-attr module descriptor — JSON-serializable for --format json tests."""

    def __init__(self, module_id: str, description: str = "", tags=None):
        self.module_id = module_id
        self.description = description
        self.tags = tags or []
        self.annotations = None
        self.input_schema = {"properties": {}, "required": []}
        self.deprecated = False
        self.enabled = True
        self.metadata: dict = {}


def _make_module_def(module_id: str, description: str = "", tags=None):
    return _ModuleDef(module_id, description, tags)


def _make_embedded_cli(*, apcli=None, module_ids=None, prog_name="test-cli"):
    """Build an embedded CLI (registry injected) for test scenarios."""
    ids = module_ids or []
    registry = MagicMock()
    registry.list.return_value = ids
    defs = {mid: _make_module_def(mid) for mid in ids}
    registry.get_definition.side_effect = lambda mid, **kw: defs.get(mid)
    executor = MagicMock()
    # Make executor.validate raise for non-system modules so the system-cmd
    # probe correctly returns False (matching an integrator who hasn't
    # registered system.* modules).
    executor.validate.side_effect = RuntimeError("system modules not available")
    return create_cli(registry=registry, executor=executor, prog_name=prog_name, apcli=apcli)


def _make_standalone_cli(tmp_path, *, apcli=None, prog_name="apcore-cli"):
    """Build a standalone CLI (filesystem discovery) rooted at ``tmp_path``."""
    return create_cli(extensions_dir=str(tmp_path), prog_name=prog_name, apcli=apcli)


def _root_commands(cli) -> list[str]:
    return sorted(cli.commands.keys())


def _visible_root_commands(cli) -> list[str]:
    return sorted(n for n, c in cli.commands.items() if not c.hidden)


# ---------------------------------------------------------------------------
# Default visibility (auto-detect)
# ---------------------------------------------------------------------------


class TestDefaultVisibility:
    def test_t_apcli_01_standalone_default_visible(self, tmp_path):
        """T-APCLI-01: standalone → apcli visible in root --help."""
        cli = _make_standalone_cli(tmp_path)
        apcli = cli.commands.get("apcli")
        assert apcli is not None
        assert apcli.hidden is False

    def test_t_apcli_02_embedded_default_hidden(self):
        """T-APCLI-02: embedded → apcli hidden by default."""
        cli = _make_embedded_cli()
        apcli = cli.commands["apcli"]
        assert apcli.hidden is True

    def test_t_apcli_03_apcli_true_wins_over_default(self):
        """T-APCLI-03: apcli=True with embedded registry → visible."""
        cli = _make_embedded_cli(apcli=True)
        assert cli.commands["apcli"].hidden is False

    def test_t_apcli_04_apcli_false_wins_over_default(self, tmp_path):
        """T-APCLI-04: apcli=False in standalone → hidden."""
        cli = _make_standalone_cli(tmp_path, apcli=False)
        assert cli.commands["apcli"].hidden is True


# ---------------------------------------------------------------------------
# Hidden-but-reachable semantics
# ---------------------------------------------------------------------------


class TestHiddenButReachable:
    def test_t_apcli_05_hidden_apcli_list_still_executes(self):
        """T-APCLI-05: hidden apcli group; `<cli> apcli list` still works."""
        cli = _make_embedded_cli(apcli=False, module_ids=["foo.bar"])
        result = CliRunner().invoke(cli, ["apcli", "list", "--flat", "--format", "json"])
        assert result.exit_code == 0, result.output
        assert "foo.bar" in result.output

    def test_t_apcli_06_hidden_apcli_help_shows_subcommands(self):
        """T-APCLI-06: `<cli> apcli --help` works under mode=none."""
        cli = _make_embedded_cli(apcli=False, module_ids=["foo.bar"])
        result = CliRunner().invoke(cli, ["apcli", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "describe" in result.output
        assert "exec" in result.output

    def test_t_apcli_18_root_help_clean(self):
        """T-APCLI-18: root --help shows no stray built-in commands."""
        cli = _make_embedded_cli()
        result = CliRunner().invoke(cli, ["--help"])
        assert result.exit_code == 0
        # Stray builtins that used to be at root should NOT appear.
        for stale in ("list", "describe", "init", "validate", "health", "usage"):
            assert stale not in result.output.split("Commands:")[-1]


# ---------------------------------------------------------------------------
# include / exclude subcommand filtering
# ---------------------------------------------------------------------------


class TestIncludeExclude:
    def test_t_apcli_07_include_list_describe_exec(self):
        """T-APCLI-07: include=[list, describe] registers list+describe+exec."""
        cli = _make_embedded_cli(
            apcli={"mode": "include", "include": ["list", "describe"]},
            module_ids=["foo.bar"],
        )
        apcli = cli.commands["apcli"]
        assert "list" in apcli.commands
        assert "describe" in apcli.commands
        assert "exec" in apcli.commands  # always-registered
        assert "init" not in apcli.commands
        assert "config" not in apcli.commands

    def test_t_apcli_08_filtered_subcommand_not_reachable(self):
        """T-APCLI-08: `apcli init` errors when `init` not in include list."""
        cli = _make_embedded_cli(
            apcli={"mode": "include", "include": ["list"]},
            module_ids=["foo.bar"],
        )
        result = CliRunner().invoke(cli, ["apcli", "init", "--help"])
        assert result.exit_code != 0
        assert "No such command" in result.output or "no such command" in result.output.lower()

    def test_t_apcli_09_exclude_init(self):
        """T-APCLI-09: exclude=[init] → all except init are reachable."""
        cli = _make_embedded_cli(
            apcli={"mode": "exclude", "exclude": ["init"]},
            module_ids=["foo.bar"],
        )
        apcli = cli.commands["apcli"]
        assert "init" not in apcli.commands
        assert "list" in apcli.commands
        assert "describe" in apcli.commands
        assert "exec" in apcli.commands

    def test_t_apcli_20_include_empty_shows_only_exec(self):
        """T-APCLI-20: mode=include + include=[] → only exec registers."""
        cli = _make_embedded_cli(
            apcli={"mode": "include", "include": []},
            module_ids=["foo.bar"],
        )
        apcli = cli.commands["apcli"]
        # Every named subcommand is filtered out — exec is the only survivor.
        assert set(apcli.commands.keys()) == {"exec"}

    def test_t_apcli_21_exclude_empty_is_like_all(self):
        """T-APCLI-21: mode=exclude + exclude=[] == mode=all."""
        cli = _make_embedded_cli(
            apcli={"mode": "exclude", "exclude": []},
            module_ids=["foo.bar"],
        )
        apcli = cli.commands["apcli"]
        # Non-system subcommands should all be there.
        for name in ("list", "describe", "exec", "validate", "init", "completion"):
            assert name in apcli.commands

    def test_t_apcli_22_invalid_mode_exits(self):
        """T-APCLI-22: mode=whitelist → exit 2."""
        with pytest.raises(SystemExit) as exc:
            _make_embedded_cli(apcli={"mode": "whitelist"})
        assert exc.value.code == 2

    def test_t_apcli_23_yaml_mode_auto_rejected(self, tmp_path, monkeypatch):
        """T-APCLI-23: yaml mode=auto → exit 2."""
        cfg = tmp_path / "apcore.yaml"
        cfg.write_text("apcli:\n  mode: auto\n")
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as exc:
            _make_standalone_cli(tmp_path)
        assert exc.value.code == 2

    def test_t_apcli_24_exec_always_registered_runs_module(self):
        """T-APCLI-24: include=[list], run `apcli exec foo.bar` — runs."""
        cli = _make_embedded_cli(
            apcli={"mode": "include", "include": ["list"]},
            module_ids=["foo.bar"],
        )
        result = CliRunner().invoke(cli, ["apcli", "exec", "foo.bar"])
        # The mock executor's `call` returns a MagicMock; exec_cmd formats
        # the result — we just assert it doesn't fail with "No such command".
        assert "No such command" not in result.output


# ---------------------------------------------------------------------------
# Tier precedence: env var, CliConfig, yaml, auto-detect
# ---------------------------------------------------------------------------


class TestTierPrecedenceIntegration:
    def test_t_apcli_10_env_overrides_yaml_hide_to_show(self, tmp_path, monkeypatch):
        """T-APCLI-10: env=show + yaml apcli=false → visible."""
        cfg = tmp_path / "apcore.yaml"
        cfg.write_text("apcli: false\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("APCORE_CLI_APCLI", "show")
        cli = _make_standalone_cli(tmp_path)
        assert cli.commands["apcli"].hidden is False

    def test_t_apcli_11_env_overrides_yaml_show_to_hide(self, tmp_path, monkeypatch):
        """T-APCLI-11: env=hide + yaml apcli=true → hidden."""
        cfg = tmp_path / "apcore.yaml"
        cfg.write_text("apcli: true\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("APCORE_CLI_APCLI", "hide")
        cli = _make_standalone_cli(tmp_path)
        assert cli.commands["apcli"].hidden is True

    def test_t_apcli_13_cli_config_beats_env(self, monkeypatch):
        """T-APCLI-13: CliConfig non-auto wins over env var."""
        monkeypatch.setenv("APCORE_CLI_APCLI", "show")
        cli = _make_embedded_cli(apcli=False)
        assert cli.commands["apcli"].hidden is True

    def test_t_apcli_14_disable_env_seals(self, monkeypatch):
        """T-APCLI-14: disable_env=true + env=show → still hidden."""
        monkeypatch.setenv("APCORE_CLI_APCLI", "show")
        cli = _make_embedded_cli(apcli={"mode": "none", "disable_env": True})
        assert cli.commands["apcli"].hidden is True

    def test_t_apcli_15_disable_env_no_effect_when_env_unset(self, monkeypatch):
        """T-APCLI-15: disable_env=true + env unset → hidden (no change)."""
        monkeypatch.delenv("APCORE_CLI_APCLI", raising=False)
        cli = _make_embedded_cli(apcli={"mode": "none", "disable_env": True})
        assert cli.commands["apcli"].hidden is True

    def test_t_apcli_37_disable_env_alone_with_auto_detect(self, tmp_path, monkeypatch):
        """T-APCLI-37: yaml disable_env only → visibility follows auto-detect."""
        cfg = tmp_path / "apcore.yaml"
        cfg.write_text("apcli:\n  disable_env: true\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("APCORE_CLI_APCLI", "show")  # should be ignored
        cli = _make_embedded_cli(apcli=None)
        # Auto-detect → embedded → hidden; env sealed.
        assert cli.commands["apcli"].hidden is True

    def test_t_apcli_38_cli_config_beats_yaml(self, tmp_path, monkeypatch):
        """T-APCLI-38: yaml apcli=false + CliConfig apcli=true → visible."""
        cfg = tmp_path / "apcore.yaml"
        cfg.write_text("apcli: false\n")
        monkeypatch.chdir(tmp_path)
        cli = _make_standalone_cli(tmp_path, apcli=True)
        assert cli.commands["apcli"].hidden is False

    def test_t_apcli_39_cli_config_include_beats_env_show(self, monkeypatch):
        """T-APCLI-39: CliConfig include=[list] + env=show → only list+exec."""
        monkeypatch.setenv("APCORE_CLI_APCLI", "show")
        cli = _make_embedded_cli(apcli={"mode": "include", "include": ["list"]})
        apcli = cli.commands["apcli"]
        assert "list" in apcli.commands
        assert "exec" in apcli.commands
        assert "init" not in apcli.commands


# ---------------------------------------------------------------------------
# Reserved-name enforcement
# ---------------------------------------------------------------------------


class TestReservedName:
    def test_t_apcli_16_business_group_apcli_rejected(self):
        """T-APCLI-16: module with display.cli.group='apcli' → exit 2."""
        registry = MagicMock()
        registry.list.return_value = ["my.mod"]
        desc = _make_module_def("my.mod")
        desc.metadata = {"display": {"cli": {"group": "apcli", "alias": "foo"}}}
        registry.get_definition.side_effect = lambda mid, **kw: desc if mid == "my.mod" else None
        executor = MagicMock()
        executor.validate.side_effect = RuntimeError("no system")
        cli = create_cli(registry=registry, executor=executor, prog_name="test-cli")
        result = CliRunner().invoke(cli, ["--help"])
        assert result.exit_code != 0
        assert "reserved" in result.output.lower()

    def test_t_apcli_17_business_top_level_apcli_rejected(self):
        """T-APCLI-17: module with top-level CLI name='apcli' → exit 2."""
        registry = MagicMock()
        registry.list.return_value = ["mymodule"]
        desc = _make_module_def("mymodule")
        desc.metadata = {"display": {"cli": {"alias": "apcli"}}}
        registry.get_definition.side_effect = lambda mid, **kw: desc if mid == "mymodule" else None
        executor = MagicMock()
        executor.validate.side_effect = RuntimeError("no system")
        cli = create_cli(registry=registry, executor=executor, prog_name="test-cli")
        result = CliRunner().invoke(cli, ["--help"])
        assert result.exit_code != 0
        assert "reserved" in result.output.lower()


# ---------------------------------------------------------------------------
# Env-var value parsing (integration-level)
# ---------------------------------------------------------------------------


class TestEnvVarParsingIntegration:
    def test_t_apcli_12_bogus_env_falls_through_to_yaml(self, tmp_path, monkeypatch):
        """T-APCLI-12: env=bogus → WARNING logged; yaml/default used."""
        cfg = tmp_path / "apcore.yaml"
        cfg.write_text("apcli: false\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("APCORE_CLI_APCLI", "bogus")
        cli = _make_standalone_cli(tmp_path)
        # yaml said false, env was garbage → yaml wins.
        assert cli.commands["apcli"].hidden is True

    def test_t_apcli_25_unknown_subcommand_warning(self, caplog):
        """T-APCLI-25: unknown subcommand in include list → WARNING."""
        import logging as pylogging

        with caplog.at_level(pylogging.WARNING, logger="apcore_cli.builtin_group"):
            _make_embedded_cli(apcli={"mode": "include", "include": ["list", "unknown_sub"]})
        assert "Unknown apcli subcommand 'unknown_sub'" in caplog.text

    def test_t_apcli_26_non_bool_disable_env_warning(self, tmp_path, caplog, monkeypatch):
        """T-APCLI-26: non-bool disable_env in yaml → WARNING, treated as false."""
        import logging as pylogging

        cfg = tmp_path / "apcore.yaml"
        cfg.write_text("apcli:\n  mode: none\n  disable_env: 'yes'\n")
        monkeypatch.chdir(tmp_path)
        with caplog.at_level(pylogging.WARNING, logger="apcore_cli.builtin_group"):
            _make_standalone_cli(tmp_path)
        assert "disable_env must be boolean" in caplog.text


# ---------------------------------------------------------------------------
# Discovery-flag gating (standalone only)
# ---------------------------------------------------------------------------


class TestDiscoveryFlagsGating:
    def test_t_apcli_27_embedded_extensions_dir_rejected(self):
        """T-APCLI-27: embedded + --extensions-dir → exit 2."""
        cli = _make_embedded_cli()
        result = CliRunner().invoke(cli, ["--extensions-dir", "foo"])
        assert result.exit_code != 0
        assert "No such option" in result.output or "no such option" in result.output.lower()

    def test_t_apcli_28_standalone_extensions_dir_accepted(self, tmp_path):
        """T-APCLI-28: standalone + --extensions-dir → accepted."""
        cli = _make_standalone_cli(tmp_path)
        assert "extensions_dir_opt" in [p.name for p in cli.params]


# ---------------------------------------------------------------------------
# Programmatic ApcliGroup instance + equivalence
# ---------------------------------------------------------------------------


class TestProgrammaticApcliGroup:
    def test_t_apcli_33_accepts_apcli_group_instance(self):
        """T-APCLI-33: create_cli(apcli=ApcliGroup(...)) accepted."""
        g = ApcliGroup.from_cli_config({"mode": "none"}, registry_injected=True)
        cli = _make_embedded_cli(apcli=g)
        assert cli.commands["apcli"].hidden is True

    def test_t_apcli_36_three_forms_produce_identical_help(self):
        """T-APCLI-36: apcli=False == apcli={'mode':'none'} == ApcliGroup(mode='none')."""
        runner = CliRunner()
        cli_a = _make_embedded_cli(apcli=False)
        cli_b = _make_embedded_cli(apcli={"mode": "none"})
        cli_c = _make_embedded_cli(apcli=ApcliGroup.from_cli_config({"mode": "none"}, registry_injected=True))
        out_a = runner.invoke(cli_a, ["--help"]).output
        out_b = runner.invoke(cli_b, ["--help"]).output
        out_c = runner.invoke(cli_c, ["--help"]).output
        assert out_a == out_b == out_c


# ---------------------------------------------------------------------------
# Total lockdown (spec §4.11 last row)
# ---------------------------------------------------------------------------


class TestTotalLockdown:
    def test_t_apcli_41_total_lockdown_with_env_sealed(self, monkeypatch):
        """T-APCLI-41: {mode=include, include=[], disable_env=True} + env=show.

        Only ``exec`` reachable; env var sealed; `apcli list` → No such command.
        """
        monkeypatch.setenv("APCORE_CLI_APCLI", "show")
        cli = _make_embedded_cli(
            apcli={"mode": "include", "include": [], "disable_env": True},
            module_ids=["foo.bar"],
        )
        apcli = cli.commands["apcli"]
        assert set(apcli.commands.keys()) == {"exec"}
        # Group is visible per spec §4.7 note (mode=include != none even when
        # include is empty — is_group_visible() returns True).
        assert apcli.hidden is False

        result_list = CliRunner().invoke(cli, ["apcli", "list"])
        assert result_list.exit_code != 0
        assert "No such command" in result_list.output

        # But exec remains reachable (FE-12 guarantee).
        result_exec = CliRunner().invoke(cli, ["apcli", "exec", "foo.bar"])
        assert "No such command" not in result_exec.output


# ---------------------------------------------------------------------------
# Verbose flag orthogonality & env unset path
# ---------------------------------------------------------------------------


class TestVerboseOrthogonality:
    def test_t_apcli_34_all_options_does_not_unhide_apcli(self):
        """T-APCLI-34: --all-options --help with apcli=False keeps apcli hidden."""
        cli = _make_embedded_cli(apcli=False)
        result = CliRunner().invoke(cli, ["--all-options", "--help"])
        assert result.exit_code == 0
        # `apcli` should not appear in the Commands section when hidden.
        commands_section = result.output.split("Commands:")[-1] if "Commands:" in result.output else ""
        assert "apcli" not in commands_section


class TestEnvUnset:
    def test_t_apcli_35_env_unset_works_with_disable_env_set(self, monkeypatch):
        """T-APCLI-35: env -u APCORE_CLI_APCLI works regardless of disable_env."""
        monkeypatch.delenv("APCORE_CLI_APCLI", raising=False)
        cli = _make_embedded_cli(apcli={"mode": "none", "disable_env": True})
        assert cli.commands["apcli"].hidden is True


# ---------------------------------------------------------------------------
# FE-13 §11.2: deprecation shims removed in v0.8 (D9-001)
# ---------------------------------------------------------------------------


class TestDeprecationShimsRemoved:
    """Root-level legacy command names no longer exist as of v0.8."""

    def test_root_legacy_command_unknown_in_standalone(self, tmp_path):
        """`apcore-cli list` (without the apcli group) must error in v0.8."""
        env = os.environ.copy()
        env.pop("APCORE_CLI_APCLI", None)
        argv = [
            sys.executable,
            "-m",
            "apcore_cli",
            "--extensions-dir",
            str(tmp_path),
            "list",
            "--flat",
            "--format",
            "json",
        ]
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        assert result.returncode != 0
        assert "no such command" in result.stderr.lower() or "usage:" in result.stderr.lower()

    def test_legacy_names_absent_in_embedded_mode(self):
        """Embedded mode never had shims, and v0.8 standalone matches that."""
        cli = _make_embedded_cli()
        assert "list" not in cli.commands
        assert "describe" not in cli.commands


# ---------------------------------------------------------------------------
# extra_commands interaction with apcli reserved name
# ---------------------------------------------------------------------------


class TestExtraCommandsReservedName:
    def test_extra_command_named_apcli_rejected(self, tmp_path):
        """FE-13: extra_commands='apcli' → ValueError."""

        @click.command("apcli")
        def rogue():
            pass

        with pytest.raises(ValueError, match="reserved"):
            create_cli(
                extensions_dir=str(tmp_path),
                prog_name="apcore-cli",
                extra_commands=[rogue],
            )

    def test_extra_command_named_list_installs_at_root(self, tmp_path):
        """v0.8: with shims removed, an extra command named 'list' installs at
        root without colliding with anything."""

        @click.command("list")
        def user_list():
            click.echo("user-list-output")

        cli = create_cli(
            extensions_dir=str(tmp_path),
            prog_name="apcore-cli",
            extra_commands=[user_list],
        )
        assert "list" in cli.commands
        result = CliRunner().invoke(cli, ["list"])
        assert result.exit_code == 0
        assert "user-list-output" in result.output
