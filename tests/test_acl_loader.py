"""FE-14 ACL root resolution, loading and factory wiring.

Covers the T-ACL-* matrix rows that live below the command layer:
T-ACL-01..08 (resolution + load faults), T-ACL-25 (strategy bypass warning),
T-ACL-26/27/27a/27b/27c (§4.8 audit wiring), T-ACL-28/29 (embedded-host
attachment rules) and the identity-flag plumbing behind T-ACL-14/15.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from apcore_cli.acl_loader import (
    ACL_AUDIT_ENABLED_ENV_VAR,
    ACL_AUDIT_INCLUDE_DENIED_ENV_VAR,
    ACL_ROOT_ENV_VAR,
    DEFAULT_IDENTITY_ID,
    acl_file_for_root,
    build_context,
    build_identity,
    cli_context,
    get_cli_acl,
    is_acl_attached,
    load_cli_acl,
    make_acl_audit_logger,
    resolve_acl_root,
    resolve_audit_settings,
    set_cli_acl,
    set_cli_identity,
)
from apcore_cli.cli import set_audit_logger
from apcore_cli.config import ConfigResolver
from apcore_cli.factory import create_cli
from apcore_cli.security.audit import ACL_AUDIT_ENTRY_FIELDS, AuditLogger

_VALID_ACL = """\
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
"""

#: §4.8's **normative** audit-record key order — apcore's own ``AuditEntry``
#: declaration order, in the ``snake_case`` wire form every SDK writes. Spelled
#: out here rather than derived from the source constant so that reordering the
#: constant fails this file instead of being followed silently: the log is
#: JSONL, so the order is part of the cross-SDK wire contract, and TypeScript
#: (which camelCases these at runtime) converts on write to match it.
NORMATIVE_AUDIT_KEY_ORDER: list[str] = [
    "timestamp",
    "caller_id",
    "target_id",
    "decision",
    "reason",
    "matched_rule",
    "matched_rule_index",
    "identity_type",
    "roles",
    "call_depth",
    "trace_id",
    "handler_error",
    "approval_required",
]

#: §4.8 requirement 1's discriminating fixture. A file may legitimately grant
#: by default; a rebuild that passes the constructor's literal ``"deny"``
#: inverts this silently, and every ``deny``-defaulted fixture above passes
#: against that defect.
_ALLOW_DEFAULT_ACL = """\
default_effect: allow
rules:
  - callers: ["@external"]
    targets: ["system.control.*"]
    effect: deny
    description: "no external control"
"""


def _write_acl_dir(root: Path, body: str = _VALID_ACL) -> Path:
    acl_dir = root / "acl"
    acl_dir.mkdir(parents=True, exist_ok=True)
    (acl_dir / "global_acl.yaml").write_text(body, encoding="utf-8")
    return acl_dir


# ---------------------------------------------------------------------------
# Normative flag help text (§4.3 root, §4.5 `acl check`)
# ---------------------------------------------------------------------------
#
# Shared by the root and subcommand guards on purpose: the identity flags are
# spelled identically at both levels, and holding them against one literal is
# what makes a re-divergence between the two impossible rather than merely
# unlikely. `test_acl_cmd.py` imports these for the `acl check` surface.

PINNED_ACL_FLAG = ("PATH", "Path to the ACL file or directory (default: ./acl)")

PINNED_IDENTITY_FLAGS: dict[str, tuple[str, str]] = {
    "--identity-id": (
        "ID",
        "Assert Identity.id for ACL conditions. Unauthenticated assertion, not authentication.",
    ),
    "--identity-type": (
        "TYPE",
        "Assert Identity.type for ACL conditions (default: user). Unauthenticated assertion, not authentication.",
    ),
    "--role": (
        "ROLE",
        "Assert an Identity role for ACL conditions. Repeatable. Unauthenticated assertion, not authentication.",
    ),
}

#: The three options unique to ``apcli acl check`` (§4.5).
PINNED_CHECK_ONLY_FLAGS: dict[str, tuple[str, str]] = {
    "--caller": (
        "ID",
        "Simulated caller ID (default: @external). Nothing is executed, so any value is accepted.",
    ),
    "--depth": ("N", "Simulated call-chain depth for the max_call_depth condition."),
    "--input": (
        "JSON",
        "Argument map for the arguments condition. Key presence only; values are not compared.",
    ),
}


def assert_pinned_help(params: dict, expected: dict[str, tuple[str, str]]) -> None:
    """Assert each flag's ``metavar`` and ``help`` match the spec verbatim."""
    for flag, (metavar, help_text) in expected.items():
        assert flag in params, f"{flag} is not registered"
        assert params[flag].metavar == metavar, f"{flag} metavar drifted from the spec"
        assert params[flag].help == help_text, f"{flag} help text drifted from the spec"


