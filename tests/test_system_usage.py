"""Tests for apcore_cli.system_usage — audit-log aggregator.

Covers:

* ``compute_summary`` — JSONL parsing, period filtering, per-module aggregation,
  malformed-line tolerance, missing-file behaviour, custom audit_path resolution.
* ``sort_modules_by_usage`` — sort-field validation, fallback when audit log is
  empty, deterministic secondary sort by module_id.

Each test uses ``tmp_path`` to write its own audit.jsonl so tests stay
independent and don't touch ``~/.apcore-cli/audit.jsonl``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from apcore_cli.system_usage import compute_summary, sort_modules_by_usage

# --------------------------------------------------------------------------- #
# Helpers / fixtures
# --------------------------------------------------------------------------- #


def _entry(
    module_id: str,
    *,
    timestamp: str,
    status: str = "success",
    duration_ms: int = 100,
    extra: dict | None = None,
) -> str:
    """Build one JSONL line matching AuditLogger's wire shape."""
    payload = {
        "timestamp": timestamp,
        "module_id": module_id,
        "status": status,
        "duration_ms": duration_ms,
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload) + "\n"


def _write_log(path: Path, lines: list[str]) -> None:
    path.write_text("".join(lines), encoding="utf-8")


@dataclass
class _Mod:
    """Minimal stand-in for the module objects sort_modules_by_usage walks."""

    module_id: str


# --------------------------------------------------------------------------- #
# compute_summary
# --------------------------------------------------------------------------- #


