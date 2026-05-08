"""Unit tests for :class:`apcore_cli.builtin_group.ApcliGroup` (FE-13).

Covers the visibility-resolution algorithm, tier precedence, env var parsing,
include/exclude filtering, and reserved-name constants. End-to-end CLI
integration lives in :mod:`tests.test_apcli_integration`.
"""

from __future__ import annotations

import logging

import pytest

from apcore_cli.builtin_group import (
    APCLI_SUBCOMMAND_NAMES,
    DEFAULT_BUILTIN_GROUP_NAME,
    RESERVED_GROUP_NAMES,
    ApcliGroup,
    ApcliGroupError,
)

# ---------------------------------------------------------------------------
# Boolean shorthand / dict form construction
# ---------------------------------------------------------------------------


class TestFromCliConfigShorthand:
    """Boolean / None shorthand semantics (spec §4.2)."""

    def test_true_is_mode_all(self):
        g = ApcliGroup.from_cli_config(True, registry_injected=False)
        assert g.resolve_visibility() == "all"
        assert g.is_group_visible() is True

    def test_false_is_mode_none(self):
        g = ApcliGroup.from_cli_config(False, registry_injected=False)
        assert g.resolve_visibility() == "none"
        assert g.is_group_visible() is False

    def test_none_is_auto_detect(self, monkeypatch):
        monkeypatch.delenv("APCORE_CLI_APCLI", raising=False)
        g = ApcliGroup.from_cli_config(None, registry_injected=False)
        assert g.resolve_visibility() == "all"  # standalone default
        g2 = ApcliGroup.from_cli_config(None, registry_injected=True)
        assert g2.resolve_visibility() == "none"  # embedded default

    def test_object_mode_include(self):
        g = ApcliGroup.from_cli_config(
            {"mode": "include", "include": ["list", "describe"]},
            registry_injected=False,
        )
        assert g.resolve_visibility() == "include"
        assert g.is_subcommand_included("list") is True
        assert g.is_subcommand_included("describe") is True
        assert g.is_subcommand_included("init") is False

    def test_object_mode_exclude(self):
        g = ApcliGroup.from_cli_config(
            {"mode": "exclude", "exclude": ["init"]},
            registry_injected=False,
        )
        assert g.resolve_visibility() == "exclude"
        assert g.is_subcommand_included("list") is True
        assert g.is_subcommand_included("init") is False


# ---------------------------------------------------------------------------
# Validation / error paths
# ---------------------------------------------------------------------------


class TestValidation:
    def test_invalid_mode_exits(self, capsys):
        with pytest.raises(SystemExit) as exc:
            ApcliGroup.from_cli_config({"mode": "whitelist"}, registry_injected=False)
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "Invalid apcli mode" in err
        assert "whitelist" in err

    def test_mode_auto_rejected(self, capsys):
        # "auto" is an internal sentinel and MUST be rejected from user config.
        with pytest.raises(SystemExit) as exc:
            ApcliGroup.from_cli_config({"mode": "auto"}, registry_injected=False)
        assert exc.value.code == 2

    def test_non_string_mode_exits(self, capsys):
        with pytest.raises(SystemExit):
            ApcliGroup.from_cli_config({"mode": 42}, registry_injected=False)  # type: ignore[dict-item]

    def test_non_bool_dict_none_raises_typeerror(self):
        with pytest.raises(TypeError, match="expected bool, dict"):
            ApcliGroup.from_cli_config("yes", registry_injected=False)  # type: ignore[arg-type]

    def test_yaml_non_mapping_falls_back_to_auto(self, caplog, monkeypatch):
        monkeypatch.delenv("APCORE_CLI_APCLI", raising=False)
        with caplog.at_level(logging.WARNING, logger="apcore_cli.builtin_group"):
            g = ApcliGroup.from_yaml(["unexpected"], registry_injected=True)
        assert g.resolve_visibility() == "none"  # auto → embedded default
        assert "must be a bool" in caplog.text


# ---------------------------------------------------------------------------
# include / exclude normalization (WARN on unknowns, keep forward-compat)
# ---------------------------------------------------------------------------


