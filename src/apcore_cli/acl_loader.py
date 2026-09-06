"""ACL root resolution and loading for the CLI (FE-14 §4.1 / §4.2).

apcore has enforced access control since PROTOCOL_SPEC §6, but no apcore-cli
SDK ever *constructed* an ``ACL``: all three build an ``Executor`` directly
rather than going through ``APCore``, which is the bootstrap that performs
``ACL.discover()``. This module closes that loop — it resolves ``acl.root``
through the FE-07 4-tier chain and delegates the parse to ``ACL.load``.

The CLI deliberately does **not** reimplement YAML rule parsing: rule-key
closure, ``effect`` / ``approval`` enum closure and pattern-array arity are
apcore's contract and are conformance-tested there.

Mirrors ``src/acl-loader.ts`` (TypeScript) and ``src/acl_loader.rs`` (Rust).
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from apcore_cli.security.audit import get_audit_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apcore import ACL, AuditEntry, Context, Identity

    from apcore_cli.config import ConfigResolver

logger = logging.getLogger("apcore_cli.acl")

#: The conventional file name inside an ``acl.root`` **directory**
#: (PROTOCOL_SPEC §3.1, matching ``ACL.discover``).
GLOBAL_ACL_FILENAME = "global_acl.yaml"

#: apcore-owned env var. ``acl.root`` appears in apcore's own ``Config``
#: defaults, so its variable follows the apcore convention exactly as
#: ``extensions.root`` is overridden by ``APCORE_EXTENSIONS_ROOT`` — and not
#: by an ``APCORE_CLI_*`` name.
ACL_ROOT_ENV_VAR = "APCORE_ACL_ROOT"

#: §4.8 / §5 env vars for the two audit keys. Same apcore-owned convention as
#: ``APCORE_ACL_ROOT``: these name apcore's ``acl.audit.*`` settings, not
#: CLI-private ones, so they are **not** ``APCORE_CLI_*``.
ACL_AUDIT_ENABLED_ENV_VAR = "APCORE_ACL_AUDIT_ENABLED"
ACL_AUDIT_INCLUDE_DENIED_ENV_VAR = "APCORE_ACL_AUDIT_INCLUDE_DENIED"

#: Exit code for an ACL denial (§6). Kept here so the §4.10 gate does not
#: import the command layer.
EXIT_ACL_DENIED = 77

#: Placeholder ``Identity.id`` used when ``--role`` / ``--identity-type`` were
#: given without ``--identity-id``. ``Identity.id`` is required by apcore but
#: is read by no built-in ACL condition (``roles`` and ``identity_types`` are),
#: so a neutral, obviously-synthetic sentinel is preferable to inventing a
#: plausible-looking user name.
DEFAULT_IDENTITY_ID = "@cli"


def resolve_acl_root(config: ConfigResolver, cli_flag: str | None = None) -> str | None:
    """Resolve the ACL root through the FE-07 4-tier precedence chain.

    | Tier | Source                                   |
    |------|------------------------------------------|
    | 1    | ``create_cli(acl=…)`` / ``--acl PATH``   |
    | 2    | ``APCORE_ACL_ROOT``                      |
    | 3    | ``acl.root`` in ``apcore.yaml``          |
    | 4    | ``./acl`` (only when it exists)          |

    Tiers 1-3 are returned verbatim — an explicitly configured root that does
    not exist is still the answer to "which root did the operator name", and
    :func:`load_cli_acl` turns it into "no enforcement" without an error. Tier
    4 is the implicit default, so it is returned only when the path exists;
    otherwise the answer is ``None`` and nothing is attached.

    Args:
        config: The FE-07 resolver.
        cli_flag: Value of ``--acl``, or the ``acl=`` argument to
            ``create_cli()`` when it is a path.

    Returns:
        The resolved ACL root path, or ``None`` when tier 4 was reached and
        the default path does not exist. Never raises.
    """
    if cli_flag:
        return cli_flag

    env_value = os.environ.get(ACL_ROOT_ENV_VAR)
    if env_value:
        return env_value

    file_value = config.resolve_object("acl.root")
    if isinstance(file_value, str) and file_value:
        return file_value

    default_root = config.DEFAULTS.get("acl.root")
    if isinstance(default_root, str) and default_root and os.path.exists(default_root):
        return default_root
    return None


def acl_file_for_root(root: str) -> str | None:
    """Return the ACL file *root* resolves to under apcore's convention.

    Applies **exactly** the directory convention ``ACL.discover`` documents:

    1. a path that does not exist resolves to ``None``;
    2. a directory resolves to ``<root>/global_acl.yaml``, or ``None`` when
       that conventional file is absent;
    3. a file resolves to itself.

    Returning ``None`` rather than synthesizing anything is a **hard
    invariant** (PROTOCOL_SPEC §6.1 missing-path rule): an empty ACL with
    ``default_effect: deny`` would deny every call in every project that has
    no ``acl/`` directory.
    """
    path = Path(root)
    if not path.exists():
        return None
    if path.is_dir():
        acl_file = path / GLOBAL_ACL_FILENAME
        if not acl_file.is_file():
            return None
        return str(acl_file)
    return str(path)


def _as_bool(value: Any, default: bool) -> bool:
    """Coerce a resolved config value to a bool, falling back to *default*.

    :meth:`ConfigResolver.resolve` hands back whatever the tier produced — a
    real ``bool`` from YAML or from :data:`ConfigResolver.DEFAULTS`, a *string*
    from the environment. ``bool("false")`` is ``True``, so the environment
    tier needs an explicit spelling table or ``APCORE_ACL_AUDIT_ENABLED=false``
    would turn auditing **on**.

    An unrecognized spelling falls back to *default* rather than to ``False``:
    for both §5 keys the default is ``true``, and a typo in a governance
    setting must not be the thing that silently stops the audit trail.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    logger.warning("Unrecognized boolean value %r for an acl.audit.* key; using %s.", value, default)
    return default


