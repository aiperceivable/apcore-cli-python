"""apcore-cli factory — `create_cli` lives here so it can be imported as a
library without pulling in `__main__`'s entry-point semantics (FE-01).

This module was extracted from `__main__.py` per audit finding D9 (parallel_impl)
so that downstream projects can `from apcore_cli import create_cli` (or
`from apcore_cli.factory import create_cli`) without importing the binary
entry-point script module.
"""

from __future__ import annotations

import logging
import os
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _get_version
from typing import Any

import click

from apcore_cli.builtin_group import ApcliGroup
from apcore_cli.cli import GroupedModuleGroup, set_audit_logger, set_verbose_help
from apcore_cli.config import ConfigResolver
from apcore_cli.discovery import (
    register_describe_command,
    register_exec_command,
    register_list_command,
    register_validate_command,
)
from apcore_cli.exposure import ExposureFilter
from apcore_cli.init_cmd import register_init_command
from apcore_cli.security.audit import AuditLogger
from apcore_cli.shell import (
    configure_man_help,
    register_completion_command,
)
from apcore_cli.strategy import register_pipeline_command
from apcore_cli.system_cmd import (
    _system_modules_available,
    register_config_command,
    register_disable_command,
    register_enable_command,
    register_health_command,
    register_reload_command,
    register_usage_command,
)

try:
    __version__ = _get_version("apcore-cli")
except PackageNotFoundError:
    __version__ = "unknown"

logger = logging.getLogger("apcore_cli")

EXIT_CONFIG_NOT_FOUND = 47


def _has_verbose_flag(argv: list[str] | None = None) -> bool:
    """Check if --verbose is present in argv (pre-parse, before Click)."""
    args = argv if argv is not None else sys.argv[1:]
    return "--verbose" in args


