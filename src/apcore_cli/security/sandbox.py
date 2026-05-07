"""Subprocess-based execution sandboxing (FE-05)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apcore import Executor


class ModuleExecutionError(Exception):
    """Raised when a sandboxed module execution fails."""

    pass


class ModuleNotFoundError(Exception):  # noqa: A001 — intentional shadow of builtin
    """Raised when a module ID is not found in the registry (exit 44).

    Cross-SDK naming parity (D1-002): renamed back to ``ModuleNotFoundError``
    to match TypeScript's ``ModuleNotFoundError`` and Rust's
    ``DiscoveryError::ModuleNotFound``. Importers should access this class
    via the qualified module path (``apcore_cli.security.sandbox``) or a
    namespaced alias (``import apcore_cli as cli; cli.ModuleNotFoundError``)
    rather than ``from apcore_cli import *``, which would shadow
    :class:`builtins.ModuleNotFoundError`.
    """

    pass


# Deprecated alias kept for backward compat — prefer ModuleNotFoundError. Will be removed in v0.10.0.
CliModuleNotFoundError = ModuleNotFoundError


class SchemaValidationError(Exception):
    """Raised when JSON schema validation fails (exit 45).

    Equivalent to TypeScript's ``SchemaValidationError`` and Rust's
    ``RefResolverError::Unresolvable``.
    """

    pass


# Env forwarding strategy (mirrors Rust spec §4.4 and apcore-cli/docs/features/security.md):
# Allow: PATH, LANG, LC_ALL + all APCORE_* vars.
# PYTHONPATH is intentionally excluded (D10-010) — it must not cross the sandbox boundary.
# Deny prefix: APCORE_AUTH_ — credentials must not cross the sandbox trust boundary.
_SANDBOX_ALLOW_KEYS = ("PATH", "LANG", "LC_ALL")
_SANDBOX_ALLOW_PREFIX = "APCORE_"
_SANDBOX_DENY_PREFIX = "APCORE_AUTH_"
_SANDBOX_DENY_KEYS: frozenset[str] = frozenset({"APCORE_AUTH_API_KEY"})


class Sandbox:
    """Subprocess-isolated module execution.

    Cross-SDK constructor parity (D1-001): the public constructor is
    ``Sandbox(enabled, timeout_seconds)`` mirroring
    apcore-cli-rust ``Sandbox::new(enabled, timeout_secs)`` and
    apcore-cli-typescript ``new Sandbox(enabled, timeoutSeconds)``.
    Python-only hardening knobs (``extensions_root``,
    ``max_output_bytes``) are configured post-construction via the
    builder-style ``with_*`` setters below; they remain Python-only
    until cross-SDK parity for the corresponding settings lands.

    When ``enabled=False``, :meth:`execute` is a passthrough to the
    injected apcore Executor.
    """

    DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024 * 1024

    def __init__(self, enabled: bool = False, timeout_seconds: int = 300) -> None:
        self._enabled = enabled
        self._timeout_seconds = timeout_seconds
        self._extensions_root: str | None = None
        self._max_output_bytes: int = self.DEFAULT_MAX_OUTPUT_BYTES

    def with_extensions_root(self, extensions_root: str | None) -> Sandbox:
        """Set the extensions root that is forwarded to the sandboxed runner.

        Builder-style — returns ``self`` so call sites can chain. Python-only
        knob; no equivalent in the Rust or TypeScript SDKs at this writing
        (D1-001 cross-SDK parity note in apcore-cli/docs/features/security.md).
        """
        self._extensions_root = extensions_root
        return self

    def with_max_output_bytes(self, max_output_bytes: int) -> Sandbox:
        """Cap the post-capture stdout+stderr byte budget for the sandboxed
        subprocess. Default: 64 MiB (:attr:`DEFAULT_MAX_OUTPUT_BYTES`).

        Builder-style — returns ``self``. Python-only knob; no equivalent
        in the Rust or TypeScript SDKs at this writing.
        """
        self._max_output_bytes = max_output_bytes
        return self

    def execute(self, module_id: str, input_data: dict, executor: Executor) -> Any:
        if not self._enabled:
            return executor.call(module_id, input_data)
        return self._sandboxed_execute(module_id, input_data)

    def _sandboxed_execute(self, module_id: str, input_data: dict) -> Any:
        env: dict[str, str] = {}
        for key in _SANDBOX_ALLOW_KEYS:
            if key in os.environ:
                env[key] = os.environ[key]
        # Forward all APCORE_* vars except the APCORE_AUTH_* deny prefix and
        # any explicit deny-listed key (D11-002 — defense-in-depth parity with
        # the TS and Rust sandboxes, which both apply prefix + explicit-key
        # filtering). The explicit deny set covers entries that match the
        # allow prefix but should never cross the sandbox trust boundary.
        for key, val in os.environ.items():
            if (
                key.startswith(_SANDBOX_ALLOW_PREFIX)
                and not key.startswith(_SANDBOX_DENY_PREFIX)
                and key not in _SANDBOX_DENY_KEYS
            ):
                env[key] = val

        # Inject extensions root as an absolute path so the runner locates
        # modules correctly even when cwd is changed to the sandbox tempdir.
        if self._extensions_root is not None:
            env["APCORE_EXTENSIONS_ROOT"] = str(Path(self._extensions_root).resolve())
        elif "APCORE_EXTENSIONS_ROOT" in env:
            env["APCORE_EXTENSIONS_ROOT"] = str(Path(env["APCORE_EXTENSIONS_ROOT"]).resolve())

        with tempfile.TemporaryDirectory(prefix="apcore_sandbox_") as tmpdir:
            env["HOME"] = tmpdir
            env["TMPDIR"] = tmpdir

            try:
                result = subprocess.run(
                    [sys.executable, "-m", "apcore_cli._sandbox_runner", module_id],
                    input=json.dumps(input_data),
                    capture_output=True,
                    text=True,
                    env=env,
                    cwd=tmpdir,
                    timeout=self._timeout_seconds,
                )
            except subprocess.TimeoutExpired as err:
                raise ModuleExecutionError(f"Error: Module '{module_id}' timed out in sandbox.") from err

            # Enforce output size cap (post-capture soft limit; for a hard cap
            # that prevents memory accumulation, use Popen-based streaming).
            total_bytes = len(result.stdout.encode()) + len(result.stderr.encode())
            if total_bytes > self._max_output_bytes:
                limit_mb = self._max_output_bytes // (1024 * 1024)
                raise ModuleExecutionError(
                    f"Error: Module '{module_id}' output exceeded the {limit_mb}MB sandbox limit."
                )

            if result.returncode != 0:
                raise ModuleExecutionError(f"Error: Module '{module_id}' execution failed: {result.stderr}")

            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError as err:
                preview = result.stdout[:200]
                raise ModuleExecutionError(f"Error: Module '{module_id}' returned non-JSON output: {preview}") from err
