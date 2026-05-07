"""system.usage aggregator — reads ~/.apcore-cli/audit.jsonl and groups by module_id.

Implements the `system.usage.summary` data pipeline described in
aiperceivable/apcore-cli#17:

    Read audit.jsonl
      -> filter by time period (timestamp >= now - period; default 24h)
      -> group by module_id
      -> aggregate: calls, errors, avg latency_ms

This module provides the aggregator that powers `list --sort calls|errors|latency`
in `apcore_cli.discovery`. The data pipeline has zero dependency on LLM or any
external service — it reads the local JSONL audit log written by
`apcore_cli.security.audit.AuditLogger` on every module execution.

Module-protocol registration of `system.usage.summary` and `system.usage.module`
as registry-callable built-ins is tracked as a follow-up; today the readers
are invoked directly by the discovery layer.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict

from apcore_cli.security.audit import AuditLogger

logger = logging.getLogger("apcore_cli.system_usage")


class UsageSummary(TypedDict):
    """Per-module aggregated usage stats over a time period."""

    module_id: str
    calls: int
    errors: int
    latency_ms: float


_PERIOD_TO_DELTA: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


def _parse_iso_timestamp(value: str) -> datetime | None:
    """Parse `2026-04-27T10:00:00.123Z` into a tz-aware datetime, or None on failure."""
    try:
        # `Z` suffix is not parsed by fromisoformat in 3.10; replace with +00:00
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def compute_summary(
    audit_path: Path | None = None,
    period: str = "24h",
    *,
    now: datetime | None = None,
) -> dict[str, UsageSummary]:
    """Return per-module aggregates from the audit log.

    Args:
        audit_path: Path to ``audit.jsonl`` (defaults to AuditLogger.DEFAULT_PATH).
        period: One of ``1h``, ``24h``, ``7d``, ``30d``. Unknown values are
            treated as ``24h`` with a debug log.
        now: Override "now" for deterministic testing; defaults to UTC now.

    Returns:
        Mapping of module_id -> UsageSummary. Empty dict if the log is missing
        or empty (caller is expected to fall back to id-sort with a visible
        message in that case).
    """
    path = audit_path or AuditLogger.DEFAULT_PATH
    if not path.exists():
        return {}

    delta = _PERIOD_TO_DELTA.get(period)
    if delta is None:
        logger.debug("Unknown period %r; falling back to 24h.", period)
        delta = _PERIOD_TO_DELTA["24h"]
    cutoff = (now or datetime.now(UTC)) - delta

    counts: dict[str, int] = {}
    errors: dict[str, int] = {}
    latency_sum: dict[str, int] = {}

    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    # Audit log is append-only JSONL; tolerate a partial last
                    # line from a crashed write rather than failing the sort.
                    continue
                ts = _parse_iso_timestamp(entry.get("timestamp", ""))
                if ts is None or ts < cutoff:
                    continue
                module_id = entry.get("module_id")
                if not module_id:
                    continue
                counts[module_id] = counts.get(module_id, 0) + 1
                if entry.get("status") == "error":
                    errors[module_id] = errors.get(module_id, 0) + 1
                duration = entry.get("duration_ms", 0)
                if isinstance(duration, int | float):
                    latency_sum[module_id] = latency_sum.get(module_id, 0) + int(duration)
    except OSError as e:
        logger.debug("Could not read audit log %s: %s", path, e)
        return {}

    out: dict[str, UsageSummary] = {}
    for mid, calls in counts.items():
        avg_latency = latency_sum.get(mid, 0) / calls if calls else 0.0
        out[mid] = UsageSummary(
            module_id=mid,
            calls=calls,
            errors=errors.get(mid, 0),
            latency_ms=avg_latency,
        )
    return out


def sort_modules_by_usage(
    modules: list,
    sort_field: str,
    *,
    reverse: bool = True,
    audit_path: Path | None = None,
    period: str = "24h",
) -> tuple[list, bool]:
    """Sort `modules` by `calls`, `errors`, or `latency` using audit-log data.

    Returns ``(sorted_modules, used_usage_data)``. When the audit log has no
    matching entries, modules retain id-sort order and ``used_usage_data`` is
    False — callers should surface that to the user (per issue #17 AC: visible
    message, not just a logger warning).
    """
    if sort_field not in ("calls", "errors", "latency"):
        raise ValueError(f"sort_field must be calls/errors/latency, got {sort_field!r}")

    summary = compute_summary(audit_path=audit_path, period=period)
    if not summary:
        modules.sort(key=lambda m: getattr(m, "module_id", ""), reverse=reverse)
        return modules, False

    key_attr = "latency_ms" if sort_field == "latency" else sort_field

    def _key(m: object) -> tuple[float, str]:
        mid = getattr(m, "module_id", "") or ""
        s = summary.get(mid)
        primary = float(s[key_attr]) if s else 0.0  # type: ignore[literal-required]
        # Stable secondary sort by id keeps output deterministic when two
        # modules share a count (very common: 0 errors).
        return (primary, mid)

    modules.sort(key=_key, reverse=reverse)
    return modules, True