def resolve_audit_settings(config: ConfigResolver) -> tuple[bool, bool]:
    """Resolve ``acl.audit.enabled`` / ``acl.audit.include_denied`` (§5).

    Both default to ``true`` and both run the ordinary FE-07 chain
    (env → ``apcore.yaml`` → default); neither has a CLI flag.

    Returns:
        ``(enabled, include_denied)``.
    """
    enabled = _as_bool(config.resolve("acl.audit.enabled", env_var=ACL_AUDIT_ENABLED_ENV_VAR), True)
    include_denied = _as_bool(
        config.resolve("acl.audit.include_denied", env_var=ACL_AUDIT_INCLUDE_DENIED_ENV_VAR), True
    )
    return enabled, include_denied


def make_acl_audit_logger(*, include_denied: bool = True) -> Callable[[AuditEntry], None]:
    """Build the §4.8 callback adapting an ``AuditEntry`` onto FE-05.

    apcore emits exactly one ``AuditEntry`` per ``check_access()``, through
    this callback and nowhere else; the CLI forwards it to the FE-05
    :class:`~apcore_cli.security.audit.AuditLogger` so ACL decisions land in
    ``~/.apcore-cli/audit.jsonl`` beside execution records.

    ``include_denied=False`` suppresses **deny** entries only — apcore's own
    meaning for the key (``schemas/acl-config.schema.json``: "Whether to log
    denied access attempts"). Allow decisions keep being written. The filter
    lives here rather than in the writer because it is a *policy* about which
    decisions are recorded, and the writer's job is to record what it is given.

    The logger is looked up late, not captured: see
    :func:`apcore_cli.security.audit.get_audit_logger`.

    Every failure is swallowed. apcore invokes this callback inline on the
    decision path with no guard of its own, so an exception raised here would
    propagate out of ``check_access`` — turning a failure to *record* a
    decision into a failure to *reach* one. A governance verdict must not
    depend on the audit sink being writable.
    """

    def _log(entry: AuditEntry) -> None:
        try:
            if not include_denied and getattr(entry, "decision", None) == "deny":
                return
            audit_logger = get_audit_logger()
            if audit_logger is None:
                return
            audit_logger.log_acl_decision(entry)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("Failed to record an ACL audit entry: %s", e)

    return _log


