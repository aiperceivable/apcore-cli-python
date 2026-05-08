"""Tests for Approval Gate (FE-03)."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from apcore_cli.approval import ApprovalDeniedError, ApprovalTimeoutError, check_approval


def test_check_approval_is_exported_at_package_root():
    """D1-002: check_approval must be importable from apcore_cli (cross-SDK parity).

    Rust src/lib.rs:141 and TS src/index.ts:23 expose check_approval at the
    crate / package root; Python previously only re-exported the handler and
    error classes. Import path parity matters because cross-language docs
    and examples reference apcore_cli.check_approval directly.
    """
    import apcore_cli

    assert (
        apcore_cli.check_approval is check_approval
    ), "apcore_cli.check_approval must alias apcore_cli.approval.check_approval"
    assert (
        "check_approval" in apcore_cli.__all__
    ), "check_approval must be in apcore_cli.__all__ for `from apcore_cli import *`"


def test_module_not_found_error_exports_with_deprecated_alias():
    """D1-002: apcore_cli re-exports ModuleNotFoundError (cross-SDK parity)
    and keeps CliModuleNotFoundError as a deprecated alias.

    TypeScript exports ``ModuleNotFoundError`` from src/index.ts and Rust
    surfaces an equivalent ``DiscoveryError::ModuleNotFound`` — Python
    aligned in v0.8.x by renaming back to ``ModuleNotFoundError``. Importers
    that still pull the old name continue to work because
    :data:`apcore_cli.security.sandbox.CliModuleNotFoundError` is kept as
    an alias to the renamed class until v0.10.0.

    Per-language note: ``from apcore_cli import *`` would shadow
    :class:`builtins.ModuleNotFoundError`. Importers should prefer
    ``import apcore_cli`` and access ``apcore_cli.ModuleNotFoundError``,
    or import the qualified path
    ``from apcore_cli.security.sandbox import ModuleNotFoundError``.
    """
    import apcore_cli

    assert hasattr(apcore_cli, "ModuleNotFoundError")
    assert "ModuleNotFoundError" in apcore_cli.__all__
    # Deprecated alias still resolves to the same class.
    assert hasattr(apcore_cli, "CliModuleNotFoundError")
    assert "CliModuleNotFoundError" in apcore_cli.__all__
    assert apcore_cli.CliModuleNotFoundError is apcore_cli.ModuleNotFoundError


def _make_module(requires_approval=None, approval_message=None):
    m = MagicMock()
    m.module_id = "test.module"
    m.canonical_id = "test.module"
    if requires_approval is None:
        m.annotations = None
    else:
        ann = {"requires_approval": requires_approval}
        if approval_message:
            ann["approval_message"] = approval_message
        m.annotations = ann
    return m


class TestCheckApprovalBypass:
    """Task 1: Bypass and skip logic."""

    def test_no_annotations_skips(self):
        m = _make_module(requires_approval=None)
        check_approval(m, auto_approve=False)  # No error

    def test_annotations_not_dict_skips(self):
        m = MagicMock()
        m.module_id = "test"
        m.annotations = "not a dict"
        check_approval(m, auto_approve=False)  # No error

    def test_requires_approval_false_skips(self):
        m = _make_module(requires_approval=False)
        check_approval(m, auto_approve=False)  # No error

    def test_requires_approval_string_true_skips(self):
        m = _make_module(requires_approval="true")
        check_approval(m, auto_approve=False)  # No error (not bool True)

    def test_bypass_yes_flag(self, caplog):
        m = _make_module(requires_approval=True)
        with caplog.at_level(logging.INFO, logger="apcore_cli.approval"):
            check_approval(m, auto_approve=True)
        assert "bypassed via --yes flag" in caplog.text

    def test_bypass_env_var(self, monkeypatch, caplog):
        monkeypatch.setenv("APCORE_CLI_AUTO_APPROVE", "1")
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        m = _make_module(requires_approval=True)
        with caplog.at_level(logging.INFO, logger="apcore_cli.approval"):
            check_approval(m, auto_approve=False)
        assert "bypassed via APCORE_CLI_AUTO_APPROVE" in caplog.text

    def test_env_var_not_one_warns(self, monkeypatch, capsys):
        # D10-009 cross-SDK parity: warning is now emitted on stderr (not
        # via the Python logger) so callers see a consistent user-visible
        # channel regardless of logger handler config. Test switched from
        # caplog to capsys.
        monkeypatch.setenv("APCORE_CLI_AUTO_APPROVE", "true")
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        m = _make_module(requires_approval=True)
        with pytest.raises(ApprovalDeniedError):
            check_approval(m, auto_approve=False)
        captured = capsys.readouterr()
        assert "expected '1'" in captured.err
        assert "Warning:" in captured.err

    def test_yes_flag_priority_over_env(self, monkeypatch, caplog):
        monkeypatch.setenv("APCORE_CLI_AUTO_APPROVE", "1")
        m = _make_module(requires_approval=True)
        with caplog.at_level(logging.INFO, logger="apcore_cli.approval"):
            check_approval(m, auto_approve=True)
        assert "bypassed via --yes flag" in caplog.text


class TestNonTTYRejection:
    """Task 2: Non-TTY rejection."""

    def test_non_tty_no_bypass_exits_46(self, monkeypatch):
        """D11-001: non-TTY denial now raises ApprovalDeniedError (carries
        ``code='APPROVAL_DENIED'`` which maps to exit 46 via _ERROR_CODE_MAP).
        Previously this was a direct ``sys.exit(46)`` that bypassed audit-flush.
        """
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.delenv("APCORE_CLI_AUTO_APPROVE", raising=False)
        m = _make_module(requires_approval=True)
        with pytest.raises(ApprovalDeniedError) as exc_info:
            check_approval(m, auto_approve=False)
        assert exc_info.value.code == "APPROVAL_DENIED"

    def test_non_tty_with_yes_flag_proceeds(self, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        m = _make_module(requires_approval=True)
        check_approval(m, auto_approve=True)  # No error


class TestTTYPrompt:
    """Task 3: TTY prompt with timeout."""

    def test_tty_user_approves(self, monkeypatch, caplog):
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.delenv("APCORE_CLI_AUTO_APPROVE", raising=False)
        m = _make_module(requires_approval=True)
        with (
            patch("apcore_cli.approval.click.confirm", return_value=True),
            caplog.at_level(logging.INFO, logger="apcore_cli.approval"),
        ):
            check_approval(m, auto_approve=False)
        assert "approved" in caplog.text

    def test_tty_user_denies(self, monkeypatch):
        """D11-001: TTY rejection raises ApprovalDeniedError (exit 46 via map)."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.delenv("APCORE_CLI_AUTO_APPROVE", raising=False)
        m = _make_module(requires_approval=True)
        with (
            patch("apcore_cli.approval.click.confirm", return_value=False),
            pytest.raises(ApprovalDeniedError) as exc_info,
        ):
            check_approval(m, auto_approve=False)
        assert exc_info.value.code == "APPROVAL_DENIED"

    def test_tty_timeout(self, monkeypatch):
        """D11-001: TTY timeout raises ApprovalTimeoutError (exit 46 via map)."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.delenv("APCORE_CLI_AUTO_APPROVE", raising=False)
        m = _make_module(requires_approval=True)
        with (
            patch(
                "apcore_cli.approval.click.confirm",
                side_effect=ApprovalTimeoutError(),
            ),
            pytest.raises(ApprovalTimeoutError) as exc_info,
        ):
            check_approval(m, auto_approve=False)
        assert exc_info.value.code == "APPROVAL_TIMEOUT"

    def test_custom_approval_message(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.delenv("APCORE_CLI_AUTO_APPROVE", raising=False)
        m = _make_module(
            requires_approval=True,
            approval_message="DANGER: This will delete everything!",
        )
        with patch("apcore_cli.approval.click.confirm", return_value=True):
            check_approval(m, auto_approve=False)
        err = capsys.readouterr().err
        assert "DANGER: This will delete everything!" in err

    def test_default_approval_message(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.delenv("APCORE_CLI_AUTO_APPROVE", raising=False)
        m = _make_module(requires_approval=True)
        with patch("apcore_cli.approval.click.confirm", return_value=True):
            check_approval(m, auto_approve=False)
        err = capsys.readouterr().err
        assert "requires approval to execute" in err


class TestApprovalTimeoutError:
    """Task 4: Custom exception."""

    def test_approval_timeout_error_is_exception(self):
        assert issubclass(ApprovalTimeoutError, Exception)
        e = ApprovalTimeoutError()
        assert isinstance(e, Exception)


class TestApprovalDeniedError:
    """Public error-class surface — listed in v0.6.0 CLAUDE.md + spec."""

    def test_approval_denied_error_is_exception(self):
        assert issubclass(ApprovalDeniedError, Exception)
        assert isinstance(ApprovalDeniedError("denied"), Exception)

    def test_approval_denied_error_re_exported_from_package(self):
        from apcore_cli import ApprovalDeniedError as ReExported

        assert ReExported is ApprovalDeniedError


class TestCheckApprovalRaisesTypedErrors:
    """D11-001: check_approval must raise typed exceptions (not sys.exit) so
    discovery.py exec_cmd's ``except Exception`` handler can flush the audit
    log before the process exits 46.

    SystemExit is a BaseException, not an Exception, so it bypasses the
    audit-flush handler — that is the bug this regression covers.
    """

    def test_non_tty_raises_approval_denied_error_not_systemexit(self, monkeypatch):
        """Non-TTY denial path: raise ApprovalDeniedError, never sys.exit."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.delenv("APCORE_CLI_AUTO_APPROVE", raising=False)
        m = _make_module(requires_approval=True)
        with pytest.raises(ApprovalDeniedError):
            check_approval(m, auto_approve=False)

    def test_non_tty_error_catchable_by_except_exception(self, monkeypatch):
        """Defense-in-depth: the raised error must be catchable by the
        ``except Exception`` handler in discovery.py exec_cmd.
        SystemExit (BaseException) would slip past this handler."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.delenv("APCORE_CLI_AUTO_APPROVE", raising=False)
        m = _make_module(requires_approval=True)
        caught = False
        try:
            check_approval(m, auto_approve=False)
        except Exception:
            caught = True
        assert caught, "ApprovalDeniedError must be a normal Exception subclass"

    def test_tty_user_denies_raises_approval_denied_error(self, monkeypatch):
        """TTY denial path: raise ApprovalDeniedError, never sys.exit."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.delenv("APCORE_CLI_AUTO_APPROVE", raising=False)
        m = _make_module(requires_approval=True)
        with patch("apcore_cli.approval.click.confirm", return_value=False), pytest.raises(ApprovalDeniedError):
            check_approval(m, auto_approve=False)

    def test_tty_timeout_raises_approval_timeout_error(self, monkeypatch):
        """TTY timeout path: raise ApprovalTimeoutError, never sys.exit."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.delenv("APCORE_CLI_AUTO_APPROVE", raising=False)
        m = _make_module(requires_approval=True)
        with (
            patch(
                "apcore_cli.approval.click.confirm",
                side_effect=ApprovalTimeoutError(),
            ),
            pytest.raises(ApprovalTimeoutError),
        ):
            check_approval(m, auto_approve=False)

    def test_env_var_invalid_raises_approval_denied_error(self, monkeypatch, capsys):
        """Env var set to non-'1' should warn then take the non-TTY denial path
        as a typed exception (not sys.exit). D10-009 cross-SDK parity: the
        warning emits on stderr, not via the Python logger.
        """
        monkeypatch.setenv("APCORE_CLI_AUTO_APPROVE", "true")
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        m = _make_module(requires_approval=True)
        with pytest.raises(ApprovalDeniedError):
            check_approval(m, auto_approve=False)
        captured = capsys.readouterr()
        assert "expected '1'" in captured.err

    def test_typed_errors_carry_code_attribute_for_error_code_map(self, monkeypatch):
        """The discovery.py exec_cmd handler maps ``e.code`` through
        _ERROR_CODE_MAP. ApprovalDeniedError must carry ``code='APPROVAL_DENIED'``
        and ApprovalTimeoutError ``code='APPROVAL_TIMEOUT'`` so they resolve to
        exit code 46.
        """
        from apcore_cli.cli import _ERROR_CODE_MAP

        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.delenv("APCORE_CLI_AUTO_APPROVE", raising=False)
        m = _make_module(requires_approval=True)
        try:
            check_approval(m, auto_approve=False)
        except ApprovalDeniedError as e:
            code = getattr(e, "code", None)
            assert code == "APPROVAL_DENIED"
            assert _ERROR_CODE_MAP.get(code) == 46
        else:
            pytest.fail("expected ApprovalDeniedError")


def test_validate_module_import_path():
    """D9-005: format_preflight_result and first_failed_exit_code now live in
    apcore_cli.validate (mirrors apcore-cli-rust/src/validate.rs split). The
    legacy import path ``from apcore_cli.cli import format_preflight_result``
    is preserved as a re-export shim for back-compat.
    """
    from apcore_cli.cli import _first_failed_exit_code as legacy_efc
    from apcore_cli.cli import format_preflight_result as legacy_fp
    from apcore_cli.validate import first_failed_exit_code as new_efc
    from apcore_cli.validate import format_preflight_result as new_fp

    # Same callable behind both names.
    assert legacy_fp is new_fp
    assert legacy_efc is new_efc