@pytest.fixture
def workdir(tmp_path, monkeypatch, clean_env):
    """A clean cwd with an empty extensions dir so create_cli() constructs."""
    (tmp_path / "extensions").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# resolve_acl_root — the FE-07 4-tier chain (§4.1)
# ---------------------------------------------------------------------------


class TestResolveAclRoot:
    def test_tier4_missing_default_returns_none(self, workdir):
        """T-ACL-01: no ./acl directory -> nothing resolves."""
        assert resolve_acl_root(ConfigResolver()) is None

    def test_tier4_existing_default_resolves(self, workdir):
        """T-ACL-02: ./acl present -> the default root is the answer."""
        _write_acl_dir(workdir)
        assert resolve_acl_root(ConfigResolver()) == "./acl"

    def test_tier1_cli_flag_beats_yaml(self, workdir):
        """T-ACL-03: --acl wins over acl.root in apcore.yaml."""
        (workdir / "apcore.yaml").write_text("acl:\n  root: ./from-yaml\n", encoding="utf-8")
        assert resolve_acl_root(ConfigResolver(), "./from-flag") == "./from-flag"

    def test_tier2_env_beats_yaml(self, workdir, monkeypatch):
        """T-ACL-04: APCORE_ACL_ROOT wins over apcore.yaml."""
        (workdir / "apcore.yaml").write_text("acl:\n  root: ./from-yaml\n", encoding="utf-8")
        monkeypatch.setenv(ACL_ROOT_ENV_VAR, "./from-env")
        assert resolve_acl_root(ConfigResolver()) == "./from-env"

    def test_tier3_yaml_beats_default(self, workdir):
        (workdir / "apcore.yaml").write_text("acl:\n  root: ./from-yaml\n", encoding="utf-8")
        assert resolve_acl_root(ConfigResolver()) == "./from-yaml"

    def test_tier1_wins_over_env(self, workdir, monkeypatch):
        monkeypatch.setenv(ACL_ROOT_ENV_VAR, "./from-env")
        assert resolve_acl_root(ConfigResolver(), "./from-flag") == "./from-flag"

    def test_env_var_is_apcore_prefixed_not_apcore_cli(self, workdir, monkeypatch):
        """acl.root is an apcore-owned key, so APCORE_CLI_ACL_ROOT is inert."""
        monkeypatch.setenv("APCORE_CLI_ACL_ROOT", "./wrong")
        assert resolve_acl_root(ConfigResolver()) is None

    def test_empty_env_falls_through(self, workdir, monkeypatch):
        monkeypatch.setenv(ACL_ROOT_ENV_VAR, "")
        _write_acl_dir(workdir)
        assert resolve_acl_root(ConfigResolver()) == "./acl"

    def test_never_raises_for_nonexistent_explicit_root(self, workdir):
        assert resolve_acl_root(ConfigResolver(), "./nope") == "./nope"


# ---------------------------------------------------------------------------
# acl_file_for_root / load_cli_acl — the directory convention (§4.2)
# ---------------------------------------------------------------------------