def create_cli(
    extensions_dir: str | None = None,
    prog_name: str | None = None,
    commands_dir: str | None = None,
    binding_path: str | None = None,
    registry: Any | None = None,
    executor: Any | None = None,
    extra_commands: list[Any] | None = None,
    app: Any | None = None,
    expose: dict | ExposureFilter | None = None,
    apcli: bool | dict | ApcliGroup | None = None,
    allowed_prefixes: list[str] | None = None,
    version: str | None = None,
    description: str | None = None,
    builtin_group_name: str = "apcli",
) -> click.Group:
    """Create the CLI application.

    Args:
        extensions_dir: Override for extensions directory.
                        When None, resolves via ConfigResolver (env/file/default).
        prog_name: Name shown in help text and version output.
                   Defaults to the basename of sys.argv[0], so downstream projects
                   that install their own entry-point script get the correct name
                   automatically (e.g. ``mycli`` instead of ``apcore-cli``).
        commands_dir: Directory containing convention-based modules.
                      When set, scans for plain-function modules and registers
                      them via ConventionScanner (requires apcore-toolkit).
        binding_path: Path to binding.yaml file or directory for display resolution.
                      When set, applies DisplayResolver to convention-scanned modules
                      (requires apcore-toolkit).
        registry: Pre-populated apcore Registry instance. When provided, skips
                  filesystem discovery entirely. Useful for frameworks that register
                  modules at runtime (e.g. apflow's bridge).
        executor: Pre-built apcore Executor instance. When provided alongside
                  registry, skips Executor construction. If omitted but registry
                  is provided, an Executor is built from the given registry.
        extra_commands: Extra Click commands to add to the CLI root (FE-11 §3.11).
                        Names must not collide with the resolved built-in-group
                        name (default 'apcli'; see ``builtin_group_name``) or any
                        existing top-level command. Collisions with deprecation
                        shims are detected and raise ValueError. Note: BUILTIN_COMMANDS
                        was retired in v0.7.0; see cli.py for the retirement notice.
        app: APCore unified client (apcore >= 0.18.0). Mutually exclusive with
             registry/executor. When provided, registry and executor are extracted
             from app.registry and app.executor. Filesystem discovery is skipped
             if app.registry already has registered modules; otherwise discovery
             proceeds into app.registry. Note: ext_dir validation still runs when
             the app registry is empty (discovery fallthrough path).
        expose: Module exposure filter (FE-12). Accepts an ExposureFilter instance
                or a dict that ExposureFilter.from_config can parse.
        allowed_prefixes: Optional allowlist of module-path prefixes forwarded
                          to :meth:`apcore_toolkit.RegistryWriter.write` when
                          registering convention-scanned or binding-loaded
                          modules. When set, ``resolve_target`` rejects any
                          ``target:`` path outside the listed prefixes
                          *before* calling ``importlib.import_module`` —
                          mitigates arbitrary-code-execution via forged
                          binding YAML (e.g. ``target: "os:system"``).
                          Mirrors the TypeScript SDK's ``allowedPrefixes``
                          option. Also settable from the CLI via the
                          repeatable ``--allowed-prefix`` flag (standalone
                          mode only).
        apcli: Built-in ``apcli`` group configuration (FE-13). Accepts:

               * ``True`` / ``False`` — shorthand for ``{mode: "all"}`` /
                 ``{mode: "none"}``.
               * A dict matching the ``ApcliConfig`` schema
                 (``{"mode": "...", "include": [...], "exclude": [...],
                 "disable_env": bool}``).
               * A pre-built :class:`~apcore_cli.builtin_group.ApcliGroup`
                 instance (Tier 1 override; bypasses env var + yaml).
               * ``None`` — falls back to ``apcore.yaml``'s ``apcli:`` block,
                 then ``APCORE_CLI_APCLI`` env var, then auto-detect
                 (standalone → visible, embedded → hidden).
        builtin_group_name: Override the name of the built-in command group
                            (default ``"apcli"``). Downstream branded CLIs
                            that want their built-ins under a custom
                            namespace (e.g. ``mycorp-cli admin health``)
                            pass a different value here. Must match
                            ``/^[a-z][a-z0-9_-]*$/`` — non-empty, lowercase,
                            alphanumeric + ``_`` / ``-``. Invalid values
                            cause exit code 2. Note: env var
                            ``APCORE_CLI_APCLI`` and config keys ``apcli.*``
                            remain stable regardless of this rename — they
                            are apcore-cli-internal toggles, not user-facing.
                            Mirrors TS ``createCli({builtinGroupName})``.
                            Cross-SDK parity: 2026-05-08.
    """
    if app is not None and (registry is not None or executor is not None):
        raise ValueError("app is mutually exclusive with registry/executor")

    # FE-13 FR-13-13: lock in "registry was caller-injected" BEFORE filesystem
    # discovery may assign `registry` to a freshly constructed Registry. This
    # drives both the auto-detect default (embedded → apcli hidden) and the
    # gating of the --extensions-dir/--commands-dir/--binding root flags.
    registry_injected = registry is not None or app is not None

    if app is not None:
        registry = app.registry
        executor = app.executor

    if prog_name is None:
        prog_name = os.path.basename(sys.argv[0]) or "apcore-cli"

    # Pre-parse --verbose before Click runs so build_module_command knows
    # whether to hide built-in options.
    verbose = _has_verbose_flag()
    set_verbose_help(verbose)

    # Resolve CLI log level (3-tier precedence, evaluated before Click runs):
    #   APCORE_CLI_LOGGING_LEVEL (CLI-specific) > APCORE_LOGGING_LEVEL (global) > WARNING
    # The --log-level flag (parsed later) can further override at runtime.
    _cli_level_str = os.environ.get("APCORE_CLI_LOGGING_LEVEL", "").upper()
    _global_level_str = os.environ.get("APCORE_LOGGING_LEVEL", "").upper()
    _active_level_str = _cli_level_str or _global_level_str
    _default_level = getattr(logging, _active_level_str, logging.WARNING) if _active_level_str else logging.WARNING
    logging.basicConfig(level=_default_level, format="%(levelname)s: %(message)s")
    # basicConfig is a no-op if handlers already exist; always set the root level explicitly.
    logging.getLogger().setLevel(_default_level)
    # Silence noisy upstream apcore loggers unless the user requests verbose output.
    # Always set explicitly so the level is deterministic regardless of prior state.
    apcore_level = _default_level if _default_level <= logging.INFO else logging.ERROR
    logging.getLogger("apcore").setLevel(apcore_level)

    config = ConfigResolver()

    if extensions_dir is not None:
        ext_dir = extensions_dir
    else:
        ext_dir = config.resolve(
            "extensions.root",
            cli_flag="--extensions-dir",
            env_var="APCORE_EXTENSIONS_ROOT",
        )

    help_text_max_length = config.resolve(
        "cli.help_text_max_length",
        env_var="APCORE_CLI_HELP_TEXT_MAX_LENGTH",
    )
    try:
        help_text_max_length = int(help_text_max_length)
    except (TypeError, ValueError):
        help_text_max_length = 1000

    if executor is not None and registry is None:
        raise ValueError("executor requires registry — pass both or neither")

    if registry is not None:
        # Pre-populated registry provided.
        # When called via app=, skip discovery only if the registry already has modules;
        # otherwise fall through to filesystem discovery into the provided registry.
        _app_registry_has_modules = (app is not None) and len(list(registry.list())) > 0
        _skip_discovery = (app is None) or _app_registry_has_modules

        if _skip_discovery:
            # Skip filesystem discovery entirely.
            try:
                from apcore import Executor as _Executor

                if executor is None:
                    executor = _Executor(registry)
                logger.info("Using pre-populated registry (%d modules).", len(list(registry.list())))
            except Exception as e:
                click.echo(
                    f"Error: Failed to initialize executor from provided registry: {e}",
                    err=True,
                )
                sys.exit(EXIT_CONFIG_NOT_FOUND)
        else:
            # app= was provided but registry is empty — run discovery into app.registry.
            ext_dir_missing = not os.path.exists(ext_dir)
            ext_dir_unreadable = not ext_dir_missing and not os.access(ext_dir, os.R_OK)

            if ext_dir_missing:
                click.echo(
                    f"Error: Extensions directory not found: '{ext_dir}'."
                    " Set APCORE_EXTENSIONS_ROOT or verify the path.",
                    err=True,
                )
                sys.exit(EXIT_CONFIG_NOT_FOUND)

            if ext_dir_unreadable:
                click.echo(
                    f"Error: Cannot read extensions directory: '{ext_dir}'. Check permissions.",
                    err=True,
                )
                sys.exit(EXIT_CONFIG_NOT_FOUND)

            try:
                logger.debug("Loading extensions from %s (into app.registry)", ext_dir)
                count = registry.discover()
                logger.info("Initialized apcore-cli with %d modules (via app.registry).", count)
            except Exception as e:
                logger.warning("Discovery failed: %s", e)

            try:
                from apcore import Executor as _Executor

                if executor is None:
                    executor = _Executor(registry)
            except Exception as e:
                click.echo(f"Error: Failed to initialize executor from app.registry: {e}", err=True)
                sys.exit(EXIT_CONFIG_NOT_FOUND)
    else:
        # Standard path: discover modules from filesystem.
        ext_dir_missing = not os.path.exists(ext_dir)
        ext_dir_unreadable = not ext_dir_missing and not os.access(ext_dir, os.R_OK)

        if ext_dir_missing:
            click.echo(
                f"Error: Extensions directory not found: '{ext_dir}'. Set APCORE_EXTENSIONS_ROOT or verify the path.",
                err=True,
            )
            sys.exit(EXIT_CONFIG_NOT_FOUND)

        if ext_dir_unreadable:
            click.echo(
                f"Error: Cannot read extensions directory: '{ext_dir}'. Check permissions.",
                err=True,
            )
            sys.exit(EXIT_CONFIG_NOT_FOUND)

        try:
            from apcore import Executor as _Executor
            from apcore import Registry as _Registry

            registry = _Registry(extensions_dir=ext_dir)
            try:
                logger.debug("Loading extensions from %s", ext_dir)
                count = registry.discover()
                logger.info("Initialized apcore-cli with %d modules.", count)
            except Exception as e:
                logger.warning("Discovery failed: %s", e)

            # Toolkit integration: convention scanner + binding loader.
            # Split into a dedicated helper so the three SDK entry points
            # (ConventionScanner, BindingLoader, DisplayResolver) can be
            # composed cleanly — this brings the Python CLI to parity with
            # ``../apcore-cli-typescript/src/main.ts::loadBindingDisplayOverlay``
            # which wires BindingLoader through when only ``binding_path`` is
            # supplied (previously a no-op in the Python CLI).
            _apply_toolkit_integration(
                registry,
                commands_dir=commands_dir,
                binding_path=binding_path,
                allowed_prefixes=allowed_prefixes,
            )

            executor = _Executor(registry)
        except Exception as e:
            click.echo(f"Error: Failed to initialize registry: {e}", err=True)
            sys.exit(EXIT_CONFIG_NOT_FOUND)

    # Initialize audit logger
    try:
        audit_logger = AuditLogger()
        set_audit_logger(audit_logger)
    except Exception as e:
        logger.warning("Failed to initialize audit logger: %s", e)

    # Wire CliApprovalHandler to Executor (FE-11 §3.5)
    try:
        import contextlib

        from apcore_cli.approval import CliApprovalHandler

        approval_timeout = 60
        with contextlib.suppress(TypeError, ValueError):
            approval_timeout = int(config.resolve("cli.approval_timeout", env_var="APCORE_CLI_APPROVAL_TIMEOUT") or 60)
        handler = CliApprovalHandler(auto_approve=False, timeout=approval_timeout)
        if hasattr(executor, "set_approval_handler"):
            executor.set_approval_handler(handler)
            logger.debug("CliApprovalHandler wired to Executor (timeout=%ds).", approval_timeout)
    except Exception as e:
        # Surface at WARNING so a misconfigured apcore runtime (e.g., signature
        # drift in set_approval_handler) doesn't silently skip the gate — modules
        # with requires_approval=True would otherwise execute with no prompt.
        logger.warning("Failed to wire CliApprovalHandler: %s", e)

    # Build exposure filter (FE-12)
    if isinstance(expose, ExposureFilter):
        exposure_filter = expose
    elif isinstance(expose, dict):
        exposure_filter = ExposureFilter.from_config({"expose": expose})
    else:
        expose_mode = config.resolve("expose.mode", env_var="APCORE_CLI_EXPOSE_MODE")
        expose_include = config.resolve("expose.include")
        expose_exclude = config.resolve("expose.exclude")
        if expose_mode and expose_mode != "all":
            exposure_filter = ExposureFilter.from_config(
                {
                    "expose": {
                        "mode": expose_mode,
                        "include": expose_include or [],
                        "exclude": expose_exclude or [],
                    }
                }
            )
        else:
            exposure_filter = ExposureFilter()

    # Build ApcliGroup (FE-13 §4.8) via 3-source dispatch:
    #   1) pre-built ApcliGroup instance (pass-through),
    #   2) CliConfig bool/dict (Tier 1 — wins over env + yaml),
    #   3) apcore.yaml via ConfigResolver.resolve_object (Tier 3).
    try:
        if isinstance(apcli, ApcliGroup):
            # Caller supplied a pre-built ApcliGroup. Honour its name verbatim;
            # if `builtin_group_name` was also passed and disagrees, surface the
            # conflict rather than silently picking a winner — both came from
            # the same caller and the kwarg is redundant in this branch.
            if apcli.name != builtin_group_name and builtin_group_name != "apcli":
                raise TypeError(
                    f"builtin_group_name={builtin_group_name!r} conflicts with the "
                    f"name on the supplied ApcliGroup ({apcli.name!r}). Pass only one."
                )
            apcli_cfg = apcli
        elif isinstance(apcli, bool | dict):
            apcli_cfg = ApcliGroup.from_cli_config(
                apcli,
                registry_injected=registry_injected,
                name=builtin_group_name,
            )
        elif apcli is None:
            yaml_val = config.resolve_object("apcli")
            apcli_cfg = ApcliGroup.from_yaml(
                yaml_val,
                registry_injected=registry_injected,
                name=builtin_group_name,
            )
        else:
            raise TypeError(f"apcli: expected bool, dict, ApcliGroup, or None; got {type(apcli).__name__}")
    except TypeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)
    except ValueError as e:
        # ApcliGroup name validation rejected `builtin_group_name`.
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)

    # Issue #19: when used as a library, do not leak "apcore" framing into
    # the host CLI's --help. Default to a neutral, prog_name-derived string.
    _resolved_help = description if description is not None else f"{prog_name} CLI"

    @click.group(
        cls=GroupedModuleGroup,
        registry=registry,
        executor=executor,
        help_text_max_length=help_text_max_length,
        exposure_filter=exposure_filter,
        extensions_root=ext_dir,
        name=prog_name,
        help=_resolved_help,
    )
    @click.option(
        "--log-level",
        default=None,
        type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
        help="Log verbosity. Overrides APCORE_CLI_LOGGING_LEVEL and APCORE_LOGGING_LEVEL env vars.",
    )
    @click.option(
        "--verbose",
        "verbose_help",
        is_flag=True,
        default=False,
        help="Show all options in help output (including built-in options).",
    )
    @click.pass_context
    def cli(
        ctx: click.Context,
        log_level: str | None = None,
        verbose_help: bool = False,
        **_discovery_opts: Any,  # --extensions-dir/--commands-dir/--binding when standalone
    ) -> None:
        if log_level is not None:
            level = getattr(logging, log_level.upper(), logging.WARNING)
            logging.getLogger().setLevel(level)
            apcore_level = level if level <= logging.INFO else logging.ERROR
            logging.getLogger("apcore").setLevel(apcore_level)
        ctx.ensure_object(dict)
        ctx.obj["extensions_dir"] = ext_dir
        ctx.obj["verbose_help"] = verbose_help
        ctx.obj["exposure_filter"] = exposure_filter

    # FE-13 §4.1 / FR-13-13: --extensions-dir, --commands-dir, --binding are
    # registered only in standalone mode. When a registry is injected they
    # are inert (the discovery path doesn't run) so omitting them removes
    # help-text noise AND surfaces a clear "unknown option" error for the
    # rare caller who still tries them.
    if not registry_injected:
        cli.params.extend(
            [
                click.Option(
                    ["--extensions-dir", "extensions_dir_opt"],
                    default=None,
                    help="Path to apcore extensions directory.",
                ),
                click.Option(
                    ["--commands-dir", "commands_dir_opt"],
                    default=None,
                    help="Path to convention-based commands directory.",
                    expose_value=False,
                ),
                click.Option(
                    ["--binding", "binding_opt"],
                    default=None,
                    help="Path to binding.yaml file or directory for display resolution.",
                    expose_value=False,
                ),
                click.Option(
                    ["--allowed-prefix", "allowed_prefix_opt"],
                    multiple=True,
                    default=None,
                    help=(
                        "Allowlist of module-path prefixes for binding/convention "
                        "``target:`` resolution (repeatable). Forwarded to "
                        "RegistryWriter.write(allowed_prefixes=...). Mitigates "
                        "code-exec via forged binding YAML."
                    ),
                    expose_value=False,
                ),
            ]
        )

    # Issue #18: only register --version when the host application opts in by
    # passing `version=`. Embedded CLIs that do not supply a version no longer
    # leak the SDK's own version through `-V/--version`.
    if version is not None:
        cli = click.version_option(version=version, prog_name=prog_name)(cli)

    # Build the apcli sub-group. `hidden` controls root --help rendering only
    # (spec §4.1 / §4.11): the group and its subcommands remain reachable via
    # `<cli> <builtin-group-name> ...` regardless. Group name resolves from
    # `apcli_cfg.name` so downstream branded CLIs that overrode
    # `builtin_group_name` see their custom namespace.
    apcli_group = click.Group(
        name=apcli_cfg.name,
        help="Built-in commands.",
        hidden=not apcli_cfg.is_group_visible(),
    )
    cli.add_command(apcli_group)
    # Tell GroupedModuleGroup which name(s) are reserved for collision checks.
    # When the caller renames the built-in group, business modules must not be
    # allowed to claim the new name either.
    cli._reserved_group_names = frozenset({apcli_cfg.name})  # type: ignore[attr-defined]

    # Dispatch the 13-entry subcommand registrar table (FE-13 §4.9).
    _register_apcli_subcommands(
        apcli_group,
        apcli_cfg,
        registry,
        executor,
        exposure_filter,
        prog_name,
    )

    # Root-level --help --man support (stays at root per spec §4.1).
    configure_man_help(cli, prog_name, __version__)

    # FE-13 §11.2 deprecation shims — standalone mode only. Embedded
    # integrators' end users never see apcore-cli deprecation warnings.
    if not registry_injected:
        _register_deprecation_shims(cli, apcli_group, prog_name)

    # Extra commands from downstream projects (FE-11 §3.11). Reserved-name
    # (`apcli`) collisions are hard-rejected. Collisions with deprecation
    # shims yield to the user-supplied command (shim is dropped with a warning)
    # — shims are transitional scaffolding, not a real collision.
    if extra_commands:
        # Reserved-name set follows the resolved built-in-group name, not the
        # static default. When a downstream branded CLI renames the group via
        # ``builtin_group_name``, an extra_commands entry that shadows the
        # renamed group is the same kind of collision and is rejected here.
        _reserved_for_extra: frozenset[str] = frozenset({apcli_cfg.name})
        for cmd in extra_commands:
            cmd_name = getattr(cmd, "name", None)
            if cmd_name and cmd_name in _reserved_for_extra:
                msg = f"Extra command '{cmd_name}' is reserved."
                raise ValueError(msg)
            if cmd_name and cmd_name in cli.commands:
                existing = cli.commands[cmd_name]
                if getattr(existing, "__is_deprecation_shim__", False):
                    logger.warning(
                        "extra_commands '%s' overrides the deprecation shim for the same name.",
                        cmd_name,
                    )
                    del cli.commands[cmd_name]
                else:
                    msg = f"Extra command '{cmd_name}' conflicts with an existing command."
                    raise ValueError(msg)
            cli.add_command(cmd)

    return cli


