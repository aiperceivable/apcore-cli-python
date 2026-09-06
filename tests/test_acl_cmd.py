"""FE-14 ``apcli acl`` subcommand group.

Covers T-ACL-09..21 (list / check / validate / status) plus the end-to-end
denial rows T-ACL-22 and T-ACL-23, which exercise the previously-inert
``acl_check`` pipeline step through a real apcore Registry + Executor.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import click
import pytest
from apcore import ACL, Executor, Registry, module
from click.testing import CliRunner

from apcore_cli.acl_cmd import register_acl_command
from apcore_cli.acl_loader import build_identity, set_cli_identity
from apcore_cli.factory import create_cli


def _leading_json(output: str) -> dict:
    """Parse the JSON object at the head of *output*.

    ``CliRunner`` interleaves stderr, so a denial's ``Access denied:`` line
    trails the JSON payload on the same stream.
    """
    return json.JSONDecoder().raw_decode(output.lstrip())[0]


def _build_cli(executor=None, acl=None, source=None) -> click.Group:
    @click.group()
    def apcli() -> None:
        pass

    register_acl_command(apcli, executor, acl, source)
    return apcli


def _acl_from(tmp_path, body: str, name: str = "global_acl.yaml") -> tuple[ACL, str]:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return ACL.load(str(path)), str(path)


_THREE_RULES = """\
default_effect: deny
rules:
  - callers: ["@external"]
    targets: ["system.control.*"]
    effect: deny
    description: "no external control"
  - callers: ["*"]
    targets: ["db.migrate"]
    effect: allow
    approval: required
    description: "migrations need a human"
  - callers: ["*"]
    targets: ["db.read"]
    effect: allow
    conditions:
      roles: ["admin"]
    description: "admins may read"
"""


# ---------------------------------------------------------------------------
# apcli acl list (§4.4)
# ---------------------------------------------------------------------------


class TestAclList:
    def test_json_preserves_definition_order(self, tmp_path):
        """T-ACL-09: three rules, indices 0..2, definition order intact."""
        acl, source = _acl_from(tmp_path, _THREE_RULES)
        result = CliRunner().invoke(_build_cli(None, acl, source), ["acl", "list", "--format", "json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["source"] == source
        assert payload["default_effect"] == "deny"
        assert [r["index"] for r in payload["rules"]] == [0, 1, 2]
        assert payload["rules"][0]["targets"] == ["system.control.*"]
        assert payload["rules"][1]["approval"] == "required"
        assert payload["rules"][2]["conditions"] == {"roles": ["admin"]}

    def test_no_acl_json_shape(self):
        """T-ACL-10: listing nothing is not an error."""
        result = CliRunner().invoke(_build_cli(None, None, None), ["acl", "list", "--format", "json"])
        assert result.exit_code == 0
        assert json.loads(result.output) == {"source": None, "default_effect": None, "rules": []}

    def test_no_acl_table(self):
        result = CliRunner().invoke(_build_cli(None, None, None), ["acl", "list"])
        assert result.exit_code == 0
        assert "No ACL configured." in result.output

    def test_table_shows_default_effect_and_source(self, tmp_path):
        acl, source = _acl_from(tmp_path, _THREE_RULES)
        result = CliRunner().invoke(_build_cli(None, acl, source), ["acl", "list"])
        assert result.exit_code == 0
        assert "Default effect: deny" in result.output
        assert "3 rules" in result.output

    def test_table_conditions_column_lists_keys_only(self, tmp_path):
        """The Conditions column carries keys, never bodies."""
        body = """\
default_effect: deny
rules:
  - callers: ["*"]
    targets: ["a"]
    effect: allow
    conditions:
      roles: ["admin"]
      max_call_depth: 3
"""
        acl, source = _acl_from(tmp_path, body)
        # Widen the terminal so Rich does not wrap the Conditions column.
        result = CliRunner().invoke(_build_cli(None, acl, source), ["acl", "list"], env={"COLUMNS": "200"})
        assert "max_call_depth, roles" in result.output
        # No condition *body* leaks into the table — full bodies stay in JSON.
        assert "admin" not in result.output

    def test_yaml_and_jsonl_formats(self, tmp_path):
        acl, source = _acl_from(tmp_path, _THREE_RULES)
        runner = CliRunner()
        cli = _build_cli(None, acl, source)
        assert runner.invoke(cli, ["acl", "list", "--format", "yaml"]).exit_code == 0
        jsonl = runner.invoke(cli, ["acl", "list", "--format", "jsonl"])
        assert jsonl.exit_code == 0
        assert len(jsonl.output.strip().splitlines()) == 3
        assert runner.invoke(cli, ["acl", "list", "--format", "csv"]).exit_code == 0


# ---------------------------------------------------------------------------
# apcli acl check (§4.5)
# ---------------------------------------------------------------------------


class TestAclCheck:
    def test_allow_exits_0_and_reports_matched_rule(self, tmp_path):
        """T-ACL-11."""
        body = """\
