"""Configuration resolver with 4-tier precedence (FE-07)."""

from __future__ import annotations

import logging
import os
from typing import Any

import yaml

logger = logging.getLogger("apcore_cli.config")

# Module-level Config Bus namespace defaults for the apcore-cli namespace.
# Mirrors the `DEFAULTS` export shipped by apcore-cli-typescript and the
# `DEFAULTS` const in apcore-cli-rust so embedders have cross-SDK parity.
DEFAULTS: dict[str, Any] = {
    "help_text_max_length": 1000,
    "approval_timeout": 60,
    "group_depth": 1,
}


def register_config_namespace() -> bool:
    """Register the apcore-cli Config Bus namespace with apcore (>= 0.15.0).

    Public helper mirroring apcore-cli-typescript's ``registerConfigNamespace``
    and apcore-cli-rust's equivalent registration call site so embedders can
    invoke registration explicitly. Returns ``True`` when registration
    succeeded.

    ``apcore`` is a required runtime dependency
    (``pyproject.toml`` pins ``apcore>=0.21.0``) so the import is performed
    locally rather than guarded — a missing or too-old apcore is a hard
    install error, not a silent no-op. The prior graceful-fallback path
    was removed in the 6.2 fix.
    """
    from apcore import Config

    Config.register_namespace(
        name="apcore-cli",
        schema=None,
        env_prefix="APCORE_CLI",
        defaults=DEFAULTS,
    )
    return True


class ConfigResolver:
    """Resolves configuration values using 4-tier precedence:
    CLI flag > Environment variable > Config file > Default.
    """

    # Namespace key -> legacy key mapping for backward compatibility
    _NAMESPACE_TO_LEGACY: dict[str, str] = {
        "apcore-cli.stdin_buffer_limit": "cli.stdin_buffer_limit",
        "apcore-cli.auto_approve": "cli.auto_approve",
        "apcore-cli.help_text_max_length": "cli.help_text_max_length",
        "apcore-cli.logging_level": "logging.level",
    }
    _LEGACY_TO_NAMESPACE: dict[str, str] = {v: k for k, v in _NAMESPACE_TO_LEGACY.items()}

    DEFAULTS: dict[str, Any] = {
        "extensions.root": "./extensions",
        "logging.level": "WARNING",
        "cli.help_text_max_length": 1000,
        # FE-11 (v0.6.0)
        "cli.approval_timeout": 60,
        "cli.strategy": "standard",
        "cli.group_depth": 1,
        # ACL governance (FE-14). `acl.root` is an apcore-owned key, so its
        # env var follows the apcore convention (APCORE_ACL_ROOT) rather than
        # APCORE_CLI_*. The default matches apcore's own
        # `Config.get_default("acl.root")`; a missing path attaches nothing.
        "acl.root": "./acl",
        # FE-14 §4.8 audit wiring. Both keys are apcore-owned too, so they
        # follow the same convention (`APCORE_ACL_AUDIT_*`). `include_denied`
        # takes its meaning from apcore's own `schemas/acl-config.schema.json`
        # ("Whether to log denied access attempts", default true) rather than
        # forking a similarly-named key with inverted semantics: `false`
        # suppresses **deny** entries only, and allow entries keep being
        # written.
        "acl.audit.enabled": True,
        "acl.audit.include_denied": True,
        # Exposure filtering (FE-12)
        "expose.mode": "all",
        "expose.include": [],
        "expose.exclude": [],
    }
    # Audit D9 (config cleanup): the unused entries `sandbox.enabled`,
    # `cli.stdin_buffer_limit`, `cli.auto_approve`, and the four
    # `apcore-cli.*` namespace aliases were removed in v0.6.x. None of
    # them were ever read by `resolve()` at runtime — sandbox/auto-approve
    # come from CLI flags, the stdin buffer is hard-coded, and the
    # namespace aliases are registered separately via the Config Bus
    # `register_namespace()` call in `apcore_cli/__init__.py`.

    def __init__(
        self,
        cli_flags: dict[str, Any] | None = None,
        config_path: str = "apcore.yaml",
    ) -> None:
        self._cli_flags = cli_flags or {}
        self._config_path = config_path
        self._config_file: dict[str, Any] | None = self._load_config_file()

    def resolve(
        self,
        key: str,
        cli_flag: str | None = None,
        env_var: str | None = None,
    ) -> Any:
        """Resolve a configuration value using 4-tier precedence."""
        # Tier 1: CLI flag
        if cli_flag is not None and cli_flag in self._cli_flags:
            value = self._cli_flags[cli_flag]
            if value is not None:
                return value

        # Tier 2: Environment variable
        if env_var is not None:
            env_value = os.environ.get(env_var)
            if env_value is not None and env_value != "":
                return env_value

        # Tier 3: Config file (try both namespace and legacy keys)
        if self._config_file is not None:
            if key in self._config_file:
                return self._config_file[key]
            alt_key = self._NAMESPACE_TO_LEGACY.get(key) or self._LEGACY_TO_NAMESPACE.get(key)
            if alt_key and alt_key in self._config_file:
                return self._config_file[alt_key]

        # Tier 4: Default
        return self.DEFAULTS.get(key)

    def resolve_object(self, key: str) -> Any:
        """Return the subtree rooted at ``key`` from the on-disk config file.

        Unlike :meth:`resolve`, this is for non-leaf keys that may evaluate to
        a scalar, a bool, ``None``, or a nested mapping — e.g. the FE-13
        ``apcli:`` block which can be ``true`` / ``false`` / ``null`` / object.

        Lookup semantics:
          - Scalar / bool leaf at ``<key>``: returns the value as-is.
          - Flattened subtree under ``<key>.*``: reconstructed into a
            one-level dict whose keys drop the ``<key>.`` prefix.
          - Key absent everywhere: returns ``None``.
        """
        if self._config_file is None:
            return None
        if key in self._config_file:
            return self._config_file[key]
        prefix = f"{key}."
        subtree = {k[len(prefix) :]: v for k, v in self._config_file.items() if k.startswith(prefix)}
        return subtree or None

    def _load_config_file(self) -> dict[str, Any] | None:
        """Load and flatten a YAML config file."""
        try:
            with open(self._config_path) as f:
                config = yaml.safe_load(f)
        except FileNotFoundError:
            return None
        except yaml.YAMLError:
            logger.warning(
                "Configuration file '%s' is malformed, using defaults.",
                self._config_path,
            )
            return None
        except OSError as e:
            # Cross-SDK parity: TypeScript/Rust both treat every non-"missing"
            # read error (permission denied, path is a directory, a symlink
            # loop, ...) as "malformed, use defaults" rather than propagating
            # it — ConfigResolver.__init__ has a documented "no errors raised"
            # contract. FileNotFoundError is caught above (silently, no
            # warning — "no config file" is the common, expected case); every
            # other OSError is unexpected enough to warn about, mirroring the
            # yaml.YAMLError branch immediately above.
            logger.warning(
                "Configuration file '%s' could not be read (%s), using defaults.",
                self._config_path,
                e,
            )
            return None

        if not isinstance(config, dict):
            logger.warning(
                "Configuration file '%s' is malformed, using defaults.",
                self._config_path,
            )
            return None

        return self._flatten_dict(config)

    def _flatten_dict(self, d: dict, prefix: str = "") -> dict[str, Any]:
        """Flatten nested dict to dot-notation keys."""
        result: dict[str, Any] = {}
        for key, value in d.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                result.update(self._flatten_dict(value, full_key))
            else:
                result[full_key] = value
        return result
