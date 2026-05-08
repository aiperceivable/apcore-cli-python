"""Public-surface parity tests (audit D1-W1/W2/W3/W5).

These tests pin the package-root export surface so cross-SDK parity with
apcore-cli-typescript and apcore-cli-rust does not silently regress. Each
test corresponds to a finding from the 2026-05-08 audit.
"""

from __future__ import annotations

import apcore_cli


def test_apcli_subcommand_names_reexported() -> None:
    """D1-W1: ``APCLI_SUBCOMMAND_NAMES`` is re-exported at the package root.

    Rust re-exports it via ``pub use builtin_group::{APCLI_SUBCOMMAND_NAMES,
    RESERVED_GROUP_NAMES}`` (lib.rs); Python must expose the same set.
    """
    from apcore_cli import APCLI_SUBCOMMAND_NAMES

    assert isinstance(APCLI_SUBCOMMAND_NAMES, frozenset)
    assert "list" in APCLI_SUBCOMMAND_NAMES
    assert "describe" in APCLI_SUBCOMMAND_NAMES
    assert "APCLI_SUBCOMMAND_NAMES" in apcore_cli.__all__


def test_apcli_config_typed_dict_reexported() -> None:
    """D1-W2: ``ApcliConfig`` TypedDict is re-exported (parity with TS/Rust)."""
    from apcore_cli import ApcliConfig

    # TypedDict instances are dicts at runtime; verify the shape declaration.
    cfg: ApcliConfig = {"mode": "all", "include": ["health"]}
    assert cfg["mode"] == "all"
    assert "ApcliConfig" in apcore_cli.__all__


def test_register_config_namespace_helper_reexported() -> None:
    """D1-W3: ``register_config_namespace`` and ``DEFAULTS`` are public helpers.

    Mirrors apcore-cli-typescript's ``registerConfigNamespace`` /
    ``DEFAULTS`` exports and apcore-cli-rust's ``DEFAULTS`` const.
    """
    from apcore_cli import DEFAULTS, register_config_namespace

    assert callable(register_config_namespace)
    assert isinstance(DEFAULTS, dict)
    assert {"help_text_max_length", "approval_timeout", "group_depth"} <= set(DEFAULTS.keys())
    assert "register_config_namespace" in apcore_cli.__all__
    assert "DEFAULTS" in apcore_cli.__all__


def test_dispatcher_symbols_reexported() -> None:
    """D1-W5: core dispatcher embedder API is re-exported (parity with TS/Rust).

    Rust's ``lib.rs`` re-exports ``build_module_command``, ``collect_input``,
    ``validate_module_id``, ``set_audit_logger``, ``set_verbose_help``,
    ``set_docs_url``. Python must expose the same set so embedders do not
    have to import from private submodules.
    """
    from apcore_cli import (
        build_module_command,
        collect_input,
        set_audit_logger,
        set_docs_url,
        set_verbose_help,
        validate_module_id,
    )

    for name, obj in [
        ("build_module_command", build_module_command),
        ("collect_input", collect_input),
        ("validate_module_id", validate_module_id),
        ("set_audit_logger", set_audit_logger),
        ("set_verbose_help", set_verbose_help),
        ("set_docs_url", set_docs_url),
    ]:
        assert callable(obj), f"{name} should be callable"
        assert name in apcore_cli.__all__, f"{name} should be in __all__"