default_effect: deny
rules:
  - callers: ["*"]
    targets: ["db.read"]
    effect: allow
    description: "reads are fine"
"""
        acl, source = _acl_from(tmp_path, body)
        result = CliRunner().invoke(_build_cli(None, acl, source), ["acl", "check", "db.read", "--format", "json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["access"] == "allow"
        assert payload["matched_rule_index"] == 0
        assert payload["caller"] == "@external"

    def test_deny_exits_77(self, tmp_path):
        """T-ACL-12."""
        acl, source = _acl_from(tmp_path, _THREE_RULES)
        result = CliRunner().invoke(_build_cli(None, acl, source), ["acl", "check", "system.control.disable"])
        assert result.exit_code == 77
        assert "Access denied: @external -> system.control.disable" in result.output

    def test_allow_with_approval_exits_0(self, tmp_path):
        """T-ACL-13: authorization and approval are independent axes."""
        acl, source = _acl_from(tmp_path, _THREE_RULES)
        result = CliRunner().invoke(_build_cli(None, acl, source), ["acl", "check", "db.migrate", "--format", "json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["access"] == "allow"
        assert payload["approval_required"] is True

    def test_allow_with_approval_table_shows_both_axes(self, tmp_path):
        acl, source = _acl_from(tmp_path, _THREE_RULES)
        result = CliRunner().invoke(_build_cli(None, acl, source), ["acl", "check", "db.migrate"])
        assert "Decision: ALLOW" in result.output
        assert "Approval: REQUIRED" in result.output
        assert 'rule #1: "migrations need a human"' in result.output

    def test_role_condition_satisfied(self, tmp_path):
        """T-ACL-14."""
        acl, source = _acl_from(tmp_path, _THREE_RULES)
        result = CliRunner().invoke(
            _build_cli(None, acl, source),
            ["acl", "check", "db.read", "--role", "admin", "--format", "json"],
        )
        assert result.exit_code == 0
        assert json.loads(result.output)["access"] == "allow"

    def test_role_condition_unmatched_falls_through_to_default(self, tmp_path):
        """T-ACL-15: no context -> the rule does not match; default_effect wins."""
        acl, source = _acl_from(tmp_path, _THREE_RULES)
        result = CliRunner().invoke(_build_cli(None, acl, source), ["acl", "check", "db.read", "--format", "json"])
        assert result.exit_code == 77
        payload = _leading_json(result.output)
        assert payload["access"] == "deny"
        assert payload["reason"] == "default_effect"
        assert payload["matched_rule_index"] is None

    def test_arguments_condition_has_key(self, tmp_path):
        """T-ACL-16: --input feeds the §6.1.8 governance projection."""
        body = """\
default_effect: deny
rules:
  - callers: ["*"]
    targets: ["db.migrate"]
    effect: allow
    conditions:
      arguments:
        has_key: ["force"]
"""
        acl, source = _acl_from(tmp_path, body)
        cli = _build_cli(None, acl, source)
        ok = CliRunner().invoke(cli, ["acl", "check", "db.migrate", "--input", '{"force": true}', "--format", "json"])
        assert ok.exit_code == 0
        assert json.loads(ok.output)["access"] == "allow"

        missing = CliRunner().invoke(cli, ["acl", "check", "db.migrate", "--input", "{}", "--format", "json"])
        assert missing.exit_code == 77

    def test_depth_feeds_max_call_depth(self, tmp_path):
        body = """\
default_effect: deny
rules:
  - callers: ["*"]
    targets: ["db.read"]
    effect: allow
    conditions:
      max_call_depth: 2
"""
        acl, source = _acl_from(tmp_path, body)
        cli = _build_cli(None, acl, source)
        shallow = CliRunner().invoke(cli, ["acl", "check", "db.read", "--depth", "1", "--format", "json"])
        assert shallow.exit_code == 0
        deep = CliRunner().invoke(cli, ["acl", "check", "db.read", "--depth", "9", "--format", "json"])
        assert deep.exit_code == 77

    def test_custom_caller_is_accepted(self, tmp_path):
        body = """\
default_effect: deny
rules:
  - callers: ["admin.*"]
    targets: ["*"]
    effect: allow