class TestComputeSummary:
    def test_missing_log_returns_empty_dict(self, tmp_path: Path) -> None:
        """No audit.jsonl on disk → empty summary, never raise."""
        missing = tmp_path / "does-not-exist.jsonl"
        assert compute_summary(audit_path=missing) == {}

    def test_aggregates_calls_errors_and_average_latency(self, tmp_path: Path) -> None:
        now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        ts = (now - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
        log = tmp_path / "audit.jsonl"
        _write_log(
            log,
            [
                _entry("math.add", timestamp=ts, duration_ms=100),
                _entry("math.add", timestamp=ts, duration_ms=200),
                _entry("math.add", timestamp=ts, status="error", duration_ms=300),
                _entry("text.summarize", timestamp=ts, duration_ms=500),
            ],
        )
        summary = compute_summary(audit_path=log, period="1h", now=now)
        assert summary["math.add"]["calls"] == 3
        assert summary["math.add"]["errors"] == 1
        assert summary["math.add"]["latency_ms"] == pytest.approx(200.0)  # (100+200+300)/3
        assert summary["text.summarize"]["calls"] == 1
        assert summary["text.summarize"]["errors"] == 0
        assert summary["text.summarize"]["latency_ms"] == pytest.approx(500.0)

    def test_period_filter_excludes_entries_older_than_cutoff(self, tmp_path: Path) -> None:
        now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        recent_ts = (now - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
        old_ts = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        log = tmp_path / "audit.jsonl"
        _write_log(
            log,
            [
                _entry("mod.recent", timestamp=recent_ts),
                _entry("mod.old", timestamp=old_ts),
            ],
        )
        summary = compute_summary(audit_path=log, period="1h", now=now)
        assert "mod.recent" in summary
        assert "mod.old" not in summary

    def test_unknown_period_falls_back_to_24h(self, tmp_path: Path) -> None:
        now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        within_24h = (now - timedelta(hours=10)).isoformat().replace("+00:00", "Z")
        beyond_24h = (now - timedelta(hours=30)).isoformat().replace("+00:00", "Z")
        log = tmp_path / "audit.jsonl"
        _write_log(
            log,
            [
                _entry("mod.in", timestamp=within_24h),
                _entry("mod.out", timestamp=beyond_24h),
            ],
        )
        summary = compute_summary(audit_path=log, period="bogus", now=now)
        assert "mod.in" in summary
        assert "mod.out" not in summary

    def test_malformed_json_lines_are_skipped_not_fatal(self, tmp_path: Path) -> None:
        now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        ts = (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        log = tmp_path / "audit.jsonl"
        # Mix of: blank, garbage, valid, valid-but-unparseable-timestamp, missing module_id
        _write_log(
            log,
            [
                "\n",
                "not-json-at-all{\n",
                _entry("good.module", timestamp=ts),
                _entry("bad.ts", timestamp="not-a-timestamp"),
                json.dumps({"timestamp": ts, "status": "success", "duration_ms": 50}) + "\n",  # no module_id
            ],
        )
        summary = compute_summary(audit_path=log, period="1h", now=now)
        assert list(summary.keys()) == ["good.module"]
        assert summary["good.module"]["calls"] == 1

    def test_non_numeric_duration_excluded_from_latency_average(self, tmp_path: Path) -> None:
        """latency_sum only accepts int/float duration_ms — strings ignored."""
        now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        ts = (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        log = tmp_path / "audit.jsonl"
        _write_log(
            log,
            [
                _entry("mod.x", timestamp=ts, duration_ms=100),
                _entry("mod.x", timestamp=ts, extra={"duration_ms": "garbage"}),
            ],
        )
        # Two calls counted; latency only summed once (100), avg = 100/2 = 50.
        summary = compute_summary(audit_path=log, period="1h", now=now)
        assert summary["mod.x"]["calls"] == 2
        assert summary["mod.x"]["latency_ms"] == pytest.approx(50.0)

    def test_default_audit_path_uses_audit_logger_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When audit_path is None, the default AuditLogger.DEFAULT_PATH is used."""
        from apcore_cli.security.audit import AuditLogger

        # Redirect the default path to a tmp file we control.
        fake = tmp_path / "default-audit.jsonl"
        monkeypatch.setattr(AuditLogger, "DEFAULT_PATH", fake)
        now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        ts = (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        _write_log(fake, [_entry("default.mod", timestamp=ts)])
        summary = compute_summary(period="1h", now=now)
        assert "default.mod" in summary


# --------------------------------------------------------------------------- #
# sort_modules_by_usage
# --------------------------------------------------------------------------- #


class TestSortModulesByUsage:
    def test_invalid_sort_field_raises_value_error(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        _write_log(log, [])
        with pytest.raises(ValueError, match="calls/errors/latency"):
            sort_modules_by_usage([_Mod("a")], "popularity", audit_path=log)

    def test_no_audit_data_returns_id_sorted_with_used_data_false(self, tmp_path: Path) -> None:
        """Empty log → fall back to id-sort, signalling the caller via False."""
        log = tmp_path / "audit.jsonl"
        log.write_text("", encoding="utf-8")
        modules = [_Mod("zzz"), _Mod("aaa"), _Mod("mmm")]
        sorted_mods, used = sort_modules_by_usage(modules, "calls", audit_path=log)
        assert used is False
        # reverse=True is the default → descending alphabetic id order.
        assert [m.module_id for m in sorted_mods] == ["zzz", "mmm", "aaa"]

    def test_sorts_by_calls_desc_when_data_available(self, tmp_path: Path) -> None:
        # Build a log such that "b.mod" has more calls than "a.mod".
        now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        log = tmp_path / "audit.jsonl"
        _write_log(
            log,
            [
                _entry("a.mod", timestamp=now_iso),
                _entry("b.mod", timestamp=now_iso),
                _entry("b.mod", timestamp=now_iso),
                _entry("b.mod", timestamp=now_iso),
            ],
        )
        modules = [_Mod("a.mod"), _Mod("b.mod")]
        sorted_mods, used = sort_modules_by_usage(modules, "calls", audit_path=log, period="24h")
        assert used is True
        assert [m.module_id for m in sorted_mods] == ["b.mod", "a.mod"]

    def test_modules_absent_from_log_get_zero_and_sort_last(self, tmp_path: Path) -> None:
        now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        log = tmp_path / "audit.jsonl"
        _write_log(log, [_entry("seen.mod", timestamp=now_iso)])
        modules = [_Mod("unseen.mod"), _Mod("seen.mod")]
        sorted_mods, used = sort_modules_by_usage(modules, "calls", audit_path=log, period="24h")
        assert used is True
        # seen.mod (1 call) comes before unseen.mod (0 calls) under reverse=True.
        assert [m.module_id for m in sorted_mods] == ["seen.mod", "unseen.mod"]

    def test_latency_sort_uses_latency_ms_attribute(self, tmp_path: Path) -> None:
        """sort_field='latency' must read 'latency_ms' from the summary."""
        now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        log = tmp_path / "audit.jsonl"
        _write_log(
            log,
            [
                _entry("slow.mod", timestamp=now_iso, duration_ms=1000),
                _entry("fast.mod", timestamp=now_iso, duration_ms=10),
            ],
        )
        modules = [_Mod("fast.mod"), _Mod("slow.mod")]
        sorted_mods, used = sort_modules_by_usage(modules, "latency", audit_path=log, period="24h")
        assert used is True
        assert [m.module_id for m in sorted_mods] == ["slow.mod", "fast.mod"]

    def test_reverse_false_yields_ascending_order(self, tmp_path: Path) -> None:
        now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        log = tmp_path / "audit.jsonl"
        _write_log(
            log,
            [
                _entry("hi.mod", timestamp=now_iso),
                _entry("hi.mod", timestamp=now_iso),
                _entry("lo.mod", timestamp=now_iso),
            ],
        )
        modules = [_Mod("hi.mod"), _Mod("lo.mod")]
        sorted_mods, used = sort_modules_by_usage(modules, "calls", reverse=False, audit_path=log, period="24h")
        assert used is True
        assert [m.module_id for m in sorted_mods] == ["lo.mod", "hi.mod"]