class TestLoadCliAcl:
    def test_missing_path_attaches_nothing(self, workdir):
        """Hard invariant: a missing path MUST NOT synthesize an empty ACL."""
        assert acl_file_for_root("./nope") is None
        assert load_cli_acl("./nope") is None

    def test_directory_without_global_acl_attaches_nothing(self, workdir):
        """T-ACL-05: directory present, conventional file absent -> no-op."""
        (workdir / "acl").mkdir()
        assert acl_file_for_root("./acl") is None
        assert load_cli_acl("./acl") is None

    def test_directory_with_global_acl_loads(self, workdir):
        _write_acl_dir(workdir)
        assert acl_file_for_root("./acl") == str(Path("acl/global_acl.yaml"))
        acl = load_cli_acl("./acl")
        assert acl is not None
        assert acl.default_effect == "deny"
        assert len(acl.rules) == 2

    def test_file_root_loads_directly(self, workdir):
        path = workdir / "custom.yaml"
        path.write_text(_VALID_ACL, encoding="utf-8")
        acl = load_cli_acl(str(path))
        assert acl is not None
        assert len(acl.rules) == 2

    def test_unknown_rule_key_raises_acl_rule_error(self, workdir):
        """T-ACL-06: an unknown rule key is refused, naming the rule index."""
        from apcore.errors import ACLRuleError

        path = workdir / "bad.yaml"
        path.write_text(
            'default_effect: deny\nrules:\n  - callers: ["*"]\n    targets: ["a"]\n'
            "    effect: allow\n    priority: 3\n",
            encoding="utf-8",
        )
        with pytest.raises(ACLRuleError) as exc:
            load_cli_acl(str(path))
        assert "Rule 0" in str(exc.value)

    def test_invalid_effect_raises(self, workdir):
        """T-ACL-07: `effect: permit` is outside the closed enum."""
        from apcore.errors import ACLRuleError

        path = workdir / "bad.yaml"
        path.write_text(
            'default_effect: deny\nrules:\n  - callers: ["*"]\n    targets: ["a"]\n    effect: permit\n',
            encoding="utf-8",
        )
        with pytest.raises(ACLRuleError):
            load_cli_acl(str(path))

    def test_empty_callers_raises(self, workdir):
        """T-ACL-08: pattern-array arity is closed."""
        from apcore.errors import ACLRuleError

        path = workdir / "bad.yaml"
        path.write_text(
            'default_effect: deny\nrules:\n  - callers: []\n    targets: ["a"]\n    effect: allow\n',
            encoding="utf-8",
        )
        with pytest.raises(ACLRuleError):
            load_cli_acl(str(path))


# ---------------------------------------------------------------------------
# Identity flags (§4.3)
# ---------------------------------------------------------------------------


class TestIdentityFlags:
    def test_no_flags_builds_no_identity(self):
        assert build_identity(None, None, ()) is None

    def test_roles_only_uses_sentinel_id(self):
        identity = build_identity(None, None, ("admin",))
        assert identity is not None
        assert identity.id == DEFAULT_IDENTITY_ID
        assert identity.type == "user"
        assert identity.roles == ("admin",)

    def test_all_three_flags(self):
        identity = build_identity("alice", "service", ["a", "b"])
        assert identity.id == "alice"
        assert identity.type == "service"
        assert identity.roles == ("a", "b")

    def test_build_context_returns_none_when_empty(self):
        assert build_context(None) is None

    def test_build_context_carries_identity(self):
        identity = build_identity("alice", None, ("admin",))
        ctx = build_context(identity)
        assert ctx is not None
        assert ctx.identity is identity
        # caller_id is never fabricated — it is managed by Context.child().
        assert ctx.caller_id is None

    def test_build_context_depth_populates_call_chain(self):
        ctx = build_context(None, depth=3)
        assert ctx is not None
        assert len(ctx.call_chain) == 3

    def test_build_context_arguments_populate_projection(self):
        ctx = build_context(None, arguments={"force": True, "other": 1})
        assert ctx is not None
        assert ctx.governance_projection is not None
        assert ctx.governance_projection.keys == frozenset({"force", "other"})

    def test_cli_context_reflects_installed_identity(self):
        assert cli_context() is None
        set_cli_identity(build_identity(None, None, ("admin",)))
        ctx = cli_context()
        assert ctx is not None
        assert ctx.identity.roles == ("admin",)


# ---------------------------------------------------------------------------
# Factory wiring (§4.2 / §4.10)
# ---------------------------------------------------------------------------