# ---------------------------------------------------------------------------
# apcore-toolkit integration (ConventionScanner + BindingLoader + DisplayResolver)
# ---------------------------------------------------------------------------


def _apply_toolkit_integration(
    registry: Any,
    *,
    commands_dir: str | None,
    binding_path: str | None,
    allowed_prefixes: list[str] | None,
) -> None:
    """Load convention-scanner and/or binding-loader modules into the registry.

    Mirrors the TypeScript ``applyToolkitIntegration`` +
    ``loadBindingDisplayOverlay`` pair
    (``../apcore-cli-typescript/src/main.ts:706-781``). Both sources of
    module metadata (Python ``ConventionScanner`` for in-code modules,
    ``BindingLoader`` for ``.binding.yaml`` files) are parsed into
    ``ScannedModule`` lists, enriched with display overlay via
    ``DisplayResolver``, then written through ``RegistryWriter`` — the
    single registration path ensures ``--allowed-prefix`` protection
    applies to both sources consistently.

    Silently no-op if ``apcore-toolkit`` is not installed; individual
    optional features (``BindingLoader`` in toolkit < 0.5) degrade to
    a WARNING and are skipped.
    """
    if commands_dir is None and binding_path is None:
        return

    try:
        from apcore_toolkit import DisplayResolver, RegistryWriter
    except ImportError:
        logger.warning("apcore-toolkit not installed — toolkit features unavailable")
        return

    scanned: list[Any] = []

    if commands_dir is not None:
        try:
            from apcore_toolkit.convention_scanner import ConventionScanner

            scanner = ConventionScanner()
            scanned.extend(scanner.scan(commands_dir))
        except Exception as e:
            logger.warning("Convention scanner failed on '%s': %s", commands_dir, e)

    if binding_path is not None:
        try:
            from apcore_toolkit import BindingLoader
        except ImportError:
            # apcore-toolkit < 0.5.0 — silently skip the overlay (parity with
            # TS main.ts:761 "apcore-toolkit < 0.5.0 (no BindingLoader)").
            logger.warning("apcore-toolkit < 0.5.0: BindingLoader unavailable, --binding skipped")
        else:
            try:
                loader = BindingLoader()
                loaded = loader.load(binding_path)
                scanned.extend(loaded)
                logger.info(
                    "BindingLoader: parsed %d module(s) from %s",
                    len(loaded),
                    binding_path,
                )
            except Exception as e:
                logger.warning("BindingLoader failed on '%s': %s", binding_path, e)

    if not scanned:
        return

    if binding_path is not None:
        try:
            resolver = DisplayResolver()
            scanned = resolver.resolve(scanned, binding_path=binding_path)
            logger.debug("DisplayResolver: applied binding overlay from %s", binding_path)
        except Exception as e:
            logger.warning("DisplayResolver failed: %s", e)

    try:
        writer = RegistryWriter()
        writer.write(scanned, registry, allowed_prefixes=allowed_prefixes)
        logger.info("RegistryWriter: registered %d toolkit-sourced module(s)", len(scanned))
    except Exception as e:
        logger.warning("RegistryWriter failed: %s", e)


