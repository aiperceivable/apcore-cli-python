"""Tests for Sandbox (FE-05)."""

import io
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from apcore_cli.security.sandbox import ModuleExecutionError, Sandbox


class _FakePopen:
    """Minimal :class:`subprocess.Popen` stand-in for sandbox tests.

    Streaming reads of ``stdout`` and ``stderr`` are served from in-memory
    :class:`io.BytesIO` buffers. ``stdin.write`` is captured but discarded.
    Records the constructor kwargs so tests can assert on the env that was
    passed at process spawn time.
    """

    last_call: "_FakePopen | None" = None

    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self._stdout_data = stdout
        self._stderr_data = stderr
        self.returncode = returncode
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(self._stdout_data)
        self.stderr = io.BytesIO(self._stderr_data)
        self.killed = False
        self.call_args: dict | None = None

    def __call__(self, *args, **kwargs):
        # Allow the instance to act as the Popen constructor in patching.
        self.call_args = kwargs
        type(self).last_call = self
        return self

    def wait(self, timeout=None):  # noqa: ARG002
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


@contextmanager
def patch_popen(stdout: bytes = b"{}", stderr: bytes = b"", returncode: int = 0):
    """Patch :func:`subprocess.Popen` used by :mod:`sandbox` with a fake.

    Yields the :class:`_FakePopen` instance so tests can read ``call_args``.
    """
    fake = _FakePopen(stdout=stdout, stderr=stderr, returncode=returncode)
    with patch("apcore_cli.security.sandbox.subprocess.Popen", new=fake):
        yield fake