def load_cli_acl(root: str, *, audit_enabled: bool = True, include_denied: bool = True) -> ACL | None:
    """Load an :class:`~apcore.ACL` from *root*, or ``None`` for no enforcement.

    When *audit_enabled* is true the ACL is **rebuilt** so it can carry the
    §4.8 audit callback: ``ACL.load`` takes no ``audit_logger``, and the
    callback is a constructor argument, so ::

        src = ACL.load(resolved_path)
        acl = ACL(src.rules, src.default_effect, audit_logger=…)

    is the only lossless way to attach one. Two things about that sequence are
    load-bearing:

    1. ``default_effect`` is carried from ``src``, **never** a literal. A file
       may legitimately declare ``default_effect: allow``, and passing the
       constructor's own ``"deny"`` default would silently invert the governing
       verdict for every call no rule matched — a rule set that grants by
       default would start denying, with nothing in the output saying so.
    2. The rebuilt ACL **loses** :meth:`~apcore.ACL.reload`, which needs the
       ``_yaml_path`` only ``ACL.load`` sets. This is accepted rather than
       worked around: no apcore-cli SDK calls ``reload()`` on any path, and
       writing the private attribute to fake the provenance would make
       ``reload()`` claim a file the object was not in fact loaded from. An
       embedder that needs reloading passes its own ACL to
       ``create_cli(acl=…)``, which §4.2 attaches unchanged.

    With auditing disabled the ``ACL.load`` result is returned **directly** —
    no rebuild, no callback — so the ``reload()`` caveat applies only to the
    auditing path.

    Args:
        root: Resolved ACL root (file or directory).
        audit_enabled: ``acl.audit.enabled`` (§5).
        include_denied: ``acl.audit.include_denied`` (§5). Ignored when
            *audit_enabled* is false, since no callback is installed at all.

    Returns:
        The loaded ACL, or ``None`` when the path (or the conventional file
        inside a directory root) is absent.

    Raises:
        ConfigNotFoundError: The path vanished between resolution and load.
        ACLRuleError: The file is structurally invalid (bad ``default_effect``,
            unknown rule key, malformed pattern array, non-mapping
            ``conditions``). Both map to exit ``47``.
    """
    from apcore import ACL as _ACL

    acl_file = acl_file_for_root(root)
    if acl_file is None:
        logger.debug("No ACL at '%s' — enforcement stays off.", root)
        return None
    src = _ACL.load(acl_file)
    logger.info("ACL loaded from %s (%d rules).", acl_file, len(src.rules))
    if not audit_enabled:
        logger.debug("acl.audit.enabled is false — attaching the loaded ACL directly, no audit callback.")
        return src
    acl = _ACL(
        src.rules,
        src.default_effect,
        audit_logger=make_acl_audit_logger(include_denied=include_denied),
    )
    logger.debug(
        "ACL audit callback installed (include_denied=%s); reload() is unavailable on the rebuilt ACL.",
        include_denied,
    )
    return acl


# ---------------------------------------------------------------------------
# Identity flags (FE-14 §4.3)
# ---------------------------------------------------------------------------
#
# apcore deliberately makes ``Context.caller_id`` unsettable by callers — it is
# managed exclusively by ``Context.child()``, so a top-level CLI invocation is
# always the effective caller ``@external``. The CLI MUST NOT fabricate one.
# What *is* settable is the identity, via ``Context.create(identity=…)``, and
# that is what the ``roles`` and ``identity_types`` conditions read.

_cli_identity: Identity | None = None


def build_identity(
    identity_id: str | None = None,
    identity_type: str | None = None,
    roles: tuple[str, ...] | list[str] = (),
) -> Identity | None:
    """Build an :class:`~apcore.Identity` from the three global flags.

    Returns ``None`` when none of the three was supplied, so
    ``Context.create()`` is called exactly as it is today and conditional
    rules keyed on ``roles`` / ``identity_types`` simply do not match.
    """
    role_tuple = tuple(roles or ())
    if not identity_id and not identity_type and not role_tuple:
        return None

    from apcore import Identity as _Identity

    return _Identity(
        id=identity_id or DEFAULT_IDENTITY_ID,
        type=identity_type or "user",
        roles=role_tuple,
    )