class TestFactoryWiring:
    def test_no_acl_dir_attaches_nothing(self, workdir):
        """T-ACL-01: behaviour is identical to pre-FE-14."""
        create_cli(extensions_dir=str(workdir / "extensions"))
        assert is_acl_attached() is False

    def test_acl_dir_is_attached(self, workdir):
        """T-ACL-02: ./acl/global_acl.yaml present -> attached."""
        _write_acl_dir(workdir)
        create_cli(extensions_dir=str(workdir / "extensions"))
        assert is_acl_attached() is True

    def test_acl_kwarg_path_attaches(self, workdir):
        path = workdir / "custom.yaml"
        path.write_text(_VALID_ACL, encoding="utf-8")
        create_cli(extensions_dir=str(workdir / "extensions"), acl=str(path))
        assert is_acl_attached() is True

    def test_invalid_acl_file_exits_47(self, workdir):
        """T-ACL-06/07/08 at the CLI boundary: exit 47, not the generic 1."""
        path = workdir / "bad.yaml"
        path.write_text(
            'default_effect: deny\nrules:\n  - callers: ["*"]\n    targets: ["a"]\n    effect: permit\n',
            encoding="utf-8",
        )
        with pytest.raises(SystemExit) as exc:
            create_cli(extensions_dir=str(workdir / "extensions"), acl=str(path))
        assert exc.value.code == 47

    def test_injected_executor_without_acl_kwarg_is_left_alone(self, workdir):
        """T-ACL-28: an embedded host owns its own governance."""
        _write_acl_dir(workdir)
        registry = MagicMock()
        registry.list.return_value = []
        executor = MagicMock()
        create_cli(registry=registry, executor=executor)
        executor.set_acl.assert_not_called()
        assert is_acl_attached() is False

    def test_injected_executor_with_explicit_acl_is_honoured(self, workdir):
        """T-ACL-29: an explicit acl= is an instruction and always wins."""
        _write_acl_dir(workdir)
        registry = MagicMock()
        registry.list.return_value = []
        executor = MagicMock()
        create_cli(registry=registry, executor=executor, acl="./acl")
        executor.set_acl.assert_called_once()
        assert is_acl_attached() is True

    def test_prebuilt_acl_instance_is_attached_verbatim(self, workdir):
        from apcore import ACL, ACLRule

        acl = ACL(rules=[ACLRule(callers=["*"], targets=["*"], effect="allow")], default_effect="deny")
        registry = MagicMock()
        registry.list.return_value = []
        executor = MagicMock()
        create_cli(registry=registry, executor=executor, acl=acl)
        assert executor.set_acl.call_args.args[0] is acl

    def test_acl_and_openapi_are_registered_under_apcli(self, workdir):
        """FE-14 §4.10 / FE-15a §4.7 — four edit sites, third one."""
        cli = create_cli(extensions_dir=str(workdir / "extensions"))
        apcli = cli.commands["apcli"]
        assert "acl" in apcli.commands
        assert "openapi" in apcli.commands
        assert sorted(apcli.commands["acl"].commands) == ["check", "list", "status", "validate"]
        assert sorted(apcli.commands["openapi"].commands) == ["generate", "scan"]

    def test_neither_is_always_registered_under_include_mode(self, workdir):
        """Neither is in _ALWAYS_REGISTERED, so include-mode must gate them."""
        cli = create_cli(
            extensions_dir=str(workdir / "extensions"),
            apcli={"mode": "include", "include": ["list"]},
        )
        apcli = cli.commands["apcli"]
        assert "acl" not in apcli.commands
        assert "openapi" not in apcli.commands

        listed = create_cli(
            extensions_dir=str(workdir / "extensions"),
            apcli={"mode": "include", "include": ["acl", "openapi"]},
        )
        assert "acl" in listed.commands["apcli"].commands
        assert "openapi" in listed.commands["apcli"].commands

    def test_openapi_registers_without_an_executor(self):
        """FE-15a needs neither registry nor executor."""
        import click

        from apcore_cli.builtin_group import ApcliGroup
        from apcore_cli.exposure import ExposureFilter
        from apcore_cli.factory import _register_apcli_subcommands

        group = click.Group(name="apcli")
        _register_apcli_subcommands(
            group,
            ApcliGroup.from_cli_config(True, registry_injected=True),
            registry=MagicMock(),
            executor=None,
            exposure_filter=ExposureFilter(),
            prog_name="x",
        )
        assert "openapi" in group.commands
        # `acl status` reads governance_state(), so the group needs an executor.
        assert "acl" not in group.commands

    def test_acl_flag_registered_only_in_standalone_mode(self, workdir):
        standalone = create_cli(extensions_dir=str(workdir / "extensions"))
        assert any("--acl" in (p.opts or []) for p in standalone.params)

        registry = MagicMock()
        registry.list.return_value = []
        embedded = create_cli(registry=registry, executor=MagicMock())
        assert not any("--acl" in (p.opts or []) for p in embedded.params)

    def test_identity_flags_are_global_and_always_registered(self, workdir):
        cli = create_cli(extensions_dir=str(workdir / "extensions"))
        opts = {opt for p in cli.params for opt in (p.opts or [])}
        assert {"--identity-id", "--identity-type", "--role"} <= opts

    def test_identity_flags_install_a_process_identity(self, workdir):
        cli = create_cli(extensions_dir=str(workdir / "extensions"))
        CliRunner().invoke(cli, ["--role", "admin", "--identity-type", "service", "apcli", "list"])
        ctx = cli_context()
        assert ctx is not None
        assert ctx.identity.roles == ("admin",)
        assert ctx.identity.type == "service"

    def test_root_acl_flag_help_text_matches_the_normative_spec(self, workdir):
        """§4.3 pins these four `help=` strings and metavars NORMATIVELY.

        The `apcli-visibility` conformance fixtures byte-match root ``--help``
        across Python, TypeScript and Rust, so this text is a cross-SDK
        contract rather than a per-SDK stylistic choice — the three SDKs did
        drift here once. Asserted directly rather than through the golden
        fixture because this repo's byte-match test is ``xfail`` pending the
        canonical clap-style help formatter, which would let a reword pass
        unnoticed here and fail in another SDK.

        Compared against ``Option.help`` rather than rendered output, so the
        assertion is independent of click's terminal-width wrapping.
        """
        cli = create_cli(extensions_dir=str(workdir / "extensions"))
        params = {opt: p for p in cli.params for opt in (p.opts or [])}
        expected = {"--acl": PINNED_ACL_FLAG, **PINNED_IDENTITY_FLAGS}
        assert_pinned_help(params, expected)


