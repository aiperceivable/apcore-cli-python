"""Configuration resolver with 4-tier precedence (FE-07)."""

from __future__ import annotations

import logging
import os
from typing import Any

import yaml

logger = logging.getLogger("apcore_cli.config")


class ConfigResolver:
    """Resolves configuration values using 4-tier precedence:
    CLI flag > Environment variable > Config file > Default.
    """

    DEFAULTS: dict[str, Any] = {
        "extensions.root": "./extensions",
        "logging.level": "WARNING",
        "sandbox.enabled": False,
        "cli.stdin_buffer_limit": 10_485_760,  # 10 MB
        "cli.auto_approve": False,
    }

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

        # Tier 3: Config file
        if self._config_file is not None and key in self._config_file:
            return self._config_file[key]

        # Tier 4: Default
        return self.DEFAULTS.get(key)

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