"""
        acl, source = _acl_from(tmp_path, body)
        cli = _build_cli(None, acl, source)
        assert CliRunner().invoke(cli, ["acl", "check", "db.read", "--caller", "admin.tool"]).exit_code == 0
        assert CliRunner().invoke(cli, ["acl", "check", "db.read"]).exit_code == 77

    def test_no_acl_exits_47(self):
        result = CliRunner().invoke(_build_cli(None, None, None), ["acl", "check", "db.read"])
        assert result.exit_code == 47
        assert "No ACL configured; nothing to check." in result.output

    def test_invalid_input_json_exits_2(self, tmp_path):
        acl, source = _acl_from(tmp_path, _THREE_RULES)
        result = CliRunner().invoke(_build_cli(None, acl, source), ["acl", "check", "db.read", "--input", "{nope}"])
        assert result.exit_code == 2

    def test_non_object_input_json_exits_2(self, tmp_path):
        acl, source = _acl_from(tmp_path, _THREE_RULES)
        result = CliRunner().invoke(_build_cli(None, acl, source), ["acl", "check", "db.read", "--input", "[1,2]"])
        assert result.exit_code == 2

    def test_check_flag_help_text_matches_the_normative_spec(self, tmp_path):
        """§4.5 pins all six `acl check` options.

        The three identity options restate the §4.3 root flags and MUST reuse
        their exact wording — two spellings of one flag inside one CLI is a
        defect whether or not this level is byte-matched. Held against the
        same literals the root guard uses, so the two levels cannot drift
        apart from each other either.
        """
        from .test_acl_loader import (
            PINNED_CHECK_ONLY_FLAGS,
            PINNED_IDENTITY_FLAGS,
            assert_pinned_help,
        )

        acl, source = _acl_from(tmp_path, _THREE_RULES)
        check_cmd = _build_cli(None, acl, source).commands["acl"].commands["check"]
        params = {opt: p for p in check_cmd.params for opt in (p.opts or [])}
        assert_pinned_help(params, {**PINNED_IDENTITY_FLAGS, **PINNED_CHECK_ONLY_FLAGS})


class TestIdentityFlagPrecedence:
    """§4.5: subcommand identity flags override their root counterparts.

    The merge is per-field: a root flag the subcommand does not restate still
    applies. All-or-nothing replacement would silently drop fields the caller
    never withdrew, which is the bug this suite pins shut.
    """

    _BODY = """\
default_effect: deny
rules:
  - callers: ["*"]
    targets: ["db.read"]
    effect: allow
    conditions:
      roles: ["admin"]
      identity_types: ["service"]
    description: "admin services only"
"""

    def _cli(self, tmp_path):
        acl, source = _acl_from(tmp_path, self._BODY)
        return _build_cli(None, acl, source)

    def _decide(self, cli, argv: list[str]) -> str:
        result = CliRunner().invoke(cli, ["acl", "check", "db.read", "--format", "json", *argv])
        return _leading_json(result.output)["access"]

    def test_subcommand_role_overrides_the_root_role(self, tmp_path):
        set_cli_identity(build_identity(None, "service", ("guest",)))
        # Root says guest; the subcommand restates the role as admin and wins.
        assert self._decide(self._cli(tmp_path), ["--role", "admin"]) == "allow"

    def test_root_role_alone_is_honoured(self, tmp_path):
        set_cli_identity(build_identity(None, "service", ("admin",)))
        assert self._decide(self._cli(tmp_path), []) == "allow"

    def test_unrestated_root_field_still_applies(self, tmp_path):
        """The discriminating case for a per-field merge.

        `identity_types: [service]` comes only from the root and is never
        restated; `roles: [admin]` comes only from the subcommand. The rule
        needs both, so it matches only if the merge kept the root's type
        instead of replacing the whole identity.
        """
        set_cli_identity(build_identity(None, "service", ()))
        assert self._decide(self._cli(tmp_path), ["--role", "admin"]) == "allow"

    def test_subcommand_type_overrides_the_root_type(self, tmp_path):
        set_cli_identity(build_identity(None, "user", ("admin",)))
        cli = self._cli(tmp_path)
        assert self._decide(cli, []) == "deny"
        assert self._decide(cli, ["--identity-type", "service"]) == "allow"

    def test_no_identity_anywhere_falls_through_to_default_effect(self, tmp_path):
        assert self._decide(self._cli(tmp_path), []) == "deny"

    def test_merge_helper_is_per_field(self):
        """Unit-level statement of the same rule."""
        from apcore_cli.acl_loader import merge_identity

        base = build_identity("root-id", "service", ("guest",))
        merged = merge_identity(base, None, None, ("admin",))
        assert merged.roles == ("admin",)  # restated -> overridden
        assert merged.type == "service"  # unrestated -> preserved
        assert merged.id == "root-id"  # unrestated -> preserved

        assert merge_identity(base) is base
        assert merge_identity(None) is None
        assert merge_identity(None, None, None, ("admin",)).roles == ("admin",)


# ---------------------------------------------------------------------------
# apcli acl validate (§4.6)
# ---------------------------------------------------------------------------


class TestAclValidate:
    def test_unregistered_condition_key_reports_and_exits_47(self, tmp_path):
        """T-ACL-17: the finding names rule index, path, key and effect."""
        body = """\
