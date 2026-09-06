"""Shared test fixtures for apcore-cli tests."""

import os

import pytest


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Provide a temporary directory for config file tests."""
    return tmp_path


@pytest.fixture
def clean_env(monkeypatch):
    """Remove all APCORE_ env vars to ensure test isolation."""
    for key in list(os.environ.keys()):
        if key.startswith("APCORE_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True, scope="session")
def _redirect_default_audit_log(tmp_path_factory):
    """Keep the suite out of the developer's real ``~/.apcore-cli/audit.jsonl``.

    ``create_cli`` constructs an ``AuditLogger()`` with no path, so every test
    that builds a CLI has always appended execution records to the real home
    directory. FE-14 §4.8 adds ACL decision records to the same sink, which
    makes an already-untidy side effect noisier. Redirecting the class default
    for the session is transparent — a test that passes an explicit ``path=``
    is unaffected — and leaves the file where it belongs in production.
    """
    from apcore_cli.security.audit import AuditLogger

    original = AuditLogger.DEFAULT_PATH
    AuditLogger.DEFAULT_PATH = tmp_path_factory.mktemp("apcore-cli-audit") / "audit.jsonl"
    yield
    AuditLogger.DEFAULT_PATH = original


@pytest.fixture(autouse=True)
def _clean_audit_logger():
    """Ensure global _audit_logger is reset after every test (LOW-2 fix)."""
    from apcore_cli.cli import set_audit_logger

    yield
    set_audit_logger(None)


@pytest.fixture(autouse=True)
def _clean_acl_globals():
    """Reset the FE-14 process-wide ACL state around every test.

    ``set_cli_identity`` (the ``--identity-id`` / ``--role`` flags) and
    ``set_cli_acl`` (the §6.2 warning and the §4.10 sandbox gate) are module-level
    like ``set_audit_logger``, so they leak across tests unless cleared.
    """
    from apcore_cli.acl_loader import set_cli_acl, set_cli_identity

    set_cli_identity(None)
    set_cli_acl(None)
    yield
    set_cli_identity(None)
    set_cli_acl(None)
