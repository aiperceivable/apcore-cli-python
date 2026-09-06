"""``apcli acl`` — author, inspect and lint access-control rules (FE-14).

Four subcommands under a nested group, mirroring the ``apcli config get|set``
and ``apcli init module`` precedent:

* ``list``     — render the attached rule set and its ``default_effect`` (§4.4)
* ``check``    — simulate one call through ``ACL.check_access()`` (§4.5)
* ``validate`` — report every ``RuleValidationFinding`` (§4.6)
* ``status``   — render ``Executor.governance_state()`` (§4.7)

Mirrors ``src/acl-cmd.ts`` (TypeScript) and ``src/acl_cmd.rs`` (Rust).
"""

from __future__ import annotations

import json
import sys
from typing import Any

import click
from apcore_toolkit import format_csv, format_jsonl
from rich.console import Console
from rich.table import Table

from apcore_cli.acl_loader import build_context, get_cli_identity, merge_identity

# Exit codes (FE-14 §6). 47 is a *configuration* fault; 77 is an actual
# access decision. Conflating them would make a broken ACL file read as a
# permissions problem to any script branching on the exit code.
EXIT_OK = 0
EXIT_INVALID_INPUT = 2
EXIT_ACL_CONFIG = 47
EXIT_ACL_DENIED = 77

_NO_ACL_MESSAGE = "No ACL configured; nothing to check."

#: The nine observations ``Executor.governance_state()`` carries, paired with
#: the labels §4.7 renders them under, in the spec's order.
_GOVERNANCE_ROWS: tuple[tuple[str, str], ...] = (
    ("control_modules_registered", "Control modules registered"),
    ("read_modules_registered", "Read modules registered"),
    ("acl_configured", "ACL configured"),
    ("builtin_acl_gate_wired", "Built-in ACL gate wired"),
    ("approval_handler_configured", "Approval handler configured"),
    ("builtin_approval_gate_wired", "Built-in approval gate wired"),
    ("policy_strict", "Policy strict"),
    ("all_control_modules_require_approval", "All control modules gated"),
)

_UNPROTECTED_LABEL = "Unprotected control surface"


def _truncate(text: str, max_length: int = 48) -> str:
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def _condition_keys(conditions: Any) -> list[str]:
    """Condition **keys** only, lexicographically ordered.

    §4.4: the ``Conditions`` column lists keys and never bodies — full
    condition objects stay available (losslessly) in ``--format json``.
    """
    if not isinstance(conditions, dict):
        return []
    return sorted(str(key) for key in conditions)


def _rule_rows(acl: Any) -> list[dict[str, Any]]:
    """Project the ACL's rules into definition order — which is also
    evaluation order (first-match-wins, no priority sorting)."""
    rows: list[dict[str, Any]] = []
    for index, rule in enumerate(acl.rules):
        rows.append(
            {
                "index": index,
                "effect": rule.effect,
                "approval": rule.approval,
                "callers": list(rule.callers),
                "targets": list(rule.targets),
                "conditions": rule.conditions,
                "description": rule.description or "",
            }
        )
    return rows