default_effect: allow
rules:
  - callers: ["*"]
    targets: ["a"]
    effect: allow
  - callers: ["*"]
    targets: ["b"]
    effect: deny
    conditions:
      mispelled: ["x"]
"""
        acl, source = _acl_from(tmp_path, body)
        result = CliRunner().invoke(_build_cli(None, acl, source), ["acl", "validate", "--format", "json"])
        assert result.exit_code == 47
        payload = json.loads(result.output)
        assert payload["count"] == 1
        finding = payload["findings"][0]
        assert finding["rule_index"] == 1
        assert finding["condition_path"] == "mispelled"
        assert finding["condition_key"] == "mispelled"
        assert finding["effect"] == "deny"

    def test_sync_and_async_columns_are_not_collapsed(self, tmp_path):
        """T-ACL-18: an async-only handler must render sync=no, async=yes."""
        from apcore import ACL as _ACL

        class _AsyncOnly:
            async def evaluate(self, value, context):  # pragma: no cover - never invoked
                return True

        _ACL.register_async_condition("async_only_probe", _AsyncOnly())
        try:
            body = """\
default_effect: allow
rules:
  - callers: ["*"]
    targets: ["a"]
    effect: deny
    conditions:
      async_only_probe: true
"""
            acl, source = _acl_from(tmp_path, body)
            cli = _build_cli(None, acl, source)

            payload = json.loads(CliRunner().invoke(cli, ["acl", "validate", "--format", "json"]).output)
            finding = payload["findings"][0]
            assert finding["sync_resolvable"] is False
            assert finding["async_resolvable"] is True

            table = CliRunner().invoke(cli, ["acl", "validate"])
            assert "Sync" in table.output and "Async" in table.output
        finally:
            _ACL._async_condition_handlers.pop("async_only_probe", None)

    def test_clean_rule_set_exits_0(self, tmp_path):
        """T-ACL-19."""
        acl, source = _acl_from(tmp_path, _THREE_RULES)
        result = CliRunner().invoke(_build_cli(None, acl, source), ["acl", "validate"])
        assert result.exit_code == 0
        assert "0 findings" in result.output

    def test_no_acl_exits_47(self):
        result = CliRunner().invoke(_build_cli(None, None, None), ["acl", "validate"])
        assert result.exit_code == 47
        assert "No ACL configured; nothing to check." in result.output


# ---------------------------------------------------------------------------
# apcli acl status (§4.7)
# ---------------------------------------------------------------------------


def _control_registry():
    """A registry reporting one ``system.control.*`` module.

    apcore's ``Registry.register`` refuses the reserved ``system`` prefix, so
    the write surface is simulated at the two read points
    ``governance_state()`` actually consults — ``list(visibility=…)`` and
    ``get(id)``.
    """
    registry = MagicMock()
    registry.list.return_value = ["system.control.enable"]
    registry.get.return_value = SimpleNamespace(
        module_id="system.control.enable",
        annotations=None,
    )
    return registry


class TestAclStatus:
    def test_unprotected_control_surface_without_acl(self):
        """T-ACL-20: control modules registered and nothing gating them."""
        executor = Executor(_control_registry())
        result = CliRunner().invoke(_build_cli(executor, None, None), ["acl", "status", "--format", "json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["control_modules_registered"] is True
        assert payload["acl_configured"] is False
        assert payload["unprotected_control_surface"] is True

    def test_strict_exits_47_when_unprotected(self):
        """T-ACL-21."""
        executor = Executor(_control_registry())
        result = CliRunner().invoke(_build_cli(executor, None, None), ["acl", "status", "--strict"])
        assert result.exit_code == 47
        assert "Unprotected control surface." in result.output

    def test_attached_acl_flips_acl_configured(self, tmp_path):
        acl, source = _acl_from(tmp_path, _THREE_RULES)
        executor = Executor(_control_registry())
        executor.set_acl(acl)
        result = CliRunner().invoke(_build_cli(executor, acl, source), ["acl", "status", "--format", "json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["acl_configured"] is True
        assert payload["unprotected_control_surface"] is False
        assert payload["acl_source"] == source

    def test_table_renders_nine_observations(self, tmp_path):
        acl, source = _acl_from(tmp_path, _THREE_RULES)
        executor = Executor(_control_registry())
        executor.set_acl(acl)
        result = CliRunner().invoke(_build_cli(executor, acl, source), ["acl", "status"])
        assert result.exit_code == 0
        for label in (
            "Control modules registered",
            "Read modules registered",
            "ACL configured",
            "Built-in ACL gate wired",
            "Approval handler configured",
            "Built-in approval gate wired",
            "Policy strict",
            "All control modules gated",
            "Unprotected control surface",
        ):
            assert label in result.output
        assert source in result.output

    def test_strict_exits_0_when_protected(self, tmp_path):
        acl, source = _acl_from(tmp_path, _THREE_RULES)
        executor = Executor(_control_registry())
        executor.set_acl(acl)
        result = CliRunner().invoke(_build_cli(executor, acl, source), ["acl", "status", "--strict"])
        assert result.exit_code == 0

    def test_executor_without_governance_state_exits_47(self):
        class _Bare:
            pass

        result = CliRunner().invoke(_build_cli(_Bare(), None, None), ["acl", "status"])
        assert result.exit_code == 47
        assert "governance_state" in result.output


# ---------------------------------------------------------------------------
# End-to-end: a real ACL denial reaching the dispatcher (T-ACL-22 / T-ACL-23)
# ---------------------------------------------------------------------------


@module(id="db.wipe", description="Wipe the database")
def _db_wipe() -> dict:  # pragma: no cover - denied before execution
    return {"wiped": True}


_DENY_ALL = """\
default_effect: deny
rules:
  - callers: ["@external"]
    targets: ["db.*"]
    effect: deny
    description: "no external db access"
