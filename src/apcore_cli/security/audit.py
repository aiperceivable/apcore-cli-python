"""Audit logging in JSON Lines format (FE-05)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger("apcore_cli.security")

#: The 13 fields of apcore's ``AuditEntry``, spelled in the ``snake_case`` wire
#: form FE-14 §4.8 pins. The CLI is an adapter here and MUST NOT rename or drop
#: any of them — ``handler_error`` and ``approval_required`` included, which are
#: the two most droppable-looking and the two that carry the §6.3.1 distinctions
#: ("no answer was obtainable" vs "the handler said no", and authorization vs
#: approval) nothing else records.
#:
#: **The order is normative, not incidental** (§4.8). It is apcore's own
#: ``AuditEntry`` declaration order, and the audit log is JSONL — so an
#: unspecified order would make the same decision serialize to different bytes
#: in each SDK, the divergence class this feature has already hit on flag help
#: text and the identity sentinel. TypeScript camelCases these at runtime and
#: converts on write; the record on disk is ``snake_case`` everywhere. This
#: tuple is therefore the serialization order too: :meth:`AuditLogger.
#: log_acl_decision` builds its record by iterating it, so dict insertion order
#: carries straight through ``json.dumps``. Reordering these lines is a
#: cross-SDK wire change, not a cosmetic edit.
ACL_AUDIT_ENTRY_FIELDS: tuple[str, ...] = (
    "timestamp",
    "caller_id",
    "target_id",
    "decision",
    "reason",
    "matched_rule",
    "matched_rule_index",
    "identity_type",
    "roles",
    "call_depth",
    "trace_id",
    "handler_error",
    "approval_required",
)


class AuditLogger:
    DEFAULT_PATH = Path.home() / ".apcore-cli" / "audit.jsonl"

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or self.DEFAULT_PATH
        # D11-010: dedup write-failure warnings — warn once per logger
        # instance, matching TS `writeFailureWarned` flag for cross-SDK parity.
        self._write_failure_warned: bool = False
        self._ensure_directory()

    def _ensure_directory(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self._path.parent, 0o700)

    def log_execution(
        self,
        module_id: str,
        input_data: dict,
        status: Literal["success", "error"],
        exit_code: int,
        duration_ms: int,
    ) -> None:
        entry = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "user": self._get_user(),
            "module_id": module_id,
            "input_hash": self._hash_input(input_data),
            "status": status,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
        }
        self._append(entry)

    def log_acl_decision(self, entry: Any) -> None:
        """Append one apcore ``AuditEntry`` to the log (FE-14 §4.8).

        The 13 fields are written **verbatim** in their ``snake_case`` wire
        form. Nothing is renamed, nothing is dropped, and nothing of the CLI's
        own is added: an ACL decision record is apcore's structure, and a
        consumer reading this file should be able to hold it against
        ``AuditEntry`` rather than against a CLI-specific dialect of it.

        ``roles`` is a tuple on the dataclass and is normalized to a list so
        the JSON shape does not depend on the host language's tuple encoding.

        Whether a *denied* decision reaches this method at all is decided one
        level up, by ``acl.audit.include_denied`` — see
        :func:`apcore_cli.acl_loader.make_acl_audit_logger`. This method logs
        what it is given.
        """
        record: dict[str, Any] = {name: getattr(entry, name, None) for name in ACL_AUDIT_ENTRY_FIELDS}
        record["roles"] = list(record["roles"] or ())
        self._append(record)

    def _append(self, entry: dict[str, Any]) -> None:
        """Write one JSONL record, keeping the file at ``0600``.

        Shared by :meth:`log_execution` and :meth:`log_acl_decision` so the
        permission tightening and the warn-once-per-instance dedup cannot
        drift between the two record kinds.
        """
        try:
            with open(self._path, "a") as f:
                f.write(json.dumps(entry) + "\n")
            with contextlib.suppress(OSError):
                os.chmod(self._path, 0o600)
        except OSError as e:
            if not self._write_failure_warned:
                logger.warning("Could not write audit log: %s", e)
                self._write_failure_warned = True

    def _hash_input(self, input_data: dict) -> str:
        """Hash input with a random salt to prevent correlation across invocations."""
        salt = secrets.token_bytes(16)
        payload = json.dumps(input_data, sort_keys=True).encode()
        return hashlib.sha256(salt + payload).hexdigest()

    def _get_user(self) -> str:
        # Canonical fallback chain (cross-SDK parity, security.md / D11-W1):
        # getlogin -> pwd.getpwuid(geteuid).pw_name -> USER -> LOGNAME
        # -> USERNAME -> "unknown".
        #
        # D11-010 (2026-05-12): use geteuid() (effective UID) rather than
        # getuid() (real UID) so that under sudo / setuid binaries the audit
        # record reflects the privileges the process actually runs with —
        # matching Rust (nix::unistd::geteuid) and TypeScript (os.userInfo,
        # which queries the effective UID).
        try:
            return os.getlogin()
        except OSError:
            pass
        try:
            import pwd

            return pwd.getpwuid(os.geteuid()).pw_name
        except (ImportError, KeyError, AttributeError):
            pass
        return os.getenv("USER") or os.getenv("LOGNAME") or os.getenv("USERNAME") or "unknown"


# Module-level singleton, installed by the CLI at init time (factory.py's
# create_cli()) and read by any module that needs to record execution or ACL
# decision entries. Lives here rather than in cli.py because acl_loader.py
# also needs to read it — acl_loader is imported by cli.py at module level,
# so a getter defined in cli.py would make cli -> acl_loader -> cli a real
# import cycle. security.audit imports nothing from either module, so it is
# a safe shared dependency for both.
_audit_logger: AuditLogger | None = None


def set_audit_logger(audit_logger: AuditLogger | None) -> None:
    """Set the global audit logger instance. Pass None to clear."""
    global _audit_logger
    _audit_logger = audit_logger


def get_audit_logger() -> AuditLogger | None:
    """Return the logger installed by :func:`set_audit_logger`, or ``None``.

    Read late rather than captured: the FE-14 §4.8 ACL audit callback is built
    once, at ACL load time, but fires on every ``check_access`` thereafter, so
    binding the logger at construction would freeze whichever instance existed
    then — including ``None`` for an embedder that installs its own logger
    after ``create_cli`` returns.
    """
    return _audit_logger