class TestListNormalization:
    def test_unknown_subcommand_warns(self, caplog):
        with caplog.at_level(logging.WARNING, logger="apcore_cli.builtin_group"):
            g = ApcliGroup.from_cli_config(
                {"mode": "include", "include": ["list", "bogus"]},
                registry_injected=False,
            )
        assert "Unknown apcli subcommand 'bogus'" in caplog.text
        # Entry retained for forward-compat; never matches a real subcommand.
        assert g.is_subcommand_included("bogus") is True
        assert g.is_subcommand_included("init") is False

    def test_non_list_include_warns_and_ignores(self, caplog):
        with caplog.at_level(logging.WARNING, logger="apcore_cli.builtin_group"):
            g = ApcliGroup.from_cli_config(
                {"mode": "include", "include": "list"},
                registry_injected=False,
            )
        assert "must be a list" in caplog.text
        # Fell back to empty include → nothing matches.
        assert g.is_subcommand_included("list") is False

    def test_empty_include_shows_only_exec_effectively(self):
        """mode=include with empty include list: every named subcommand is
        filtered out; the registrar layer (outside this class) must still
        register `exec` via the FE-12 _ALWAYS_REGISTERED guarantee."""
        g = ApcliGroup.from_cli_config(
            {"mode": "include", "include": []},
            registry_injected=False,
        )
        assert g.is_group_visible() is True
        # Every subcommand fails the filter — the always-registered exception
        # is enforced at the factory layer, not here.
        for name in APCLI_SUBCOMMAND_NAMES:
            assert g.is_subcommand_included(name) is False

    def test_is_subcommand_included_bypass_required_for_all_none(self):
        g = ApcliGroup.from_cli_config(True, registry_injected=False)
        with pytest.raises(AssertionError, match="caller should bypass"):
            g.is_subcommand_included("list")
        g2 = ApcliGroup.from_cli_config(False, registry_injected=False)
        with pytest.raises(AssertionError, match="caller should bypass"):
            g2.is_subcommand_included("list")


# ---------------------------------------------------------------------------
# Env var (Tier 2) parsing
# ---------------------------------------------------------------------------


class TestEnvVarParsing:
    @pytest.mark.parametrize("value", ["show", "SHOW", "1", "true", "True", "TRUE"])
    def test_show_family_forces_all(self, monkeypatch, value):
        monkeypatch.setenv("APCORE_CLI_APCLI", value)
        g = ApcliGroup.from_yaml(False, registry_injected=True)
        assert g.resolve_visibility() == "all"

    @pytest.mark.parametrize("value", ["hide", "HIDE", "0", "false", "False", "FALSE"])
    def test_hide_family_forces_none(self, monkeypatch, value):
        monkeypatch.setenv("APCORE_CLI_APCLI", value)
        g = ApcliGroup.from_yaml(True, registry_injected=False)
        assert g.resolve_visibility() == "none"

    def test_unknown_value_warns_and_ignored(self, caplog, monkeypatch):
        monkeypatch.setenv("APCORE_CLI_APCLI", "maybe")
        with caplog.at_level(logging.WARNING, logger="apcore_cli.builtin_group"):
            g = ApcliGroup.from_yaml(True, registry_injected=False)
        assert g.resolve_visibility() == "all"  # yaml wins
        assert "Unknown APCORE_CLI_APCLI" in caplog.text

    def test_unset_env_has_no_effect(self, monkeypatch):
        monkeypatch.delenv("APCORE_CLI_APCLI", raising=False)
        g = ApcliGroup.from_yaml(False, registry_injected=False)
        assert g.resolve_visibility() == "none"

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("  show  ", "all"),
            ("\tshow\n", "all"),
            (" 1 ", "all"),
            (" TRUE ", "all"),
            ("  HIDE  ", "none"),
            (" false\n", "none"),
            (" 0 ", "none"),
        ],
    )
    def test_env_value_is_trimmed_on_read(self, monkeypatch, value, expected):
        """D10-info-1 (cross-SDK parity): surrounding whitespace must NOT cause a
        Tier-3/Tier-4 silent fall-through. Spec invariant 2: env-var parser is
        case-insensitive AND trim-on-read.
        """
        monkeypatch.setenv("APCORE_CLI_APCLI", value)
        # Tier-3 yaml says the opposite of the env var so we can verify the env
        # parser actually consumed the (whitespace-padded) value.
        yaml = False if expected == "all" else True
        g = ApcliGroup.from_yaml(yaml, registry_injected=False)
        assert g.resolve_visibility() == expected

    def test_env_whitespace_only_falls_through(self, monkeypatch):
        """A whitespace-only env var should be treated as empty/unset."""
        monkeypatch.setenv("APCORE_CLI_APCLI", "   ")
        g = ApcliGroup.from_yaml(False, registry_injected=False)
        # No env override → yaml False wins → "none".
        assert g.resolve_visibility() == "none"