# ---------------------------------------------------------------------------
# FE-13 apcli subcommand dispatcher (§4.9)
# ---------------------------------------------------------------------------


_ALWAYS_REGISTERED: frozenset[str] = frozenset({"exec"})


def _register_apcli_subcommands(
    apcli_group: click.Group,
    apcli_cfg: ApcliGroup,
    registry: Any,
    executor: Any,
    exposure_filter: ExposureFilter,
    prog_name: str,
) -> None:
    """Register the 13 canonical apcli subcommands, filtered by visibility.

    Mirrors ``_registerApcliSubcommands`` in
    ``../apcore-cli-typescript/src/main.ts``. Each entry declares whether it
    ``requires_executor``; when the executor is missing, the entry is skipped
    silently (unless it's in :data:`_ALWAYS_REGISTERED` — in that case a WARN
    is emitted since spec §4.9 guarantees registration).
    """

    # Build system-subcommand registrars only when the executor's registry
    # carries `system.*` modules. Outside standalone+system-modules deploys,
    # invoking the subcommands would error at runtime with an opaque message;
    # probing here keeps `<cli> apcli --help` lean in the common case.
    system_available = executor is not None and _system_modules_available(executor)

    # Each entry: (name, requires_executor, callable that registers the
    # subcommand on apcli_group). The apcli_group is captured per entry.
    registrars: list[tuple[str, bool, Any]] = [
        ("list", False, lambda: register_list_command(apcli_group, registry, exposure_filter)),
        ("describe", False, lambda: register_describe_command(apcli_group, registry)),
        ("exec", True, lambda: register_exec_command(apcli_group, registry, executor)),
        ("validate", True, lambda: register_validate_command(apcli_group, registry, executor)),
        ("init", False, lambda: register_init_command(apcli_group)),
        ("health", True, lambda: register_health_command(apcli_group, executor) if system_available else None),
        ("usage", True, lambda: register_usage_command(apcli_group, executor) if system_available else None),
        ("enable", True, lambda: register_enable_command(apcli_group, executor) if system_available else None),
        ("disable", True, lambda: register_disable_command(apcli_group, executor) if system_available else None),
        ("reload", True, lambda: register_reload_command(apcli_group, executor) if system_available else None),
        ("config", True, lambda: register_config_command(apcli_group, executor) if system_available else None),
        ("completion", False, lambda: register_completion_command(apcli_group, prog_name=prog_name)),
        ("describe-pipeline", True, lambda: register_pipeline_command(apcli_group, executor)),
    ]

    mode = apcli_cfg.resolve_visibility()
    for name, requires_executor, registrar in registrars:
        # Decide whether this entry registers. Compute BEFORE the
        # missing-executor skip so _ALWAYS_REGISTERED is honored even when
        # its requires_executor flag is True — missing executor then warns
        # rather than silently drops (spec §4.9).
        if mode in ("all", "none"):
            should_register = True
        else:
            should_register = name in _ALWAYS_REGISTERED or apcli_cfg.is_subcommand_included(name)
        if not should_register:
            continue

        if requires_executor and executor is None:
            if name in _ALWAYS_REGISTERED:
                logger.warning(
                    "apcli.%s is always-registered but no executor is wired — "
                    "subcommand unavailable. Pass executor to create_cli().",
                    name,
                )
            continue

        registrar()