def register_acl_command(
    apcli_group: click.Group,
    executor: Any,
    acl: Any | None = None,
    source: str | None = None,
) -> None:
    """Register the ``acl`` nested group on *apcli_group* (FE-14 §4.10).

    Args:
        apcli_group: The built-in group to attach to.
        executor: The CLI's Executor — read by ``status`` for
            ``governance_state()``.
        acl: The attached ACL, or ``None`` when no root resolved. Listing
            nothing is not an error (§4.4); checking nothing is (§4.5).
        source: The file the ACL was loaded from, for display only.
    """

    @apcli_group.group("acl")
    def acl_group() -> None:
        """Inspect and lint access-control rules."""

    # -- list ---------------------------------------------------------------

    @acl_group.command("list")
    @click.option(
        "--format",
        "output_format",
        type=click.Choice(["table", "json", "csv", "yaml", "jsonl"]),
        default="table",
        help="Output format.",
    )
    def acl_list(output_format: str) -> None:
        """List the attached rule set and its default effect."""
        if acl is None:
            # Listing nothing is not an error — exit 0 in every format.
            if output_format == "table":
                click.echo("No ACL configured.")
            elif output_format == "json":
                click.echo(json.dumps({"source": None, "default_effect": None, "rules": []}, indent=2))
            elif output_format == "yaml":
                import yaml as _yaml

                click.echo(
                    _yaml.dump(
                        {"source": None, "default_effect": None, "rules": []},
                        default_flow_style=False,
                        allow_unicode=True,
                    ).rstrip()
                )
            sys.exit(EXIT_OK)

        rows = _rule_rows(acl)
        payload = {
            "source": source,
            "default_effect": acl.default_effect,
            "rules": rows,
        }

        if output_format == "json":
            click.echo(json.dumps(payload, indent=2, default=str))
        elif output_format == "yaml":
            import yaml as _yaml

            click.echo(_yaml.dump(payload, default_flow_style=False, allow_unicode=True).rstrip())
        elif output_format == "csv":
            if rows:
                click.echo(format_csv(rows).rstrip())
        elif output_format == "jsonl":
            if rows:
                click.echo(format_jsonl(rows).rstrip())
        else:
            plural = "rule" if len(rows) == 1 else "rules"
            suffix = f" (source: {source}, {len(rows)} {plural})" if source else f" ({len(rows)} {plural})"
            click.echo(f"Default effect: {acl.default_effect}{suffix}\n")
            table = Table()
            table.add_column("#", justify="right")
            table.add_column("Effect")
            table.add_column("Approval")
            table.add_column("Callers")
            table.add_column("Targets")
            table.add_column("Conditions")
            table.add_column("Description")
            for row in rows:
                keys = _condition_keys(row["conditions"])
                table.add_row(
                    str(row["index"]),
                    str(row["effect"]),
                    str(row["approval"]),
                    _truncate(", ".join(row["callers"])),
                    _truncate(", ".join(row["targets"])),
                    ", ".join(keys) if keys else "—",
                    _truncate(str(row["description"])),
                )
            Console().print(table)
        sys.exit(EXIT_OK)

    # -- check --------------------------------------------------------------

    # The three identity options restate the §4.3 root flags, and their
    # `help=` strings and metavars are pinned NORMATIVELY by §4.5 to the same
    # text: two spellings of one flag inside one CLI is a defect whether or
    # not this level is byte-matched by the conformance fixtures. The three
    # unique to `check` are pinned by §4.5 too. Metavars are explicit because
    # click would otherwise render `TEXT` (and `INTEGER` for --depth).
    @acl_group.command("check")
    @click.argument("target")
    @click.option(
        "--caller",
        default="@external",
        metavar="ID",
        help="Simulated caller ID (default: @external). Nothing is executed, so any value is accepted.",
    )
    @click.option(
        "--identity-id",
        default=None,
        metavar="ID",
        help="Assert Identity.id for ACL conditions. Unauthenticated assertion, not authentication.",
    )
    @click.option(
        "--identity-type",
        default=None,
        metavar="TYPE",
        help=(
            "Assert Identity.type for ACL conditions (default: user). Unauthenticated assertion, not authentication."
        ),
    )
    @click.option(
        "--role",
        "roles",
        multiple=True,
        metavar="ROLE",
        help=("Assert an Identity role for ACL conditions. Repeatable. Unauthenticated assertion, not authentication."),
    )
    @click.option(
        "--depth",
        type=int,
        default=None,
        metavar="N",
        help="Simulated call-chain depth for the max_call_depth condition.",
    )
    @click.option(
        "--input",
        "input_json",
        default=None,
        metavar="JSON",
        help="Argument map for the arguments condition. Key presence only; values are not compared.",
    )
    @click.option(
        "--format",
        "output_format",
        type=click.Choice(["table", "json"]),
        default="table",
        help="Output format.",
    )
    def acl_check(
        target: str,
        caller: str,
        identity_id: str | None,
        identity_type: str | None,
        roles: tuple[str, ...],
        depth: int | None,
        input_json: str | None,
        output_format: str,
    ) -> None:
        """Evaluate a simulated call against the rule set.

        Executes nothing. Reports both §6.1.6 axes separately: authorization
        (``access``) and the approval requirement (``approval_required``). An
        allow-with-approval outcome exits 0 — the call *is* permitted.
        """
        if acl is None:
            click.echo(f"Error: {_NO_ACL_MESSAGE}", err=True)
            sys.exit(EXIT_ACL_CONFIG)

        arguments: dict[str, Any] | None = None
        if input_json is not None:
            try:
                parsed = json.loads(input_json)
            except json.JSONDecodeError as exc:
                click.echo(f"Error: --input is not valid JSON: {exc}", err=True)
                sys.exit(EXIT_INVALID_INPUT)
            if not isinstance(parsed, dict):
                click.echo("Error: --input JSON must be an object.", err=True)
                sys.exit(EXIT_INVALID_INPUT)
            arguments = parsed

        # §4.5 precedence: a restated flag overrides its root counterpart for
        # this invocation; a root flag left unrestated still applies. Merged
        # field by field — replacing the whole identity whenever any one flag
        # is restated would silently drop root fields the caller never
        # withdrew.
        identity = merge_identity(get_cli_identity(), identity_id, identity_type, roles)
        context = build_context(identity, depth=depth, arguments=arguments)

        # MUST be check_access() and never check(): the boolean fails closed on
        # approval, so a call that is allowed but needs a human would report as
        # denied. Both axes have to be shown separately.
        decision = acl.check_access(caller, target, context)

        matched = decision.matched_rule_index
        matched_description = ""
        if matched is not None and 0 <= matched < len(acl.rules):
            matched_description = acl.rules[matched].description or ""

        if output_format == "json":
            click.echo(
                json.dumps(
                    {
                        "target": target,
                        "caller": caller,
                        "access": decision.access,
                        "approval_required": bool(decision.approval_required),
                        "matched_rule_index": matched,
                        "reason": decision.reason,
                    },
                    indent=2,
                )
            )
        else:
            if matched is None:
                rule_note = ""
            elif matched_description:
                rule_note = f'  (rule #{matched}: "{matched_description}")'
            else:
                rule_note = f"  (rule #{matched})"
            click.echo(f"Target:   {target}")
            click.echo(f"Caller:   {caller}")
            click.echo(f"Decision: {decision.access.upper()}{rule_note}")
            click.echo(f"Approval: {'REQUIRED' if decision.approval_required else 'NOT REQUIRED'}")
            click.echo(f"Reason:   {decision.reason}")

        if decision.access == "deny":
            click.echo(f"Access denied: {caller} -> {target}", err=True)
            sys.exit(EXIT_ACL_DENIED)
        sys.exit(EXIT_OK)

    # -- validate -----------------------------------------------------------

    @acl_group.command("validate")
    @click.option(
        "--format",
        "output_format",
        type=click.Choice(["table", "json"]),
        default="table",
        help="Output format.",
    )
    def acl_validate(output_format: str) -> None:
        """Report every structural or registry fault in the rule set.

        ``validate_rules()`` is a *runtime* check by design — condition
        handlers are registered process-wide and legitimately after load, so
        ``ACL.load`` only warns. This is the deterministic check to run once
        registration is complete.
        """
        if acl is None:
            click.echo(f"Error: {_NO_ACL_MESSAGE}", err=True)
            sys.exit(EXIT_ACL_CONFIG)

        findings = acl.validate_rules()
        rows = [
            {
                "rule_index": finding.rule_index,
                "condition_path": finding.condition_path,
                "condition_key": finding.condition_key,
                "effect": finding.effect,
                "sync_resolvable": bool(finding.sync_resolvable),
                "async_resolvable": bool(finding.async_resolvable),
            }
            for finding in findings
        ]

        if output_format == "json":
            click.echo(json.dumps({"findings": rows, "count": len(rows)}, indent=2))
        else:
            if not rows:
                click.echo("0 findings.")
            else:
                click.echo(f"{len(rows)} finding{'s' if len(rows) != 1 else ''}:\n")
                table = Table()
                table.add_column("Rule", justify="right")
                table.add_column("Path")
                table.add_column("Key")
                table.add_column("Effect")
                # Sync / Async are rendered as separate columns and MUST NOT be
                # collapsed into one boolean (§6.1.3 rule 3): sync=no, async=yes
                # is an async-only handler — working under async_check(),
                # unevaluable under check().
                table.add_column("Sync")
                table.add_column("Async")
                for row in rows:
                    table.add_row(
                        str(row["rule_index"]),
                        str(row["condition_path"]),
                        str(row["condition_key"]) if row["condition_key"] is not None else "—",
                        str(row["effect"]),
                        "yes" if row["sync_resolvable"] else "no",
                        "yes" if row["async_resolvable"] else "no",
                    )
                Console().print(table)
                click.echo(
                    "\nA finding on a `deny` rule is the consequential one: that rule now denies\n"
                    "every call it matches."
                )

        sys.exit(EXIT_ACL_CONFIG if rows else EXIT_OK)

    # -- status -------------------------------------------------------------

    @acl_group.command("status")
    @click.option(
        "--strict",
        is_flag=True,
        default=False,
        help="Exit 47 when the control surface is unprotected (for deployment startup checks).",
    )
    @click.option(
        "--format",
        "output_format",
        type=click.Choice(["table", "json"]),
        default="table",
        help="Output format.",
    )
    def acl_status(strict: bool, output_format: str) -> None:
        """Report what is actually gating the registry.

        ``acl_configured`` alone is not the answer: the ACL and approval gates
        are pipeline *steps*, and the ``internal`` / ``testing`` / ``minimal``
        strategies remove them, so an executor can hold an ACL that no step
        ever consults.
        """
        if executor is None or not hasattr(executor, "governance_state"):
            click.echo(
                "Error: Executor does not expose governance_state(); cannot report ACL status.",
                err=True,
            )
            sys.exit(EXIT_ACL_CONFIG)

        try:
            state = executor.governance_state()
        except Exception as exc:  # pragma: no cover - defensive
            click.echo(f"Error: Failed to read governance state: {exc}", err=True)
            sys.exit(EXIT_ACL_CONFIG)

        values = {field: bool(getattr(state, field, False)) for field, _ in _GOVERNANCE_ROWS}
        unprotected = bool(getattr(state, "unprotected_control_surface", False))

        if output_format == "json":
            payload: dict[str, Any] = dict(values)
            payload["unprotected_control_surface"] = unprotected
            payload["acl_source"] = source
            click.echo(json.dumps(payload, indent=2))
        else:
            width = max(len(label) for _, label in _GOVERNANCE_ROWS + ((_UNPROTECTED_LABEL, _UNPROTECTED_LABEL),)) + 2
            for field, label in _GOVERNANCE_ROWS:
                rendered = "yes" if values[field] else "no"
                if field == "acl_configured" and values[field] and source:
                    rendered = f"{rendered}  ({source})"
                click.echo(f"{label + ':':<{width}}{rendered}")
            click.echo("─" * (width + 4))
            click.echo(f"{_UNPROTECTED_LABEL + ':':<{width}}{'YES' if unprotected else 'NO'}")

        if strict and unprotected:
            click.echo("Unprotected control surface.", err=True)
            sys.exit(EXIT_ACL_CONFIG)
        sys.exit(EXIT_OK)

    _ = (acl_list, acl_check, acl_validate, acl_status)


__all__ = ["register_acl_command"]