# ---------------------------------------------------------------------------
# Strategy bypass warning (§6.2)
# ---------------------------------------------------------------------------


class TestStrategyBypassWarning:
    @pytest.mark.parametrize("strategy", ["internal", "testing", "minimal"])
    def test_warns_for_acl_dropping_strategies(self, strategy, capsys):
        """T-ACL-25: the warning names the *configured* ACL."""
        from apcore_cli.strategy import warn_if_acl_bypassed

        set_cli_acl(object())
        warn_if_acl_bypassed(strategy)
        err = capsys.readouterr().err
        assert f"'{strategy}' strategy" in err
        assert "configured ACL is not enforced" in err

    @pytest.mark.parametrize("strategy", ["standard", "performance", None, ""])
    def test_silent_for_acl_preserving_strategies(self, strategy, capsys):
        from apcore_cli.strategy import warn_if_acl_bypassed

        set_cli_acl(object())
        warn_if_acl_bypassed(strategy)
        assert capsys.readouterr().err == ""

    def test_silent_when_no_acl_attached(self, capsys):
        from apcore_cli.strategy import warn_if_acl_bypassed

        set_cli_acl(None)
        warn_if_acl_bypassed("testing")
        assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# Audit wiring (§4.8) — T-ACL-26 / 27 / 27a / 27b / 27c
# ---------------------------------------------------------------------------


@pytest.fixture
def audit_path(tmp_path):
    """A throwaway FE-05 audit log, installed as the process-wide logger.

    Installed *after* whatever `create_cli` did, which is safe because the
    §4.8 callback resolves the logger late — see `cli.get_audit_logger`.
    """
    path = tmp_path / "audit-log" / "audit.jsonl"
    set_audit_logger(AuditLogger(path=path))
    return path