# ---------------------------------------------------------------------------
# FE-13 §11.2 deprecation shims (standalone-mode only)
# ---------------------------------------------------------------------------


_DEPRECATED_ROOT_COMMANDS: tuple[str, ...] = (
    "list",
    "describe",
    "exec",
    "init",
    "validate",
    "health",
    "usage",
    "enable",
    "disable",
    "reload",
    "config",
    "completion",
    "describe-pipeline",
)


def _register_deprecation_shims(
    root: click.Group,
    apcli_group: click.Group,
    prog_name: str,
) -> None:
    """Register thin root-level shims that forward to the ``apcli`` subcommand.

    Each shim writes the spec §11.2 warning to stderr then re-enters Click's
    dispatch loop on the ``apcli <name>`` path, preserving positional args +
    options. The shim is tagged with ``__is_deprecation_shim__ = True`` so
    ``extra_commands`` can override without raising a collision error.
    """

    def _make_shim(name: str, sub: click.Command) -> click.Command:
        @click.command(
            name=name,
            context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
            help=f"[DEPRECATED] Use '{prog_name} apcli {name}' instead.",
            hidden=True,
            add_help_option=False,
        )
        @click.pass_context
        def shim(ctx: click.Context) -> None:
            click.echo(
                f"WARNING: '{name}' as a root-level command is deprecated. "
                f"Use '{prog_name} apcli {name}' instead.\n"
                f"         Will be removed in v0.8. See: "
                f"https://aiperceivable.github.io/apcore-cli/features/builtin-group/#11-migration",
                err=True,
            )
            # Forward remaining args to the apcli subcommand's own invocation
            # path so nested sub-subcommands (`config get foo`) route correctly.
            tail = list(ctx.args)
            sub.main(args=tail, prog_name=f"{prog_name} apcli {name}", standalone_mode=False)

        # Tag so extra_commands can recognize and replace shims.
        shim.__is_deprecation_shim__ = True  # type: ignore[attr-defined]
        return shim

    for name in _DEPRECATED_ROOT_COMMANDS:
        sub = apcli_group.commands.get(name)
        if sub is None:
            continue
        if name in root.commands:
            continue
        root.add_command(_make_shim(name, sub))