class TestSandbox:
    def test_sandbox_disabled_direct_execution(self):
        executor = MagicMock()
        executor.call.return_value = {"result": 42}
        sandbox = Sandbox(enabled=False)
        result = sandbox.execute("math.add", {"a": 5}, executor)
        assert result == {"result": 42}
        executor.call.assert_called_once_with("math.add", {"a": 5})

    def test_sandbox_enabled_subprocess(self):
        sandbox = Sandbox(enabled=True)
        with patch_popen(stdout=b'{"result": 42}'):
            result = sandbox._sandboxed_execute("math.add", {"a": 5})
        assert result == {"result": 42}

    def test_sandbox_restricted_env(self):
        sandbox = Sandbox(enabled=True)
        with patch_popen() as fake:
            sandbox._sandboxed_execute("mod", {})
            env = fake.call_args.get("env")
            # HOME should be a temp dir, not the real home
            assert env.get("HOME", "").startswith("/")
            # Should not have random env vars
            assert "EDITOR" not in env

    def test_sandbox_subprocess_failure(self):
        sandbox = Sandbox(enabled=True)
        with (
            patch_popen(stdout=b"", stderr=b"module error", returncode=1),
            pytest.raises(ModuleExecutionError, match="execution failed"),
        ):
            sandbox._sandboxed_execute("mod", {})

    def test_sandbox_result_parsing(self):
        sandbox = Sandbox(enabled=True)
        with patch_popen(stdout=b'{"key": "value", "num": 123}'):
            result = sandbox._sandboxed_execute("mod", {})
        assert result == {"key": "value", "num": 123}

    def test_sandbox_non_json_stdout_on_success_raises_module_execution_error(self):
        """W9: a subprocess that exits 0 but emits non-JSON stdout must surface
        as the documented ModuleExecutionError contract, not raw JSONDecodeError."""
        sandbox = Sandbox(enabled=True)
        with (
            patch_popen(stdout=b"DeprecationWarning: blah\n{not-json-at-all"),
            pytest.raises(ModuleExecutionError, match="non-JSON output"),
        ):
            sandbox._sandboxed_execute("mod", {})

    def test_sandbox_env_does_not_leak_auth_api_key(self):
        """C1: APCORE_AUTH_API_KEY must not reach the sandboxed subprocess."""
        sandbox = Sandbox(enabled=True)
        parent_env = {"APCORE_AUTH_API_KEY": "secret-token", "PATH": "/usr/bin"}
        with patch_popen() as fake:
            with patch.dict("os.environ", parent_env, clear=True):
                sandbox._sandboxed_execute("mod", {})
            call_env = fake.call_args.get("env")
            assert "APCORE_AUTH_API_KEY" not in call_env

    def test_sandbox_env_forwards_allowed_apcore_vars(self):
        """Non-secret APCORE_* vars in the allowlist must still be forwarded."""
        sandbox = Sandbox(enabled=True)
        parent_env = {
            "APCORE_EXTENSIONS_ROOT": "/some/path",
            "APCORE_LOG_LEVEL": "DEBUG",
            "APCORE_AUTH_API_KEY": "secret",
            "PATH": "/usr/bin",
        }
        with patch_popen() as fake:
            with patch.dict("os.environ", parent_env, clear=True):
                sandbox._sandboxed_execute("mod", {})
            call_env = fake.call_args.get("env")
            assert "APCORE_LOG_LEVEL" in call_env
            assert "APCORE_AUTH_API_KEY" not in call_env

    def test_sandbox_extensions_root_injected_as_absolute(self):
        """C2: extensions_root kwarg must be injected as APCORE_EXTENSIONS_ROOT
        with an absolute path so module discovery works under cwd=tmpdir."""
        sandbox = Sandbox(enabled=True).with_extensions_root("/abs/extensions")
        with patch_popen() as fake:
            with patch.dict("os.environ", {}, clear=True):
                sandbox._sandboxed_execute("mod", {})
            call_env = fake.call_args.get("env")
            assert call_env.get("APCORE_EXTENSIONS_ROOT") == "/abs/extensions"

    def test_sandbox_extensions_root_env_var_resolved_to_absolute(self):
        """C2: a relative APCORE_EXTENSIONS_ROOT env var must be resolved to
        an absolute path before injecting it into the subprocess env."""
        sandbox = Sandbox(enabled=True)
        parent_env = {"APCORE_EXTENSIONS_ROOT": "./extensions", "PATH": "/usr/bin"}
        with patch_popen() as fake:
            with patch.dict("os.environ", parent_env, clear=True):
                sandbox._sandboxed_execute("mod", {})
            call_env = fake.call_args.get("env")
            assert call_env.get("APCORE_EXTENSIONS_ROOT", "").startswith("/")

    def test_sandbox_output_size_limit_raises(self):
        """W6 (D3): oversized subprocess output must raise ModuleExecutionError."""
        sandbox = Sandbox(enabled=True).with_max_output_bytes(100)
        big_stdout = b"x" * 200
        with (
            patch_popen(stdout=big_stdout),
            pytest.raises(ModuleExecutionError, match="exceeded"),
        ):
            sandbox._sandboxed_execute("mod", {})

    def test_sandbox_per_stream_caps_allow_combined_under_limit(self):
        """D10-001: stdout and stderr have independent caps. Combined size
        beyond the cap must be allowed when each stream stays under the cap.

        Under the previous combined check, two ~0.6x streams (1.2x total)
        would raise. Per-stream caps match Rust and TS — each must clear.
        The result dict is small JSON; stdout is sized only by JSON content,
        so we exercise the cap via stderr while keeping stdout valid JSON.
        """
        cap = 100
        sandbox = Sandbox(enabled=True).with_max_output_bytes(cap)
        # stderr ~0.6x of cap; stdout valid JSON well under cap.
        stderr_blob = b"e" * int(cap * 0.6)
        stdout_blob = b'{"ok": true, "padding": "' + (b"s" * int(cap * 0.6 - 30)) + b'"}'
        # Sanity: each stream under cap, combined exceeds it.
        assert len(stderr_blob) < cap
        assert len(stdout_blob) < cap
        assert len(stderr_blob) + len(stdout_blob) > cap
        with patch_popen(stdout=stdout_blob, stderr=stderr_blob, returncode=0):
            result = sandbox._sandboxed_execute("mod", {})
        assert result["ok"] is True

    def test_sandbox_per_stream_cap_stdout_overflow_names_stream(self):
        """D10-001: when stdout alone exceeds the cap the error message must
        identify ``stdout`` as the offending stream."""
        sandbox = Sandbox(enabled=True).with_max_output_bytes(50)
        with (
            patch_popen(stdout=b"x" * 200, stderr=b""),
            pytest.raises(ModuleExecutionError, match="stdout exceeded"),
        ):
            sandbox._sandboxed_execute("mod", {})

    def test_sandbox_per_stream_cap_stderr_overflow_names_stream(self):
        """D10-001: when stderr alone exceeds the cap the error message must
        identify ``stderr`` as the offending stream."""
        sandbox = Sandbox(enabled=True).with_max_output_bytes(50)
        with (
            patch_popen(stdout=b'{"ok": true}', stderr=b"e" * 200),
            pytest.raises(ModuleExecutionError, match="stderr exceeded"),
        ):
            sandbox._sandboxed_execute("mod", {})

    def test_sandbox_streaming_kills_child_on_stdout_overflow(self):
        """D11-W2: when stdout overflows the cap mid-read the child process
        must be killed rather than allowed to fill parent memory."""
        sandbox = Sandbox(enabled=True).with_max_output_bytes(50)
        with patch_popen(stdout=b"x" * 1024, stderr=b"") as fake:
            with pytest.raises(ModuleExecutionError):
                sandbox._sandboxed_execute("mod", {})
            assert fake.killed is True

    def test_sandbox_does_not_forward_pythonpath(self):
        """D10-010: PYTHONPATH must not appear in the sandboxed subprocess environment."""
        sandbox = Sandbox(enabled=True)
        parent_env = {"PYTHONPATH": "/some/path", "PATH": "/usr/bin"}
        with patch_popen() as fake:
            with patch.dict("os.environ", parent_env, clear=True):
                sandbox._sandboxed_execute("mod", {})
            call_env = fake.call_args.get("env")
            assert "PYTHONPATH" not in call_env, "PYTHONPATH must not be forwarded to the sandbox"

    def test_sandbox_deny_keys_constant_exists(self):
        """D11-007: _SANDBOX_DENY_KEYS constant must be defined and contain APCORE_AUTH_API_KEY."""
        from apcore_cli.security import sandbox as sandbox_module

        assert hasattr(sandbox_module, "_SANDBOX_DENY_KEYS"), "_SANDBOX_DENY_KEYS must be defined in sandbox.py"
        assert "APCORE_AUTH_API_KEY" in sandbox_module._SANDBOX_DENY_KEYS

    def test_sandbox_env_consults_deny_keys_set(self, monkeypatch):
        """D11-002: env construction must consult _SANDBOX_DENY_KEYS as
        defense-in-depth alongside the prefix filter. TS and Rust both apply
        prefix + explicit-key checks; without consulting the deny set, a
        future APCORE_* key that matches the allow prefix but is in the
        explicit deny list would leak into the sandbox child env.

        Reproduction: extend _SANDBOX_DENY_KEYS with a non-AUTH-prefixed key
        that still matches the APCORE_ allow prefix; the sandbox must still
        strip it.
        """
        from apcore_cli.security import sandbox as sandbox_module

        leaky_key = "APCORE_TEST_LEAK"
        # Extend the deny set so the test exercises the deny-keys check
        # (the production guard is unchanged outside this monkeypatch).
        monkeypatch.setattr(
            sandbox_module,
            "_SANDBOX_DENY_KEYS",
            frozenset({"APCORE_AUTH_API_KEY", leaky_key}),
        )

        sandbox = Sandbox(enabled=True)
        parent_env = {leaky_key: "must-not-leak", "APCORE_LOG_LEVEL": "DEBUG", "PATH": "/usr/bin"}
        with patch_popen() as fake:
            with patch.dict("os.environ", parent_env, clear=True):
                sandbox._sandboxed_execute("mod", {})
            call_env = fake.call_args.get("env")
            # Sanity check: a non-denied APCORE_* key still forwards through.
            assert "APCORE_LOG_LEVEL" in call_env
            # Defense-in-depth: deny-set member must be stripped even though
            # it matches the allow prefix and not the AUTH_ deny prefix.
            assert leaky_key not in call_env, f"{leaky_key} is in _SANDBOX_DENY_KEYS but leaked into the sandbox env"

    def test_sandbox_env_strips_every_deny_key_member(self):
        """D11-002: for every key in the production _SANDBOX_DENY_KEYS, the
        sandbox env build must exclude that key — covers all current and
        future entries without enumerating them in the test."""
        from apcore_cli.security import sandbox as sandbox_module

        deny_keys = list(sandbox_module._SANDBOX_DENY_KEYS)
        assert deny_keys, "_SANDBOX_DENY_KEYS must not be empty"

        sandbox = Sandbox(enabled=True)
        parent_env = {k: "secret" for k in deny_keys}
        parent_env["PATH"] = "/usr/bin"

        with patch_popen() as fake:
            with patch.dict("os.environ", parent_env, clear=True):
                sandbox._sandboxed_execute("mod", {})
            call_env = fake.call_args.get("env")
            for key in deny_keys:
                assert key not in call_env, f"{key} (deny set member) leaked into sandbox env"