# ---------------------------------------------------------------------------
# 4-tier precedence (spec §4.4)
# ---------------------------------------------------------------------------


class TestTierPrecedence:
    def test_tier1_wins_over_env_and_yaml(self, monkeypatch):
        """CliConfig non-auto > env > yaml. (T-APCLI-13, T-APCLI-38)"""
        monkeypatch.setenv("APCORE_CLI_APCLI", "show")
        g = ApcliGroup.from_cli_config(False, registry_injected=False)
        assert g.resolve_visibility() == "none"  # Tier 1 wins

    def test_tier2_wins_over_yaml(self, monkeypatch):
        """env > yaml. (T-APCLI-10, T-APCLI-11)"""
        monkeypatch.setenv("APCORE_CLI_APCLI", "show")
        g = ApcliGroup.from_yaml(False, registry_injected=False)
        assert g.resolve_visibility() == "all"

    def test_tier2_over_auto_via_cli_config(self, monkeypatch):
        """When CliConfig value is auto, env should still apply (Tier 2)."""
        monkeypatch.setenv("APCORE_CLI_APCLI", "hide")
        g = ApcliGroup.from_cli_config(None, registry_injected=False)  # auto
        assert g.resolve_visibility() == "none"

    def test_tier3_used_when_no_env(self, monkeypatch):
        monkeypatch.delenv("APCORE_CLI_APCLI", raising=False)
        g = ApcliGroup.from_yaml({"mode": "include", "include": ["list"]}, registry_injected=False)
        assert g.resolve_visibility() == "include"

    def test_tier4_auto_standalone(self, monkeypatch):
        monkeypatch.delenv("APCORE_CLI_APCLI", raising=False)
        g = ApcliGroup.from_yaml(None, registry_injected=False)
        assert g.resolve_visibility() == "all"

    def test_tier4_auto_embedded(self, monkeypatch):
        monkeypatch.delenv("APCORE_CLI_APCLI", raising=False)
        g = ApcliGroup.from_yaml(None, registry_injected=True)
        assert g.resolve_visibility() == "none"


# ---------------------------------------------------------------------------
# disable_env semantics
# ---------------------------------------------------------------------------


class TestDisableEnv:
    def test_disable_env_true_seals_env(self, monkeypatch):
        """disable_env: true + env=show → yaml/default applies. (T-APCLI-14)"""
        monkeypatch.setenv("APCORE_CLI_APCLI", "show")
        g = ApcliGroup.from_yaml(
            {"mode": "none", "disable_env": True},
            registry_injected=False,
        )
        assert g.resolve_visibility() == "none"

    def test_disable_env_only_with_no_mode(self, monkeypatch):
        """disable_env: true + no mode + unset env → auto-detect fires."""
        monkeypatch.delenv("APCORE_CLI_APCLI", raising=False)
        g = ApcliGroup.from_yaml(
            {"disable_env": True},
            registry_injected=True,
        )
        # Tier 2 skipped, Tier 3 mode=auto, Tier 4 → embedded → none.
        assert g.resolve_visibility() == "none"

    def test_disable_env_accepts_camelcase_alias(self, monkeypatch):
        """Dict passed programmatically via TypeScript-style key."""
        monkeypatch.setenv("APCORE_CLI_APCLI", "show")
        g = ApcliGroup.from_cli_config(
            {"mode": "none", "disableEnv": True},
            registry_injected=False,
        )
        # Tier 1 already wins for non-auto; but also confirm disable_env held.
        assert g.resolve_visibility() == "none"

    def test_non_bool_disable_env_warns(self, caplog, monkeypatch):
        monkeypatch.delenv("APCORE_CLI_APCLI", raising=False)
        with caplog.at_level(logging.WARNING, logger="apcore_cli.builtin_group"):
            ApcliGroup.from_yaml(
                {"mode": "none", "disable_env": "yes"},
                registry_injected=False,
            )
        assert "disable_env must be boolean" in caplog.text