"""


@pytest.fixture
def denied_executor(tmp_path):
    registry = Registry()
    fm = _db_wipe.apcore_module
    registry.register(fm.module_id, fm)
    executor = Executor(registry)
    acl, _source = _acl_from(tmp_path, _DENY_ALL)
    executor.set_acl(acl)
    return registry, executor


class TestAclDenialReachesDispatch:
    def test_exec_denied_exits_77(self, denied_executor):
        """T-ACL-22: the previously-inert acl_check step now denies."""
        from apcore_cli.discovery import register_exec_command

        registry, executor = denied_executor

        @click.group()
        def apcli() -> None:
            pass

        register_exec_command(apcli, registry, executor)
        result = CliRunner().invoke(apcli, ["exec", "db.wipe", "--yes"])
        assert result.exit_code == 77
        assert "db.wipe" in result.output

    def test_validate_dry_run_reports_failed_acl_row(self, denied_executor):
        """T-ACL-23: the `acl` preflight row reports a real verdict."""
        from apcore_cli.discovery import register_validate_command

        registry, executor = denied_executor

        @click.group()
        def apcli() -> None:
            pass

        register_validate_command(apcli, registry, executor)
        result = CliRunner().invoke(apcli, ["validate", "db.wipe", "--format", "json"])
        assert result.exit_code == 77
        payload = json.loads(result.output)
        acl_rows = [c for c in payload["checks"] if c["check"] == "acl"]
        assert acl_rows and acl_rows[0]["passed"] is False


_ROLE_GATED = """\
default_effect: deny
rules:
  - callers: ["*"]
    targets: ["db.wipe"]
    effect: allow
    conditions:
      roles: ["admin"]
    description: "admins only"
