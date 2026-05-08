"""Pipeline strategy commands — describe-pipeline (FE-11)."""

from __future__ import annotations

import contextlib
import json
import logging
import sys
from typing import Any

import click

from apcore_cli.output import resolve_format

logger = logging.getLogger(__name__)

_PRESET_STEPS = {
    "standard": [
        "context_creation",
        "call_chain_guard",
        "module_lookup",
        "acl_check",
        "approval_gate",
        "middleware_before",
        "input_validation",
        "execute",
        "output_validation",
        "middleware_after",
        "return_result",
    ],
    "internal": [
        "context_creation",
        "call_chain_guard",
        "module_lookup",
        "middleware_before",
        "input_validation",
        "execute",
        "output_validation",
        "middleware_after",
        "return_result",
    ],
    "testing": [
        "context_creation",
        "module_lookup",
        "middleware_before",
        "input_validation",
        "execute",
        "output_validation",
        "middleware_after",
        "return_result",
    ],
    "performance": [
        "context_creation",
        "call_chain_guard",
        "module_lookup",
        "acl_check",
        "approval_gate",
        "input_validation",
        "execute",
        "output_validation",
        "return_result",
    ],
    "minimal": [
        "context_creation",
        "module_lookup",
        "execute",
        "return_result",
    ],
}


def _render_pipeline_table(
    steps_info: list[dict[str, Any]],
    fmt: str,
    strategy_name: str,
    step_count: int,
) -> None:
    """Render pipeline steps as JSON or a table.

    When fmt == "json" (or stdout is not a TTY), emits JSON.
    Otherwise renders a full-metadata table (pure/removable/timeout columns) when
    any step carries non-default metadata; renders a name-only table otherwise.
    """
    if fmt == "json" or not sys.stdout.isatty():
        payload = {
            "strategy": strategy_name,
            "step_count": step_count,
            "steps": [{"index": i + 1, **s} for i, s in enumerate(steps_info)],
        }
        click.echo(json.dumps(payload, indent=2))
        return

    click.echo(f"Pipeline: {strategy_name} ({step_count} steps)\n")
    has_metadata = steps_info and any(
        s.get("pure") is not False or s.get("removable") is not True or s.get("timeout_ms") for s in steps_info
    )

    if has_metadata:
        click.echo(f"  {'#':<4} {'Step':<28} {'Pure':<6} {'Removable':<11} Timeout")
        click.echo(f"  {'-' * 4} {'-' * 28} {'-' * 6} {'-' * 11} {'-' * 8}")
        for i, s in enumerate(steps_info, 1):
            pure = "yes" if s.get("pure") else "no"
            removable = "yes" if s.get("removable", True) else "no"
            timeout = f"{s['timeout_ms']}ms" if s.get("timeout_ms") else "\u2014"
            click.echo(f"  {i:<4} {s['name']:<28} {pure:<6} {removable:<11} {timeout}")
    else:
        click.echo(f"  {'#':<4} {'Step':<28}")
        click.echo(f"  {'-' * 4} {'-' * 28}")
        for i, s in enumerate(steps_info, 1):
            click.echo(f"  {i:<4} {s['name']:<28}")


def register_pipeline_command(cli: click.Group, executor: Any) -> None:
    """Register the describe-pipeline command."""

    @cli.command("describe-pipeline")
    @click.option(
        "--strategy",
        type=click.Choice(["standard", "internal", "testing", "performance", "minimal"]),
        default="standard",
        help="Strategy to describe (default: standard).",
    )
    @click.option(
        "--format",
        "output_format",
        type=click.Choice(["table", "json", "csv", "yaml", "jsonl"]),
        default=None,
    )
    def _describe_pipeline_cmd(strategy: str, output_format: str | None) -> None:  # pyright: ignore[reportUnusedVariable]
        """Show the execution pipeline steps for a strategy."""
        fmt = resolve_format(output_format)

        # Try to get StrategyInfo from executor.describe_pipeline() (apcore >= 0.18.0)
        strategy_info = None
        if hasattr(executor, "describe_pipeline"):
            with contextlib.suppress(AttributeError, NotImplementedError, TypeError):
                strategy_info = executor.describe_pipeline(strategy)

        if strategy_info is not None:
            # Use StrategyInfo dataclass fields for output
            info_name = getattr(strategy_info, "name", strategy)
            info_step_count = getattr(strategy_info, "step_count", 0)
            info_step_names = getattr(strategy_info, "step_names", [])

            # Try to get full step metadata from executor._strategy.steps
            steps_info: list[dict[str, Any]] = []
            strategy_obj = None
            if hasattr(executor, "_strategy"):
                try:
                    strategy_obj = executor._strategy
                except (AttributeError, NotImplementedError, TypeError) as e:
                    logger.warning("executor._strategy access failed; using step-names fallback: %s", e)
            if strategy_obj is None and hasattr(executor, "_resolve_strategy_name"):
                try:
                    strategy_obj = executor._resolve_strategy_name(strategy)
                except (AttributeError, NotImplementedError, TypeError) as e:
                    logger.warning("executor._resolve_strategy_name failed; using step-names fallback: %s", e)

            if strategy_obj is not None and hasattr(strategy_obj, "steps"):
                for step in strategy_obj.steps:
                    steps_info.append(
                        {
                            "name": getattr(step, "name", ""),
                            "pure": getattr(step, "pure", False),
                            "removable": getattr(step, "removable", True),
                            "timeout_ms": getattr(step, "timeout_ms", None),
                        }
                    )
            else:
                steps_info = [
                    {"name": s, "pure": False, "removable": True, "timeout_ms": None} for s in info_step_names
                ]

            _render_pipeline_table(steps_info, fmt, info_name, info_step_count)
            return

        # Fall back to legacy _resolve_strategy_name (apcore < 0.18.0)
        strategy_obj = None
        if hasattr(executor, "_resolve_strategy_name"):
            try:
                strategy_obj = executor._resolve_strategy_name(strategy)
            except (AttributeError, NotImplementedError, TypeError) as e:
                logger.warning("executor._resolve_strategy_name failed; using preset-steps fallback: %s", e)

        if strategy_obj is None:
            # Provide static info for known strategies
            steps = _PRESET_STEPS.get(strategy, [])

            if fmt == "json" or not sys.stdout.isatty():
                payload = {
                    "strategy": strategy,
                    "step_count": len(steps),
                    "steps": [{"index": i + 1, "name": s} for i, s in enumerate(steps)],
                }
                click.echo(json.dumps(payload, indent=2))
            else:
                click.echo(f"Pipeline: {strategy} ({len(steps)} steps)\n")
                click.echo(f"  {'#':<4} {'Step':<28}")
                click.echo(f"  {'-' * 4} {'-' * 28}")
                for i, s in enumerate(steps, 1):
                    click.echo(f"  {i:<4} {s:<28}")
            return

        # Use actual strategy object for detailed info
        steps_info = []
        for step in strategy_obj.steps:
            step_entry: dict[str, Any] = {
                "name": step.name,
                "pure": getattr(step, "pure", False),
                "removable": getattr(step, "removable", True),
                "timeout_ms": getattr(step, "timeout_ms", None),
            }
            steps_info.append(step_entry)

        _render_pipeline_table(steps_info, fmt, strategy, len(steps_info))