# ---------------------------------------------------------------------------
# Reserved names constant
# ---------------------------------------------------------------------------


class TestBuiltinGroupRename:
    """2026-05-08 cross-SDK parity: ApcliGroup ``name=`` parameter and
    ``create_cli(builtin_group_name=)`` kwarg let downstream branded CLIs
    rename the built-in command group from ``apcli`` to a custom namespace.
    """

    def test_default_name_is_apcli(self):
        g = ApcliGroup.from_cli_config(None, registry_injected=False)
        assert g.name == DEFAULT_BUILTIN_GROUP_NAME == "apcli"

    def test_custom_name_via_factory(self):
        g = ApcliGroup.from_cli_config(
            {"mode": "all"},
            registry_injected=False,
            name="admin",
        )
        assert g.name == "admin"
        assert g.resolve_visibility() == "all"

    def test_custom_name_via_yaml_factory(self):
        g = ApcliGroup.from_yaml({"mode": "none"}, registry_injected=False, name="builtin")
        assert g.name == "builtin"

    def test_invalid_name_raises_value_error(self):
        for bad in ("", "Apcli", "apcli/x", "1apcli", " apcli ", "with space"):
            with pytest.raises(ValueError, match="builtin_group_name"):
                ApcliGroup.from_cli_config(None, registry_injected=False, name=bad)

    def test_invalid_name_raises_apcli_group_error(self):
        """D1-info-1 (cross-SDK parity): invalid built-in group names raise the
        typed ``ApcliGroupError`` (a ``ValueError`` subclass for back-compat).
        Mirrors Rust's ``ApcliGroupError`` re-exported from ``lib.rs``.
        """
        with pytest.raises(ApcliGroupError, match="builtin_group_name"):
            ApcliGroup.from_cli_config(
                None, registry_injected=False, name="Bad-NAME"
            )

    def test_apcli_group_error_is_value_error_subclass(self):
        """Back-compat: existing ``except ValueError`` callers must still catch
        ``ApcliGroupError`` instances."""
        assert issubclass(ApcliGroupError, ValueError)
        try:
            ApcliGroup.from_cli_config(None, registry_injected=False, name="Bad")
        except ValueError as e:
            # Must be the typed subclass, not bare ValueError.
            assert isinstance(e, ApcliGroupError)
        else:
            pytest.fail("expected ApcliGroupError to be raised")

    def test_valid_names_accepted(self):
        for good in ("apcli", "admin", "built-in", "x", "a1-b_c"):
            g = ApcliGroup.from_cli_config(None, registry_injected=False, name=good)
            assert g.name == good


class TestReservedNames:
    def test_apcli_is_reserved(self):
        assert "apcli" in RESERVED_GROUP_NAMES

    def test_subcommand_names_are_frozen(self):
        assert isinstance(APCLI_SUBCOMMAND_NAMES, frozenset)
        # Spot-check: the 13 canonical entries are present.
        for name in (
            "list",
            "describe",
            "exec",
            "validate",
            "init",
            "health",
            "usage",
            "enable",
            "disable",
            "reload",
            "config",
            "completion",
            "describe-pipeline",
        ):
            assert name in APCLI_SUBCOMMAND_NAMES