"""


class TestIdentityFlagsReachDispatch:
    """§4.3: the global identity flags build the Context `apcli exec` uses.

    This is the payoff of the flags — a conditional rule keyed on `roles` is
    only evaluable from the terminal because the CLI now constructs an
    Identity-carrying Context.
    """

    @pytest.fixture
    def role_gated_cli(self, tmp_path):
        registry = Registry()
        fm = _db_wipe.apcore_module
        registry.register(fm.module_id, fm)
        executor = Executor(registry)
        path = tmp_path / "acl.yaml"
        path.write_text(_ROLE_GATED, encoding="utf-8")
        return create_cli(registry=registry, executor=executor, acl=str(path), apcli=True)

    def test_role_flag_satisfies_the_condition(self, role_gated_cli):
        result = CliRunner().invoke(
            role_gated_cli, ["--role", "admin", "apcli", "exec", "db.wipe", "--yes", "--format", "json"]
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == {"wiped": True}

    def test_without_the_role_flag_the_call_is_denied(self, role_gated_cli):
        result = CliRunner().invoke(role_gated_cli, ["apcli", "exec", "db.wipe", "--yes"])
        assert result.exit_code == 77

    def test_wrong_role_is_denied(self, role_gated_cli):
        result = CliRunner().invoke(role_gated_cli, ["--role", "guest", "apcli", "exec", "db.wipe", "--yes"])
        assert result.exit_code == 77


# ---------------------------------------------------------------------------
# §4.10 — every execution path must be gated (T-ACL-31 / 32 / 34)
# ---------------------------------------------------------------------------


@module(id="sbx.run", description="A sandboxable module")
def _sbx_run() -> dict:
    return {"ran": True}


_SANDBOX_DENY = """\
default_effect: allow
rules:
  - callers: ["@external"]
    targets: ["sbx.run"]
    effect: deny
    description: "no sandboxed wipes"
"""

_SANDBOX_ALLOW_ALL = """\
default_effect: allow
rules:
  - callers: ["@external"]
    targets: ["nothing.matches"]
    effect: deny
    description: "inert"
"""

_SANDBOX_NEEDS_APPROVAL = """\
default_effect: allow
rules:
  - callers: ["@external"]
    targets: ["sbx.run"]
    effect: allow
    approval: required
    description: "a human must see this"