class TestConstructorCrossSDKParity:
    """D1-001: Sandbox public constructor is (enabled, timeout_seconds) only.
    Python-only knobs (extensions_root, max_output_bytes) are configured
    through the builder-style ``with_*`` setters so the cross-SDK
    constructor surface matches Rust's ``Sandbox::new(enabled, timeout_secs)``
    and TS's ``new Sandbox(enabled, timeoutSeconds)``.
    """

    def test_constructor_takes_only_two_positional_args(self):
        sandbox = Sandbox(enabled=True, timeout_seconds=42)
        assert sandbox._enabled is True
        assert sandbox._timeout_seconds == 42
        # Defaults for Python-only knobs.
        assert sandbox._extensions_root is None
        assert sandbox._max_output_bytes == Sandbox.DEFAULT_MAX_OUTPUT_BYTES

    def test_constructor_rejects_old_extensions_root_kwarg(self):
        with pytest.raises(TypeError):
            Sandbox(enabled=True, extensions_root="/abs/extensions")  # type: ignore[call-arg]

    def test_constructor_rejects_old_max_output_bytes_kwarg(self):
        with pytest.raises(TypeError):
            Sandbox(enabled=True, max_output_bytes=100)  # type: ignore[call-arg]

    def test_with_extensions_root_returns_self_for_chaining(self):
        sandbox = Sandbox(enabled=True)
        result = sandbox.with_extensions_root("/abs/path")
        assert result is sandbox
        assert sandbox._extensions_root == "/abs/path"

    def test_with_max_output_bytes_returns_self_for_chaining(self):
        sandbox = Sandbox(enabled=True)
        result = sandbox.with_max_output_bytes(1024)
        assert result is sandbox
        assert sandbox._max_output_bytes == 1024

    def test_chained_setters_preserve_each_other(self):
        sandbox = (
            Sandbox(enabled=True, timeout_seconds=60)
            .with_extensions_root("/abs/extensions")
            .with_max_output_bytes(2048)
        )
        assert sandbox._enabled is True
        assert sandbox._timeout_seconds == 60
        assert sandbox._extensions_root == "/abs/extensions"
        assert sandbox._max_output_bytes == 2048
