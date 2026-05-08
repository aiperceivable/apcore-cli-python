"""Built-in Command Group (FE-13).

Encapsulates visibility resolution and subcommand filtering for the
reserved ``apcli`` group. Instantiated once by :func:`create_cli` and
attached to the root Click group.

Shape mirrors :class:`apcore_cli.exposure.ExposureFilter`: private
initializer, named classmethod factories (``from_cli_config`` for Tier 1,
``from_yaml`` for Tier 3), and a small set of predicate methods
(``resolve_visibility``, ``is_subcommand_included``, ``is_group_visible``).

See the FE-13 feature spec (``../apcore-cli/docs/features/builtin-group.md``)
§4.2–4.7 for the authoritative semantics.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from typing import Any, Literal, TypedDict

logger = logging.getLogger("apcore_cli.builtin_group")

# ---------------------------------------------------------------------------
# Types & constants
# ---------------------------------------------------------------------------

#: Resolved visibility modes. ``"auto"`` is an internal sentinel — it is never
#: returned from :meth:`ApcliGroup.resolve_visibility` and is rejected when
#: supplied via user config (CliConfig or ``apcore.yaml``).
ApcliMode = Literal["auto", "all", "none", "include", "exclude"]

#: Resolved (non-sentinel) visibility modes.
ResolvedApcliMode = Literal["all", "none", "include", "exclude"]

#: Default name of the built-in command group. Overridable per :class:`ApcliGroup`
#: instance via the ``name`` constructor parameter or via
#: :func:`apcore_cli.create_cli`'s ``builtin_group_name`` kwarg. Downstream
#: branded CLIs that want their built-ins under a different namespace
#: (e.g. ``mycorp-cli admin health`` instead of ``mycorp-cli apcli health``)
#: should set the kwarg; standalone apcore-cli leaves the default.
DEFAULT_BUILTIN_GROUP_NAME: str = "apcli"

#: Set of group names reserved by apcore-cli when no rename is configured.
#: Enforced by :class:`apcore_cli.cli.GroupedModuleGroup` when building the
#: registry-driven command surface. Business modules whose alias/top-level/group
#: name collides with a reserved entry are rejected at import time with exit
#: code 2. When ``builtin_group_name`` is overridden, the live reserved set is
#: ``frozenset({apcli_group.name})`` and is applied per-instance — see
#: :attr:`GroupedModuleGroup._reserved_group_names`.
RESERVED_GROUP_NAMES: frozenset[str] = frozenset({DEFAULT_BUILTIN_GROUP_NAME})

_VALID_USER_MODES: frozenset[str] = frozenset({"all", "none", "include", "exclude"})


class ApcliConfig(TypedDict, total=False):
    """Typed shape of the ``apcli`` config block accepted by
    :meth:`ApcliGroup.from_cli_config` / :meth:`ApcliGroup.from_yaml`.

    Mirrors the ``ApcliConfig`` type in apcore-cli-typescript and the
    ``ApcliConfig`` struct in apcore-cli-rust so embedders have cross-SDK
    parity for static typing. Note that the runtime accepts ``bool | dict |
    None`` — the ``bool`` form short-circuits ``mode`` to ``"all"`` /
    ``"none"`` and the ``None`` form means "auto-detect"; only the dict form
    matches this TypedDict.
    """

    mode: ResolvedApcliMode
    include: list[str]
    exclude: list[str]
    disable_env: bool
    name: str

#: Canonical set of apcli subcommand names. Declarative mirror of the
#: registrar table in :func:`apcore_cli.factory._register_apcli_subcommands`.
#: Used by :meth:`ApcliGroup._normalize_list` to warn on unknown entries in
#: include/exclude lists (spec §7 error table / T-APCLI-25).
#:
#: Keep in sync with the factory TABLE if subcommands are added or removed.
APCLI_SUBCOMMAND_NAMES: frozenset[str] = frozenset(
    {
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
    }
)

_ENV_VAR = "APCORE_CLI_APCLI"

# Exit code for invalid CLI input (matches Click / errors.py convention).
_EXIT_INVALID_CLI_INPUT = 2


# ---------------------------------------------------------------------------
# ApcliGroup
# ---------------------------------------------------------------------------


class ApcliGroup:
    """Visibility configuration for the built-in ``apcli`` command group.

    Instantiated via :meth:`ApcliGroup.from_cli_config` (Tier 1) or
    :meth:`ApcliGroup.from_yaml` (Tier 3). The default ``__init__`` is
    considered private — callers should prefer the classmethod factories so
    the Tier-1-vs-Tier-3 precedence flag is set correctly.
    """

    __slots__ = (
        "_mode",
        "_include",
        "_exclude",
        "_disable_env",
        "_registry_injected",
        "_from_cli_config",
        "_name",
    )

    def __init__(
        self,
        mode: ApcliMode = "auto",
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        disable_env: bool = False,
        registry_injected: bool = False,
        from_cli_config: bool = False,
        name: str = DEFAULT_BUILTIN_GROUP_NAME,
    ) -> None:
        self._mode: ApcliMode = mode
        self._include: list[str] = list(include) if include else []
        self._exclude: list[str] = list(exclude) if exclude else []
        self._disable_env: bool = bool(disable_env)
        self._registry_injected: bool = bool(registry_injected)
        self._from_cli_config: bool = bool(from_cli_config)
        # Validation: name must be shell-safe (lowercase, alphanumeric + underscore +
        # hyphen) and non-empty. Mirrors the same regex used to validate business
        # module group names downstream (cli.py:_build_group_map).
        if not name or not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
            raise ValueError(
                f"builtin_group_name {name!r} must match /^[a-z][a-z0-9_-]*$/ "
                "(non-empty, lowercase, alphanumeric + '_' / '-', leading letter)."
            )
        self._name: str = name

    @property
    def name(self) -> str:
        """Resolved name for the built-in command group (default ``"apcli"``).

        Set via the ``name=`` constructor parameter or, indirectly, via
        :func:`apcore_cli.create_cli`'s ``builtin_group_name`` kwarg. Downstream
        branded CLIs use this to expose built-ins under a custom namespace
        without forking the SDK.
        """
        return self._name

    # ---- Factories ---------------------------------------------------------

    @classmethod
    def from_cli_config(
        cls,
        config: bool | dict[str, Any] | None,
        *,
        registry_injected: bool,
        name: str = DEFAULT_BUILTIN_GROUP_NAME,
    ) -> ApcliGroup:
        """Tier 1 constructor — value came from ``create_cli(apcli=...)``.

        A non-auto mode from this tier wins outright over the env var and
        ``apcore.yaml``. Boolean / dict / ``None`` are all accepted; other
        types raise :class:`TypeError` (the factory catches and exits 2).

        ``name`` is the resolved built-in-group name (default ``"apcli"``);
        forwarded from :func:`apcore_cli.create_cli`'s ``builtin_group_name``
        kwarg so downstream branded CLIs can rename the group.
        """
        return cls._build(config, registry_injected=registry_injected, from_cli_config=True, name=name)

    @classmethod
    def from_yaml(
        cls,
        config: Any,
        *,
        registry_injected: bool,
        name: str = DEFAULT_BUILTIN_GROUP_NAME,
    ) -> ApcliGroup:
        """Tier 3 constructor — value came from ``apcore.yaml``.

        Env var (Tier 2) may override the yaml-supplied mode when
        ``disable_env`` is not set. On invalid input, logs a WARNING and
        falls back to auto-detect rather than raising.
        """
        # Coerce yaml-loaded values. Anything that isn't bool/dict/None
        # becomes "auto" with a warning — yaml is often user-edited, so we
        # log but do not crash for non-actionable shapes.
        if config is not None and not isinstance(config, bool | dict):
            logger.warning(
                "apcore.yaml 'apcli:' must be a bool, mapping, or null; got %s. Using auto-detect.",
                type(config).__name__,
            )
            config = None
        return cls._build(config, registry_injected=registry_injected, from_cli_config=False, name=name)

    @classmethod
    def try_from_yaml(
        cls,
        config: Any,
        *,
        registry_injected: bool,
    ) -> tuple[ApcliGroup, None] | tuple[None, str]:
        """Non-panicking Tier 3 constructor. Returns ``(instance, None)`` on
        success or ``(None, error_message)`` on validation failure.

        Use this in programmatic contexts where raising/exiting is unwanted.
        ``from_yaml()`` is the lenient shim that logs + falls back;
        ``try_from_yaml()`` surfaces the error so callers can decide.
        """
        if config is not None and not isinstance(config, bool | dict):
            return None, (f"apcore.yaml 'apcli:' must be a bool, mapping, or null; got {type(config).__name__}")
        if isinstance(config, dict):
            raw_mode = config.get("mode")
            if raw_mode is not None and (not isinstance(raw_mode, str) or raw_mode not in _VALID_USER_MODES):
                return None, f"Invalid apcli mode: '{raw_mode}'. Must be one of: all, none, include, exclude."
        return cls._build(config, registry_injected=registry_injected, from_cli_config=False), None

    # ---- Internal builder --------------------------------------------------

    @classmethod
    def _build(
        cls,
        config: bool | dict[str, Any] | None,
        *,
        registry_injected: bool,
        from_cli_config: bool,
        name: str = DEFAULT_BUILTIN_GROUP_NAME,
    ) -> ApcliGroup:
        if config is True:
            return cls(
                mode="all",
                disable_env=False,
                registry_injected=registry_injected,
                from_cli_config=from_cli_config,
                name=name,
            )
        if config is False:
            return cls(
                mode="none",
                disable_env=False,
                registry_injected=registry_injected,
                from_cli_config=from_cli_config,
                name=name,
            )
        if config is None:
            # Auto-detect (internal sentinel; never returned from resolve).
            return cls(
                mode="auto",
                disable_env=False,
                registry_injected=registry_injected,
                from_cli_config=from_cli_config,
                name=name,
            )
        if not isinstance(config, dict):
            # create_cli() ultimately raises TypeError for programmatic
            # callers; the factory wrapper prints the error and exits 2.
            raise TypeError(f"apcli: expected bool, dict, ApcliGroup, or None; got {type(config).__name__}")

        # Mode validation — rejects "auto" (internal sentinel) and unknowns.
        raw_mode = config.get("mode")
        if raw_mode is None:
            mode: ApcliMode = "auto"
        elif not isinstance(raw_mode, str):
            sys.stderr.write(
                f"Error: apcli.mode must be a string; got {type(raw_mode).__name__}. "
                "Expected one of all|none|include|exclude.\n"
            )
            sys.exit(_EXIT_INVALID_CLI_INPUT)
        elif raw_mode not in _VALID_USER_MODES:
            sys.stderr.write(f"Error: Invalid apcli mode: '{raw_mode}'. Must be one of: all, none, include, exclude.\n")
            sys.exit(_EXIT_INVALID_CLI_INPUT)
        else:
            mode = raw_mode  # type: ignore[assignment]

        include = cls._normalize_list(config.get("include"), "include")
        exclude = cls._normalize_list(config.get("exclude"), "exclude")

        # disable_env — accept both snake_case (apcore.yaml + Python) and
        # camelCase (JS/TS object literals passed through dict() coercion in
        # cross-language bridge tests). Must be boolean; warn + treat as
        # false otherwise. Per spec §4.2: "Non-boolean value: log WARNING,
        # treat as false."
        raw_disable = config.get("disable_env")
        if raw_disable is None:
            raw_disable = config.get("disableEnv")
        if raw_disable is None:
            disable_env = False
        elif isinstance(raw_disable, bool):
            disable_env = raw_disable
        else:
            logger.warning(
                "apcli.disable_env must be boolean; got %s. Treating as false.",
                type(raw_disable).__name__,
            )
            disable_env = False

        return cls(
            mode=mode,
            include=include,
            exclude=exclude,
            disable_env=disable_env,
            registry_injected=registry_injected,
            from_cli_config=from_cli_config,
            name=name,
        )

    @staticmethod
    def _normalize_list(raw: Any, label: str) -> list[str]:
        """Normalize an include/exclude list.

        Unknown-but-well-formed entries emit a WARNING (spec §7 error table,
        T-APCLI-25) but are retained in the returned list for forward
        compatibility — if apcore-cli later adds a subcommand named ``foo``,
        existing configs continue to work. At runtime, unknown names simply
        never match any registered subcommand.
        """
        if raw is None:
            return []
        if not isinstance(raw, list):
            logger.warning(
                "apcli.%s must be a list; got %s. Ignoring.",
                label,
                type(raw).__name__,
            )
            return []
        out: list[str] = []
        for entry in raw:
            if isinstance(entry, str) and entry:
                if entry not in APCLI_SUBCOMMAND_NAMES:
                    logger.warning(
                        "Unknown apcli subcommand '%s' in %s list — ignoring.",
                        entry,
                        label,
                    )
                out.append(entry)
            else:
                logger.warning(
                    "apcli.%s contains non-string entry; skipping.",
                    label,
                )
        return out

    # ---- Public predicates -------------------------------------------------

    def resolve_visibility(self) -> ResolvedApcliMode:
        """Return the effective visibility mode after applying tier precedence.

        Never returns ``"auto"`` — the sentinel collapses via steps 3/4 below.

        Tier order (spec §4.4):
          1. ``CliConfig`` non-auto wins outright.
          2. ``APCORE_CLI_APCLI`` env var (unless sealed by ``disable_env``).
          3. ``apcore.yaml`` non-auto.
          4. Auto-detect from ``registry_injected``.
        """
        # Tier 1 — CliConfig non-auto.
        if self._from_cli_config and self._mode != "auto":
            return self._mode  # type: ignore[return-value]

        # Tier 2 — env var (unless sealed).
        if not self._disable_env:
            env_mode = self._parse_env(os.environ.get(_ENV_VAR))
            if env_mode is not None:
                return env_mode

        # Tier 3 — yaml non-auto.
        if self._mode != "auto":
            return self._mode  # type: ignore[return-value]

        # Tier 4 — auto-detect.
        return "none" if self._registry_injected else "all"

    def is_subcommand_included(self, subcommand: str) -> bool:
        """True if the subcommand passes the include/exclude filter.

        Only meaningful when :meth:`resolve_visibility` returns
        ``"include"`` or ``"exclude"``. Raises ``AssertionError`` otherwise
        — dispatchers under ``"all"`` / ``"none"`` MUST bypass this method
        and register unconditionally (spec §4.6).
        """
        mode = self.resolve_visibility()
        if mode == "include":
            return subcommand in self._include
        if mode == "exclude":
            return subcommand not in self._exclude
        raise AssertionError(f"is_subcommand_included called under mode='{mode}'; caller should bypass.")

    def is_group_visible(self) -> bool:
        """True if the ``apcli`` group itself should appear in root ``--help``."""
        return self.resolve_visibility() != "none"

    # ---- Env parser (Tier 2) ----------------------------------------------

    @staticmethod
    def _parse_env(raw: str | None) -> ResolvedApcliMode | None:
        """Parse ``APCORE_CLI_APCLI``. Case-insensitive.

        - ``show`` / ``1`` / ``true`` → ``"all"``
        - ``hide`` / ``0`` / ``false`` → ``"none"``
        - Empty / unset → ``None``
        - Anything else → warn and return ``None``
        """
        if raw is None or raw == "":
            return None
        normalized = raw.lower()
        if normalized in ("show", "1", "true"):
            return "all"
        if normalized in ("hide", "0", "false"):
            return "none"
        logger.warning(
            "Unknown %s value '%s', ignoring. Expected: show, hide, 1, 0, true, false.",
            _ENV_VAR,
            raw,
        )
        return None


__all__ = [
    "APCLI_SUBCOMMAND_NAMES",
    "ApcliConfig",
    "ApcliGroup",
    "ApcliMode",
    "DEFAULT_BUILTIN_GROUP_NAME",
    "RESERVED_GROUP_NAMES",
    "ResolvedApcliMode",
]