"""


class TestSandboxPathIsAclGated:
    """§4.10: `--sandbox` must not be an ACL bypass.

    The sandbox runner builds a fresh Registry + Executor in the subprocess
    and never calls ``set_acl``, so before this gate a rule denying a module
    was enforced for a plain call and silently ignored for a sandboxed one —
    switching on a *security* flag switched off access control. The decision
    is reached in the parent, before the spawn.
    """

    def _cli(self, tmp_path, body: str):
        registry = Registry()
        fm = _sbx_run.apcore_module
        registry.register(fm.module_id, fm)
        path = tmp_path / "acl.yaml"
        path.write_text(body, encoding="utf-8")
        return create_cli(registry=registry, executor=Executor(registry), acl=str(path), apcli=True)

    @staticmethod
    def _spy_spawn(monkeypatch):
        """Spy on the real spawn point, so 'never spawned' is asserted on the
        process creation itself rather than inferred from an exit code."""
        import apcore_cli.security.sandbox as sandbox_mod

        spawns: list[Any] = []

        def _fail_if_called(*args, **kwargs):  # pragma: no cover - must not run
            spawns.append(args)
            raise AssertionError("subprocess was spawned for an ACL-denied call")

        monkeypatch.setattr(sandbox_mod.subprocess, "Popen", _fail_if_called)
        return spawns

    @staticmethod
    def _stub_sandboxed_run(monkeypatch):
        """Replace the subprocess body with a spy, so an *allowed* call can be
        observed reaching the sandbox without paying for a real interpreter."""
        import apcore_cli.security.sandbox as sandbox_mod

        calls: list[str] = []

        def _fake(self, module_id, input_data):
            calls.append(module_id)
            return {"ran": True, "sandboxed": True}

        monkeypatch.setattr(sandbox_mod.Sandbox, "_sandboxed_execute", _fake)
        return calls

    def test_denied_sandboxed_call_exits_77_without_spawning(self, tmp_path, monkeypatch):
        """T-ACL-31."""
        spawns = self._spy_spawn(monkeypatch)
        cli = self._cli(tmp_path, _SANDBOX_DENY)
        result = CliRunner().invoke(cli, ["apcli", "exec", "sbx.run", "--sandbox", "--yes"])
        assert result.exit_code == 77, result.output
        assert spawns == [], "the subprocess must never be spawned for a denied call"

    def test_the_same_call_without_sandbox_is_also_denied(self, tmp_path):
        """T-ACL-31, discriminating half: one rule, both paths.

        Without this the gate could be satisfied by a sandbox-only refusal
        that had nothing to do with the ACL.
        """
        cli = self._cli(tmp_path, _SANDBOX_DENY)
        result = CliRunner().invoke(cli, ["apcli", "exec", "sbx.run", "--yes"])
        assert result.exit_code == 77, result.output

    def test_with_the_rule_removed_both_paths_succeed(self, tmp_path, monkeypatch):
        """T-ACL-31, the other discriminating half.

        Proves the 77s above come from the rule rather than from `--sandbox`
        being broken or the module being unrunnable.
        """
        calls = self._stub_sandboxed_run(monkeypatch)
        cli = self._cli(tmp_path, _SANDBOX_ALLOW_ALL)

        plain = CliRunner().invoke(cli, ["apcli", "exec", "sbx.run", "--yes", "--format", "json"])
        assert plain.exit_code == 0, plain.output
        assert json.loads(plain.output) == {"ran": True}

        sandboxed = CliRunner().invoke(cli, ["apcli", "exec", "sbx.run", "--sandbox", "--yes", "--format", "json"])
        assert sandboxed.exit_code == 0, sandboxed.output
        assert calls == ["sbx.run"], "an allowed call must still reach the sandbox"

    def test_allowed_sandboxed_call_runs_normally(self, tmp_path, monkeypatch):
        """T-ACL-32: isolation is unaffected for a permitted call."""
        calls = self._stub_sandboxed_run(monkeypatch)
        cli = self._cli(tmp_path, _SANDBOX_ALLOW_ALL)
        result = CliRunner().invoke(cli, ["apcli", "exec", "sbx.run", "--sandbox", "--yes", "--format", "json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == {"ran": True, "sandboxed": True}
        assert calls == ["sbx.run"]

    def test_acl_sourced_approval_gates_a_sandboxed_call(self, tmp_path, monkeypatch):
        """T-ACL-34: an ACL `approval: required` reaches the CLI approval gate.

        `sbx.run` carries no `requires_approval` annotation, so the gate fires
        only if the ACL-sourced requirement was composed in. CliRunner's stdin
        is not a terminal, so the handler refuses and the call is denied 46 —
        the deterministic, non-interactive outcome — and nothing is spawned.
        """
        spawns = self._spy_spawn(monkeypatch)
        cli = self._cli(tmp_path, _SANDBOX_NEEDS_APPROVAL)
        result = CliRunner().invoke(cli, ["apcli", "exec", "sbx.run", "--sandbox"])
        assert result.exit_code == 46, result.output
        assert spawns == []

    def test_the_same_call_without_the_approval_rule_is_not_gated(self, tmp_path, monkeypatch):
        """T-ACL-34, discriminating half: the gate came from the ACL rule."""
        calls = self._stub_sandboxed_run(monkeypatch)
        cli = self._cli(tmp_path, _SANDBOX_ALLOW_ALL)
        result = CliRunner().invoke(cli, ["apcli", "exec", "sbx.run", "--sandbox"])
        assert result.exit_code == 0, result.output
        assert calls == ["sbx.run"]

    def test_no_acl_leaves_the_sandbox_exactly_as_before(self, tmp_path, monkeypatch):
        """Enforcement-only-when-configured (§4.2) still holds for this path."""
        calls = self._stub_sandboxed_run(monkeypatch)
        registry = Registry()
        fm = _sbx_run.apcore_module
        registry.register(fm.module_id, fm)
        cli = create_cli(registry=registry, executor=Executor(registry), apcli=True)
        result = CliRunner().invoke(cli, ["apcli", "exec", "sbx.run", "--sandbox", "--yes"])
        assert result.exit_code == 0, result.output
        assert calls == ["sbx.run"]

    def test_sandbox_execute_gates_direct_api_callers(self, tmp_path):
        """The guard lives in `Sandbox.execute`, so a downstream embedder
        calling it directly is covered too — not only the CLI's dispatch."""
        from apcore_cli.acl_loader import set_cli_acl
        from apcore_cli.security.sandbox import Sandbox

        acl, _source = _acl_from(tmp_path, _SANDBOX_DENY)
        set_cli_acl(acl)
        with pytest.raises(SystemExit) as exc:
            Sandbox(enabled=True).execute("sbx.run", {}, MagicMock())
        assert exc.value.code == 77


@module(id="ops.control", description="A module with an optional flag")
def _ops_control(force: bool = False) -> dict:
    return {"ran": True, "force": force}


# `ops.control` rather than `system.control.*`: apcore's registry rejects the
# reserved `system` prefix, so the in-process comparison half of every
# discriminating test below would fail with MODULE_NOT_FOUND before ever
# reaching the ACL check — proving nothing.
_COND_DENY_DEPTH = """\
default_effect: allow
rules:
  - callers: ["@external"]
    targets: ["ops.control"]
    effect: deny
    conditions:
      max_call_depth: 5
    description: "conditional deny"
"""

_COND_DENY_ARGUMENTS = """\
default_effect: allow
rules:
  - callers: ["@external"]
    targets: ["ops.control"]
    effect: deny
    conditions:
      arguments:
        has_key: ["force"]
    description: "deny only the forced call"
"""