def _audit_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_audit_config(root: Path, **settings: bool) -> None:
    body = "acl:\n  audit:\n" + "".join(f"    {k}: {str(v).lower()}\n" for k, v in settings.items())
    (root / "apcore.yaml").write_text(body, encoding="utf-8")


class TestAuditConfigKeys:
    """§5 — the two keys are registered, not merely documented."""

    def test_defaults_are_registered_and_true(self):
        assert ConfigResolver.DEFAULTS["acl.audit.enabled"] is True
        assert ConfigResolver.DEFAULTS["acl.audit.include_denied"] is True

    def test_defaults_resolve_to_true(self, workdir):
        assert resolve_audit_settings(ConfigResolver()) == (True, True)

    def test_yaml_overrides_the_defaults(self, workdir):
        _write_audit_config(workdir, enabled=False, include_denied=False)
        assert resolve_audit_settings(ConfigResolver()) == (False, False)

    @pytest.mark.parametrize("raw", ["false", "FALSE", "0", "no", "off"])
    def test_env_false_spellings_disable(self, workdir, monkeypatch, raw):
        """`bool("false")` is True, so the env tier needs a spelling table."""
        monkeypatch.setenv(ACL_AUDIT_ENABLED_ENV_VAR, raw)
        monkeypatch.setenv(ACL_AUDIT_INCLUDE_DENIED_ENV_VAR, raw)
        assert resolve_audit_settings(ConfigResolver()) == (False, False)

    @pytest.mark.parametrize("raw", ["true", "1", "yes", "on"])
    def test_env_true_spellings_enable(self, workdir, monkeypatch, raw):
        _write_audit_config(workdir, enabled=False, include_denied=False)
        monkeypatch.setenv(ACL_AUDIT_ENABLED_ENV_VAR, raw)
        monkeypatch.setenv(ACL_AUDIT_INCLUDE_DENIED_ENV_VAR, raw)
        assert resolve_audit_settings(ConfigResolver()) == (True, True)

    def test_unrecognized_spelling_falls_back_to_true(self, workdir, monkeypatch):
        """A typo in a governance key must not silently stop the audit trail."""
        monkeypatch.setenv(ACL_AUDIT_ENABLED_ENV_VAR, "maybe")
        assert resolve_audit_settings(ConfigResolver()) == (True, True)


