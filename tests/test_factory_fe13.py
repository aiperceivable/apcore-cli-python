"""Coverage-focused tests for FE-13 factory edges not hit by other suites.

Covers:
  * ``create_cli(apcli=...)`` TypeError branch (non-bool/dict/None/ApcliGroup)
  * Pre-built :class:`ApcliGroup` instance passthrough
  * Filesystem-discovery error paths (missing / unreadable extensions dir)
  * ``_register_apcli_subcommands`` missing-executor behavior for the
    always-registered ``exec`` subcommand (spec §4.9 WARN, not silent drop)
  * Deprecation shim runtime forwarding (standalone mode)
  * ``extra_commands`` shim-override runtime invocation
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from unittest.mock import MagicMock

import click
import pytest

from apcore_cli.builtin_group import ApcliGroup
from apcore_cli.factory import create_cli

EXIT_CONFIG_NOT_FOUND = 47


# ---------------------------------------------------------------------------
# apcli kwarg edge cases
# ---------------------------------------------------------------------------


class TestApcliKwargEdges:
    def test_invalid_apcli_type_exits_2(self, capsys):
        """create_cli(apcli='yes') → TypeError wrapped as exit 2."""
        with pytest.raises(SystemExit) as exc:
            create_cli(registry=MagicMock(), executor=MagicMock(), apcli="yes")  # type: ignore[arg-type]
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "expected bool, dict, ApcliGroup, or None" in err

    def test_apcli_group_instance_passthrough(self):
        """ApcliGroup instance is used as-is (Tier 1 carried in flag)."""
        g = ApcliGroup.from_cli_config({"mode": "none"}, registry_injected=True)
        cli = create_cli(registry=MagicMock(), executor=MagicMock(), apcli=g)
        assert cli.commands["apcli"].hidden is True


# ---------------------------------------------------------------------------
# Filesystem-discovery error paths (standalone mode)
# ---------------------------------------------------------------------------


class TestExtensionsDirErrors:
    def test_missing_extensions_dir_exits_47(self, tmp_path, capsys):
        missing = tmp_path / "does-not-exist"
        with pytest.raises(SystemExit) as exc:
            create_cli(extensions_dir=str(missing), prog_name="apcore-cli")
        assert exc.value.code == EXIT_CONFIG_NOT_FOUND
        err = capsys.readouterr().err
        assert "not found" in err

    def test_unreadable_extensions_dir_exits_47(self, tmp_path, capsys, monkeypatch):
        unreadable = tmp_path / "locked"
        unreadable.mkdir()
        # Make os.access report False for R_OK to trigger the unreadable branch
        # without actually touching permissions on the real dir.
        real_access = os.access

        def fake_access(path, mode):
            if str(path) == str(unreadable):
                return False
            return real_access(path, mode)

        monkeypatch.setattr("apcore_cli.factory.os.access", fake_access)
        with pytest.raises(SystemExit) as exc:
            create_cli(extensions_dir=str(unreadable), prog_name="apcore-cli")
        assert exc.value.code == EXIT_CONFIG_NOT_FOUND
        err = capsys.readouterr().err
        assert "Cannot read" in err


# ---------------------------------------------------------------------------
# _register_apcli_subcommands edges
# ---------------------------------------------------------------------------


class TestRegisterApcliSubcommandsEdges:
    def test_always_registered_exec_warns_without_executor(self, caplog):
        """Spec §4.9: exec is _ALWAYS_REGISTERED; missing executor → WARN."""
        # We need a real-ish call: standalone mode with no executor wired via
        # the public API is not reachable (factory always builds one). Drive
        # the dispatcher directly instead.
        from apcore_cli.builtin_group import ApcliGroup
        from apcore_cli.exposure import ExposureFilter
        from apcore_cli.factory import _register_apcli_subcommands

        apcli_group = click.Group("apcli")
        cfg = ApcliGroup.from_cli_config(True, registry_injected=True)
        registry = MagicMock()
        registry.list.return_value = []
        with caplog.at_level(logging.WARNING, logger="apcore_cli"):
            _register_apcli_subcommands(
                apcli_group,
                cfg,
                registry=registry,
                executor=None,  # trigger the WARN path
                exposure_filter=ExposureFilter(),
                prog_name="test-cli",
            )
        assert "exec" in caplog.text
        assert "no executor is wired" in caplog.text
        # exec itself is NOT registered when executor is None (WARN, not silent
        # drop — but the subcommand cannot be built without the executor).
        assert "exec" not in apcli_group.commands


# ---------------------------------------------------------------------------
# FE-13 §11.2: deprecation shims removed in v0.8 (D9-001)
# ---------------------------------------------------------------------------


class TestDeprecationShimsRemoved:
    """v0.8 retired the 13 root-level shims. ``apcli`` is now the only path."""

    def test_root_legacy_command_is_unknown(self, tmp_path):
        """Root ``apcore-cli list`` must error: shims were removed in v0.8."""
        env = os.environ.copy()
        env.pop("APCORE_CLI_APCLI", None)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "apcore_cli",
                "--extensions-dir",
                str(tmp_path),
                "list",
                "--flat",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        # Click exits non-zero with "No such command" when the shim is gone.
        assert result.returncode != 0
        assert "no such command" in result.stderr.lower() or "usage:" in result.stderr.lower()

    def test_apcli_list_still_works(self, tmp_path):
        """The canonical ``apcli list`` path remains available."""
        env = os.environ.copy()
        env.pop("APCORE_CLI_APCLI", None)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "apcore_cli",
                "--extensions-dir",
                str(tmp_path),
                "apcli",
                "list",
                "--flat",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() in ("[]", "")


# ---------------------------------------------------------------------------
# extra_commands collision behavior (no shim layer)
# ---------------------------------------------------------------------------


class TestExtraCommandsRuntime:
    def test_extra_command_named_list_no_longer_collides_with_shim(self, tmp_path):
        """Without shims at the root, an extra command named 'list' is freely
        installed at root — it does not collide with anything."""
        from click.testing import CliRunner

        @click.command("list")
        def user_list() -> None:
            click.echo("user-list-was-called")

        cli = create_cli(
            extensions_dir=str(tmp_path),
            prog_name="apcore-cli",
            extra_commands=[user_list],
        )
        result = CliRunner().invoke(cli, ["list"])
        assert result.exit_code == 0
        assert "user-list-was-called" in result.output

    def test_extra_command_collides_with_apcli_group(self, tmp_path):
        """Collision with the reserved `apcli` group still raises."""

        @click.command("apcli")
        def rogue() -> None:
            pass

        with pytest.raises(ValueError, match="reserved"):
            create_cli(
                extensions_dir=str(tmp_path),
                prog_name="apcore-cli",
                extra_commands=[rogue],
            )


class TestApprovalHandlerWiringFailure:
    """W2: approval-handler wiring failure must surface at WARNING, not DEBUG."""

    def test_wiring_failure_logs_warning(self, tmp_path, caplog):
        """When executor.set_approval_handler raises, the message reaches the
        default WARNING log level — otherwise operators never see that
        requires_approval modules will silently bypass the gate."""
        from unittest.mock import MagicMock

        # Injected executor whose set_approval_handler raises — simulates an
        # apcore version whose signature drifted or whose handler hook is broken.
        registry = MagicMock()
        registry.list.return_value = []
        executor = MagicMock()
        executor.set_approval_handler.side_effect = RuntimeError("handler hook broken")

        with caplog.at_level(logging.WARNING, logger="apcore_cli.factory"):
            create_cli(
                extensions_dir=str(tmp_path),
                prog_name="apcore-cli",
                registry=registry,
                executor=executor,
            )
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "Failed to wire CliApprovalHandler" in text
        assert "handler hook broken" in text