class TestDelegatedGateAlwaysSuppliesContext:
    """§4.10: the gate must evaluate against a real Context, with arguments.

    A second bypass one level below the first. PROTOCOL_SPEC §6.5 makes every
    conditional rule a non-match when a call supplies no context, while
    apcore's pipeline creates one at Step 1 for every real call — so a gate
    passing ``None`` leaves conditional ``deny`` rules inert on the sandbox
    path while they fire in-process. Same silent bypass, subtler cause. An
    unconditional rule cannot catch it, which is why every rule here has
    ``conditions``.
    """

    def _cli(self, tmp_path, body: str):
        registry = Registry()
        fm = _ops_control.apcore_module
        registry.register(fm.module_id, fm)
        path = tmp_path / "acl.yaml"
        path.write_text(body, encoding="utf-8")
        return create_cli(registry=registry, executor=Executor(registry), acl=str(path), apcli=True)

    def test_conditional_deny_fires_on_the_sandboxed_path(self, tmp_path, monkeypatch):
        """The case this requirement exists for.

        `max_call_depth` is satisfied by any real context (an empty call chain
        is depth 0) and unevaluable with none, so it isolates 'was a Context
        supplied' from every other variable.
        """
        spawns = TestSandboxPathIsAclGated._spy_spawn(monkeypatch)
        cli = self._cli(tmp_path, _COND_DENY_DEPTH)
        result = CliRunner().invoke(cli, ["apcli", "exec", "ops.control", "--sandbox", "--yes"])
        assert result.exit_code == 77, result.output
        assert spawns == []

    def test_the_same_conditional_rule_denies_in_process(self, tmp_path):
        """Discriminating half: the rule fires on both paths, not just one."""
        cli = self._cli(tmp_path, _COND_DENY_DEPTH)
        result = CliRunner().invoke(cli, ["apcli", "exec", "ops.control", "--yes"])
        assert result.exit_code == 77, result.output

    def test_arguments_condition_fires_on_the_sandboxed_path(self, tmp_path, monkeypatch):
        """The projection half: without it an `arguments` rule goes inert."""
        spawns = TestSandboxPathIsAclGated._spy_spawn(monkeypatch)
        cli = self._cli(tmp_path, _COND_DENY_ARGUMENTS)
        result = CliRunner().invoke(
            cli, ["apcli", "exec", "ops.control", "--sandbox", "--yes", "--input", '{"force": true}']
        )
        assert result.exit_code == 77, result.output
        assert spawns == []

    def test_arguments_condition_does_not_fire_without_the_key(self, tmp_path, monkeypatch):
        """Discriminating half: the denial tracks the argument, not the flag.

        A gate that denied every sandboxed call would pass the test above and
        fail this one.
        """
        calls = TestSandboxPathIsAclGated._stub_sandboxed_run(monkeypatch)
        cli = self._cli(tmp_path, _COND_DENY_ARGUMENTS)
        result = CliRunner().invoke(cli, ["apcli", "exec", "ops.control", "--sandbox", "--yes", "--input", "{}"])
        assert result.exit_code == 0, result.output
        assert calls == ["ops.control"]

    def test_arguments_condition_matches_the_in_process_verdict(self, tmp_path):
        """The two paths must agree for the same inputs."""
        cli = self._cli(tmp_path, _COND_DENY_ARGUMENTS)
        forced = CliRunner().invoke(cli, ["apcli", "exec", "ops.control", "--yes", "--input", '{"force": true}'])
        assert forced.exit_code == 77, forced.output
        plain = CliRunner().invoke(cli, ["apcli", "exec", "ops.control", "--yes", "--input", "{}"])
        assert plain.exit_code == 0, plain.output

    def test_delegated_context_is_never_none(self):
        """Unit-level statement of the rule."""
        from apcore_cli.acl_loader import build_context, delegated_context

        assert build_context(None) is None  # correct for `acl check`
        ctx = delegated_context(None, None)  # never for the gate
        assert ctx is not None
        assert ctx.governance_projection is not None
        assert ctx.caller_id is None

    def test_delegated_context_carries_identity_and_projection(self):
        from apcore_cli.acl_loader import build_context, delegated_context

        identity = build_identity("alice", "service", ("admin",))
        source = build_context(identity)
        ctx = delegated_context(source, {"force": True, "_approval_token": "t"})
        assert ctx.identity is identity
        # The framework-owned token is excluded from the projection (§6.1.8).
        assert ctx.governance_projection.keys == frozenset({"force"})
        # The caller's context is not mutated as a side effect.
        assert source.governance_projection is None