class TestAuditWiring:
    def test_denied_call_writes_one_entry_with_all_13_fields(self, workdir, audit_path):
        """T-ACL-26: `acl.audit.enabled: true`, run a denied call.

        End-to-end through `create_cli` rather than through `load_cli_acl`
        directly, so the assertion covers the factory actually installing the
        callback and not just the loader being able to.
        """
        _write_acl_dir(workdir)
        create_cli(extensions_dir=str(workdir / "extensions"))
        set_audit_logger(AuditLogger(path=audit_path))

        acl = get_cli_acl()
        assert acl is not None
        decision = acl.check_access("@external", "system.control.disable", None)
        assert decision.access == "deny"

        records = _audit_records(audit_path)
        assert len(records) == 1
        assert records[0]["decision"] == "deny"
        # §4.8: an *equality* check on the ordered key list, never a
        # containment one — that pins field set, order and casing in a single
        # assertion. The sequence is spelled out literally rather than compared
        # against ACL_AUDIT_ENTRY_FIELDS on purpose: this is the normative
        # cross-SDK wire order, so reordering the constant must turn this red
        # instead of being silently followed.
        assert list(records[0].keys()) == NORMATIVE_AUDIT_KEY_ORDER
        assert records[0]["target_id"] == "system.control.disable"
        assert records[0]["matched_rule_index"] == 0

    def test_the_field_constant_matches_the_normative_order(self):
        """The source constant is the serialization order, so pin it too."""
        assert list(ACL_AUDIT_ENTRY_FIELDS) == NORMATIVE_AUDIT_KEY_ORDER
        assert len(NORMATIVE_AUDIT_KEY_ORDER) == 13

    def test_identity_fields_reach_the_record(self, workdir, audit_path):
        """T-ACL-26, second half: the fields are populated, not just present."""
        _write_acl_dir(workdir)
        acl = load_cli_acl(str(workdir / "acl"))
        acl.check_access(
            "@external",
            "system.control.disable",
            build_context(build_identity("alice", "service", ("admin",))),
        )
        record = _audit_records(audit_path)[0]
        assert record["identity_type"] == "service"
        assert record["roles"] == ["admin"]
        assert record["approval_required"] is False
        assert record["handler_error"] is None

    def test_include_denied_false_suppresses_only_deny(self, workdir, audit_path):
        """T-ACL-27: the deny entry is absent; the allow entry is written."""
        _write_acl_dir(workdir)
        _write_audit_config(workdir, include_denied=False)
        create_cli(extensions_dir=str(workdir / "extensions"))
        set_audit_logger(AuditLogger(path=audit_path))

        acl = get_cli_acl()
        assert acl.check_access("@external", "system.control.disable", None).access == "deny"
        assert acl.check_access("@external", "db.migrate", None).access == "allow"

        records = _audit_records(audit_path)
        # Not inverted: `include_denied: false` drops denials, never allows.
        assert [r["decision"] for r in records] == ["allow"]
        assert records[0]["target_id"] == "db.migrate"

    def test_include_denied_true_writes_both(self, workdir, audit_path):
        """T-ACL-27, the other half of the §4.8 table."""
        _write_acl_dir(workdir)
        acl = load_cli_acl(str(workdir / "acl"), include_denied=True)
        acl.check_access("@external", "system.control.disable", None)
        acl.check_access("@external", "db.migrate", None)
        assert [r["decision"] for r in _audit_records(audit_path)] == ["deny", "allow"]

    def test_audit_disabled_installs_no_callback_and_does_not_rebuild(self, workdir, audit_path):
        """T-ACL-27a: no callback, and the `ACL.load` result attached directly.

        The load-result identity assertion is the point: "nothing was written"
        alone would also pass against a rebuild that installed a callback the
        `include_denied` filter happened to drop everything through.
        """
        from apcore import ACL as _ACL

        _write_acl_dir(workdir)
        _write_audit_config(workdir, enabled=False)

        real_load = _ACL.load
        captured: dict[str, object] = {}

        def _spy(path: str):
            result = real_load(path)
            captured["acl"] = result
            return result

        with patch.object(_ACL, "load", _spy):
            create_cli(extensions_dir=str(workdir / "extensions"))
        loaded = captured["acl"]
        set_audit_logger(AuditLogger(path=audit_path))

        acl = get_cli_acl()
        assert acl is loaded, "the ACL.load result must be attached directly, not rebuilt"
        # Provenance survives precisely because there was no rebuild.
        acl.reload()

        assert acl.check_access("@external", "system.control.disable", None).access == "deny"
        assert acl.check_access("@external", "db.migrate", None).access == "allow"
        assert _audit_records(audit_path) == []

    def test_auditing_path_rebuilds_and_documents_the_reload_cost(self, workdir):
        """§4.8 requirement 2 — accepted, and pinned so it cannot be silent."""
        from apcore.errors import ACLRuleError

        _write_acl_dir(workdir)
        rebuilt = load_cli_acl(str(workdir / "acl"), audit_enabled=True)
        with pytest.raises(ACLRuleError, match="was not loaded from a YAML file"):
            rebuilt.reload()

    def test_allow_default_survives_the_rebuild(self, workdir, audit_path):
        """T-ACL-27b: `default_effect: allow` + auditing enabled.

        **Discriminating for §4.8 requirement 1.** Every fixture above uses a
        `deny`-defaulted file and passes against a rebuild that hardcodes the
        literal `"deny"`; this one is the only case that catches it.
        """
        _write_acl_dir(workdir, _ALLOW_DEFAULT_ACL)
        create_cli(extensions_dir=str(workdir / "extensions"))
        set_audit_logger(AuditLogger(path=audit_path))

        acl = get_cli_acl()
        assert acl.default_effect == "allow", "default_effect must come from the file, never a literal"

        # A call matching no rule is governed by the file's default.
        decision = acl.check_access("@external", "db.read", None)
        assert decision.access == "allow"
        assert decision.matched_rule_index is None

        # ...and the rule that *is* declared still bites, so the fixture is not
        # simply permitting everything.
        assert acl.check_access("@external", "system.control.disable", None).access == "deny"

        # The callback is genuinely installed on this ACL, so the case is a
        # test of the auditing path and not of an accidentally-unaudited one.
        assert [r["decision"] for r in _audit_records(audit_path)] == ["allow", "deny"]

    def test_allow_default_survives_the_rebuild_at_loader_level(self, workdir):
        """T-ACL-27b at the loader, independent of the factory."""
        _write_acl_dir(workdir, _ALLOW_DEFAULT_ACL)
        acl = load_cli_acl(str(workdir / "acl"), audit_enabled=True)
        assert acl.default_effect == "allow"
        assert acl.check_access("@external", "db.read", None).access == "allow"

    def test_embedder_supplied_acl_is_attached_unchanged(self, workdir, audit_path):
        """T-ACL-27c: identity, not equivalence — so it retains `reload()`."""
        from apcore import ACL as _ACL

        acl_dir = _write_acl_dir(workdir)
        supplied = _ACL.load(str(acl_dir / "global_acl.yaml"))

        registry = MagicMock()
        registry.list.return_value = []
        executor = MagicMock()
        create_cli(registry=registry, executor=executor, acl=supplied)

        assert executor.set_acl.call_args.args[0] is supplied
        assert get_cli_acl() is supplied
        # Never rebuilt, whatever `acl.audit.enabled` says, so `reload()` works.
        supplied.reload()

        # And no CLI callback was grafted onto it: the embedder owns its own
        # audit sink, or deliberately has none.
        set_audit_logger(AuditLogger(path=audit_path))
        supplied.check_access("@external", "system.control.disable", None)
        assert _audit_records(audit_path) == []

    def test_callback_is_inert_when_no_audit_logger_is_installed(self, workdir):
        """§4.8: no FE-05 logger -> the callback writes nothing, silently.

        Matching `AuditLogger`'s own write-failure posture, and the decision
        must still be reachable.
        """
        set_audit_logger(None)
        _write_acl_dir(workdir)
        acl = load_cli_acl(str(workdir / "acl"))
        assert acl.check_access("@external", "db.migrate", None).access == "allow"

    def test_a_failing_audit_sink_never_breaks_the_decision(self, workdir):
        """§4.8: a logging fault MUST NOT change an access decision.

        apcore calls the callback inline and unguarded on the decision path, so
        failing to *record* a verdict must not become a failure to *reach* one.
        """
        exploding = MagicMock()
        exploding.log_acl_decision.side_effect = RuntimeError("disk on fire")
        set_audit_logger(exploding)

        _write_acl_dir(workdir)
        acl = load_cli_acl(str(workdir / "acl"))
        assert acl.check_access("@external", "system.control.disable", None).access == "deny"
        exploding.log_acl_decision.assert_called_once()

    def test_make_acl_audit_logger_filters_only_denials(self, audit_path):
        """The filter is the callback's, not the writer's."""
        from apcore import AuditEntry

        def _entry(decision: str) -> AuditEntry:
            return AuditEntry(
                timestamp="2026-09-06T00:00:00Z",
                caller_id="@external",
                target_id="db.migrate",
                decision=decision,
                reason="rule_match",
            )

        callback = make_acl_audit_logger(include_denied=False)
        callback(_entry("deny"))
        assert _audit_records(audit_path) == []
        callback(_entry("allow"))
        assert [r["decision"] for r in _audit_records(audit_path)] == ["allow"]

    def test_writer_records_what_it_is_given(self, audit_path):
        """`log_acl_decision` applies no policy of its own — §4.8's split."""
        from apcore import AuditEntry

        from apcore_cli.cli import get_audit_logger

        get_audit_logger().log_acl_decision(
            AuditEntry(
                timestamp="2026-09-06T00:00:00Z",
                caller_id="@external",
                target_id="system.control.disable",
                decision="deny",
                reason="rule_match",
                roles=("admin", "ops"),
            )
        )
        record = _audit_records(audit_path)[0]
        assert record["decision"] == "deny"
        # A tuple on the dataclass, a JSON array on the wire.
        assert record["roles"] == ["admin", "ops"]