def merge_identity(
    base: Identity | None,
    identity_id: str | None = None,
    identity_type: str | None = None,
    roles: tuple[str, ...] | list[str] = (),
) -> Identity | None:
    """Overlay explicitly-supplied identity fields onto *base*, field by field.

    Implements the §4.5 precedence rule for a subcommand that restates the
    §4.3 identity flags: **a subcommand-level flag overrides its root-level
    counterpart for that invocation, and a root flag not restated at the
    subcommand level still applies.**

    The merge is deliberately per-field rather than all-or-nothing. Replacing
    the whole identity as soon as any one flag is restated would silently drop
    the root's other fields — so ``--identity-type service --role admin apcli
    acl check --role guest`` would evaluate as an anonymous ``guest`` with the
    default type, discarding a ``service`` type the caller never withdrew.

    Returns ``None`` only when *base* is ``None`` and no field was supplied,
    so a caller with no identity anywhere still gets today's ``Context``.
    """
    role_tuple = tuple(roles or ())
    if base is None:
        return build_identity(identity_id, identity_type, role_tuple)
    if not identity_id and not identity_type and not role_tuple:
        return base

    from apcore import Identity as _Identity

    return _Identity(
        id=identity_id or base.id,
        type=identity_type or base.type,
        roles=role_tuple or base.roles,
    )


def set_cli_identity(identity: Identity | None) -> None:
    """Install the process-wide identity built from the global flags."""
    global _cli_identity
    _cli_identity = identity


def get_cli_identity() -> Identity | None:
    """Return the identity installed by :func:`set_cli_identity`."""
    return _cli_identity


def build_context(
    identity: Identity | None = None,
    *,
    depth: int | None = None,
    arguments: dict[str, Any] | None = None,
) -> Context | None:
    """Build a :class:`~apcore.Context` for an ACL evaluation, or ``None``.

    ``None`` is returned when there is nothing to carry — no identity, no
    synthetic call depth and no argument projection — so callers can pass the
    result straight through and get today's exact behaviour.

    ``depth`` populates a synthetic ``call_chain`` for the ``max_call_depth``
    condition; ``arguments`` populates the §6.1.8 governance projection the
    ``arguments`` condition reads (key presence only — no value ever reaches
    it).
    """
    if identity is None and depth is None and arguments is None:
        return None

    from apcore import Context as _Context
    from apcore.context import GovernanceProjection

    context = _Context.create(identity=identity)
    if depth is not None:
        context.call_chain = [f"@synthetic.{index}" for index in range(depth)]
    if arguments is not None:
        context.governance_projection = GovernanceProjection.of(arguments)
    return context


_cli_acl: Any | None = None


def set_cli_acl(acl: Any | None) -> None:
    """Record the ACL the CLI attached to its Executor.

    The **object** rather than a boolean, because §4.10 needs to reach an
    access decision in the parent for execution paths that never touch that
    Executor. Process-wide, like ``set_audit_logger``, so the §6.2 strategy
    warning and the §4.10 gate read one source without either depending on
    the executor being a real apcore one.
    """
    global _cli_acl
    _cli_acl = acl


def get_cli_acl() -> Any | None:
    """Return the ACL installed by :func:`set_cli_acl`, or ``None``."""
    return _cli_acl


def is_acl_attached() -> bool:
    """Whether an ACL is currently attached to the CLI's Executor."""
    return _cli_acl is not None


def delegated_context(
    context: Context | None = None,
    arguments: dict[str, Any] | None = None,
) -> Context:
    """Build the ``Context`` a §4.10 gate evaluates against. **Never None.**

    This is deliberately *not* :func:`build_context`'s behaviour. That function
    returns ``None`` when no identity flag was given, which is right for
    ``apcli acl check`` — it simulates a call, and a simulation with nothing
    asserted is honestly context-free. It is **wrong for the gate**, because
    the gate stands in for a real call.

    PROTOCOL_SPEC §6.5 makes every conditional rule a non-match when a call
    supplies no context, while apcore's pipeline creates a context at Step 1
    for *every* real call. So a gate passing ``None`` would leave conditional
    ``deny`` rules inert on the delegated path while they fire in-process —
    the same silent bypass §4.10 exists to close, one level down.

    The call's arguments become the §6.1.8 governance projection for the same
    reason: without it an ``arguments``-scoped rule is unevaluable and goes
    inert on this path only. The projection carries key names and JSON types
    and structurally cannot carry a value, so no argument data leaks into the
    decision.

    A fresh ``Context`` is built rather than the caller's being mutated, so a
    downstream embedder that reuses one does not have a projection attached to
    it as a side effect.
    """
    from apcore import Context as _Context
    from apcore.context import GovernanceProjection

    resolved = _Context.create(identity=getattr(context, "identity", None))
    if context is not None:
        resolved.call_chain = list(getattr(context, "call_chain", None) or [])
    resolved.governance_projection = GovernanceProjection.of(arguments if isinstance(arguments, dict) else {})
    return resolved


