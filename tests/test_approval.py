"""Tests for Approval Gate (FE-03)."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from apcore_cli.approval import ApprovalTimeoutError, check_approval


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

    def test_env_var_not_one_warns(self, monkeypatch, caplog):
        monkeypatch.setenv("APCORE_CLI_AUTO_APPROVE", "true")
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        m = _make_module(requires_approval=True)
        with caplog.at_level(logging.WARNING, logger="apcore_cli.approval"), pytest.raises(SystemExit) as exc_info:
            check_approval(m, auto_approve=False)
        assert exc_info.value.code == 46
        assert "expected '1'" in caplog.text

    def test_yes_flag_priority_over_env(self, monkeypatch, caplog):
        monkeypatch.setenv("APCORE_CLI_AUTO_APPROVE", "1")
        m = _make_module(requires_approval=True)
        with caplog.at_level(logging.INFO, logger="apcore_cli.approval"):
            check_approval(m, auto_approve=True)
        assert "bypassed via --yes flag" in caplog.text


class TestNonTTYRejection:
    """Task 2: Non-TTY rejection."""

    def test_non_tty_no_bypass_exits_46(self, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.delenv("APCORE_CLI_AUTO_APPROVE", raising=False)
        m = _make_module(requires_approval=True)
        with pytest.raises(SystemExit) as exc_info:
            check_approval(m, auto_approve=False)
        assert exc_info.value.code == 46

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
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.delenv("APCORE_CLI_AUTO_APPROVE", raising=False)
        m = _make_module(requires_approval=True)
        with patch("apcore_cli.approval.click.confirm", return_value=False), pytest.raises(SystemExit) as exc_info:
            check_approval(m, auto_approve=False)
        assert exc_info.value.code == 46

    def test_tty_timeout(self, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.delenv("APCORE_CLI_AUTO_APPROVE", raising=False)
        m = _make_module(requires_approval=True)
        with (
            patch(
                "apcore_cli.approval.click.confirm",
                side_effect=ApprovalTimeoutError(),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            check_approval(m, auto_approve=False)
        assert exc_info.value.code == 46

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