def enforce_acl_for_unguarded_path(
    module_id: str,
    context: Context | None = None,
    *,
    arguments: dict[str, Any] | None = None,
    caller_id: str = "@external",
) -> bool:
    """Gate an execution path that does not carry the attached ACL (§4.10).

    Attaching an ACL to the Executor gates the calls that go **through that
    Executor**, and nothing else. ``--sandbox`` spawns a subprocess that
    builds its own bare ``Registry`` + ``Executor``, so without this the ACL
    is silently and completely bypassed — a rule denying a module is enforced
    for a plain call and ignored for a sandboxed one. Switching on a
    **security** flag would switch off access control.

    The decision is reached **here, in the parent**, which already holds the
    ACL, and **before** the subprocess is spawned. The child is deliberately
    not trusted to re-load the ACL as the control: the sandbox forwards a
    narrow environment allowlist by design and runs in a temporary cwd, so a
    child's view of ``acl.root`` is neither guaranteed nor trustworthy as a
    gate. (Forwarding it for defence in depth is permitted; treating it as the
    control is not.)

    The decision is always evaluated against a real ``Context`` carrying the
    call's argument projection — see :func:`delegated_context` for why a
    ``None`` context would reopen the bypass for every conditional rule.

    ``caller_id`` is ``@external`` because a top-level CLI invocation always
    is — ``Context.caller_id`` is managed exclusively by ``Context.child()``
    and the CLI never fabricates one (§7 rule 2).

    Args:
        module_id: The module about to be executed off-Executor.
        context: The FE-14 identity context, when identity flags were given.
        arguments: The call's inputs, projected to keys and types for the
            ``arguments`` condition.
        caller_id: Simulated caller; callers should not override this.

    Returns:
        Whether the matched rule requires human approval, for the caller to
        compose with the module's own annotation **before** the CLI's approval
        gate runs — otherwise the same rule would demand a human on one path
        and not the other.

    Raises:
        SystemExit: Exit ``77`` when the ACL denies the call.
    """
    acl = get_cli_acl()
    if acl is None:
        # Enforcement-only-when-configured (§4.2): no ACL, no gate, and the
        # sandbox behaves exactly as it did pre-FE-14.
        return False

    decision = acl.check_access(caller_id, module_id, delegated_context(context, arguments))
    if decision.access == "deny":
        import click

        click.echo(f"Error: Permission denied for module '{module_id}'.", err=True)
        sys.exit(EXIT_ACL_DENIED)
    return bool(decision.approval_required)


def cli_context() -> Context | None:
    """Build a fresh :class:`~apcore.Context` for the installed CLI identity.

    Returns ``None`` when no identity flag was supplied, which is the signal
    for call sites to invoke the executor exactly as they did pre-FE-14.
    """
    return build_context(get_cli_identity())


__all__ = [
    "ACL_AUDIT_ENABLED_ENV_VAR",
    "ACL_AUDIT_INCLUDE_DENIED_ENV_VAR",
    "ACL_ROOT_ENV_VAR",
    "DEFAULT_IDENTITY_ID",
    "EXIT_ACL_DENIED",
    "GLOBAL_ACL_FILENAME",
    "acl_file_for_root",
    "build_context",
    "build_identity",
    "cli_context",
    "delegated_context",
    "enforce_acl_for_unguarded_path",
    "get_cli_acl",
    "get_cli_identity",
    "is_acl_attached",
    "load_cli_acl",
    "make_acl_audit_logger",
    "merge_identity",
    "resolve_acl_root",
    "resolve_audit_settings",
    "set_cli_acl",
    "set_cli_identity",
]
