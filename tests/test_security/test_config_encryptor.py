"""Tests for ConfigEncryptor (FE-05)."""

import base64
from unittest.mock import MagicMock, patch

import pytest

from apcore_cli.security.config_encryptor import ConfigDecryptionError, ConfigEncryptor


class TestConfigEncryptor:
    def test_aes_roundtrip(self):
        enc = ConfigEncryptor()
        plaintext = "my_secret_api_key_12345"
        ciphertext = enc._aes_encrypt(plaintext)
        decrypted = enc._aes_decrypt(ciphertext)
        assert decrypted == plaintext

    def test_store_without_keyring(self):
        enc = ConfigEncryptor()
        with patch.object(enc, "_keyring_available", return_value=False):
            result = enc.store("auth.api_key", "secret123")
        assert result.startswith("enc:")

    def test_store_with_keyring(self):
        enc = ConfigEncryptor()
        mock_kr = MagicMock()
        with (
            patch.object(enc, "_keyring_available", return_value=True),
            patch.dict("sys.modules", {"keyring": mock_kr}),
        ):
            result = enc.store("auth.api_key", "secret123")
        assert result == "keyring:auth.api_key"

    def test_retrieve_enc_v2_ref(self):
        enc = ConfigEncryptor()
        ct = enc._aes_encrypt("my_secret")
        enc_ref = f"enc:v2:{base64.b64encode(ct).decode()}"
        result = enc.retrieve(enc_ref, "auth.api_key")
        assert result == "my_secret"

    def test_retrieve_plaintext(self):
        enc = ConfigEncryptor()
        result = enc.retrieve("plain_value", "some.key")
        assert result == "plain_value"

    def test_retrieve_corrupted_ciphertext(self):
        enc = ConfigEncryptor()
        bad_ct = base64.b64encode(b"corrupted_data").decode()
        with pytest.raises(ConfigDecryptionError, match="Failed to decrypt"):
            enc.retrieve(f"enc:{bad_ct}", "auth.api_key")

    def test_keyring_available_returns_bool(self):
        enc = ConfigEncryptor()
        result = enc._keyring_available()
        assert isinstance(result, bool)

    def test_retrieve_malformed_base64_raises_config_decryption_error(self):
        """W8: binascii.Error from bad base64 must be wrapped, not bubbled."""
        enc = ConfigEncryptor()
        # Not valid base64 — raw base64.b64decode would raise binascii.Error.
        with pytest.raises(ConfigDecryptionError, match="Failed to decrypt"):
            enc.retrieve("enc:not!valid!base64!@@", "auth.api_key")

    def test_retrieve_non_utf8_ciphertext_raises_config_decryption_error(self):
        """W8: UnicodeDecodeError from non-UTF8 plaintext must be wrapped."""
        from unittest.mock import patch

        enc = ConfigEncryptor()
        # Force _aes_decrypt to return bytes that fail UTF-8 decode — simulate
        # the case where ciphertext successfully AES-GCM-decrypts but the
        # plaintext is invalid UTF-8 (e.g., a binary secret stored incorrectly).
        with patch.object(enc, "_aes_decrypt", side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "bad")):
            ct_b64 = base64.b64encode(b"\x00" * 32).decode()
            with pytest.raises(ConfigDecryptionError, match="Failed to decrypt"):
                enc.retrieve(f"enc:{ct_b64}", "auth.api_key")

    # --- regression tests for A-D-001/002: enc:v2 + 600k PBKDF2 ---

    def test_store_without_keyring_writes_v2_prefix(self):
        """A-D-001: store() without keyring must emit enc:v2: not enc:."""
        enc = ConfigEncryptor()
        with patch.object(enc, "_keyring_available", return_value=False):
            result = enc.store("auth.api_key", "secret123")
        assert result.startswith("enc:v2:"), f"Expected enc:v2: prefix, got: {result[:20]}"

    def test_pbkdf2_uses_600k_iterations(self):
        """A-D-002: _derive_key must use 600,000 PBKDF2-HMAC-SHA256 iterations."""
        import hashlib as _hashlib

        with patch.object(_hashlib, "pbkdf2_hmac", wraps=_hashlib.pbkdf2_hmac) as mock_pbkdf2:
            enc = ConfigEncryptor()
            enc._aes_encrypt("test")
            assert mock_pbkdf2.called
            call_kwargs = mock_pbkdf2.call_args
            iterations = call_kwargs[1].get("iterations") or call_kwargs[0][3]
            assert iterations == 600_000, f"Expected 600_000 iterations, got {iterations}"

    def test_v2_store_retrieve_roundtrip(self):
        """A-D-001: enc:v2 store → retrieve roundtrip must work."""
        enc = ConfigEncryptor()
        with patch.object(enc, "_keyring_available", return_value=False):
            stored = enc.store("auth.api_key", "round_trip_value_123")
        assert stored.startswith("enc:v2:")
        recovered = enc.retrieve(stored, "auth.api_key")
        assert recovered == "round_trip_value_123"

    def test_v1_enc_backward_compat_read(self):
        """A-D-001: enc: (v1) values written by older SDK must still be readable."""
        # Construct a v1-format enc: value using the old static-salt + 600k method
        import base64 as _b64
        import hashlib as _hl
        import os as _os
        import socket as _sock

        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        hostname = _sock.gethostname()
        username = _os.getenv("USER", _os.getenv("USERNAME", "unknown"))
        material = f"{hostname}:{username}".encode()
        static_salt = b"apcore-cli-config-v1"
        key = _hl.pbkdf2_hmac("sha256", material, static_salt, iterations=600_000)
        nonce = _os.urandom(12)
        encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        ct = encryptor.update(b"legacy_secret") + encryptor.finalize()
        tag = encryptor.tag
        raw = nonce + tag + ct
        v1_ref = f"enc:{_b64.b64encode(raw).decode()}"

        enc = ConfigEncryptor()
        result = enc.retrieve(v1_ref, "auth.api_key")
        assert result == "legacy_secret"

    def test_v1_decrypt_uses_passphrase_when_set(self):
        """D11-003: when APCORE_CLI_CONFIG_PASSPHRASE is set, v1 ciphertexts
        encrypted under that passphrase must decrypt — Python previously
        hard-coded the host:user material and silently failed for v1 payloads
        written by Rust/TS under a passphrase.
        """
        import base64 as _b64
        import hashlib as _hl
        import os as _os

        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        passphrase = "shared-passphrase-from-rust-or-ts"
        static_salt = b"apcore-cli-config-v1"
        key = _hl.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), static_salt, iterations=600_000)
        nonce = _os.urandom(12)
        encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        ct = encryptor.update(b"cross_sdk_secret") + encryptor.finalize()
        tag = encryptor.tag
        v1_ref = f"enc:{_b64.b64encode(nonce + tag + ct).decode()}"

        enc = ConfigEncryptor()
        with patch.dict(_os.environ, {"APCORE_CLI_CONFIG_PASSPHRASE": passphrase}, clear=False):
            result = enc.retrieve(v1_ref, "auth.api_key")
        assert result == "cross_sdk_secret"

    def test_config_encryptor_uses_passphrase_env_var(self):
        """D10-004: Key derived with APCORE_CLI_CONFIG_PASSPHRASE must differ from hostname:user key."""
        import os

        enc = ConfigEncryptor()
        salt = os.urandom(16)
        key_without = enc._derive_key(salt)
        with patch.dict(os.environ, {"APCORE_CLI_CONFIG_PASSPHRASE": "test_passphrase"}, clear=False):
            key_with = enc._derive_key(salt)
        assert (
            key_without != key_with
        ), "Key derived with APCORE_CLI_CONFIG_PASSPHRASE must differ from hostname:user key"

    def test_config_encryptor_passphrase_roundtrip(self):
        """D10-004: Encrypt+decrypt must work consistently when passphrase env var is set."""
        import os

        enc = ConfigEncryptor()
        plaintext = "my_secret_value"
        with patch.dict(os.environ, {"APCORE_CLI_CONFIG_PASSPHRASE": "my_passphrase"}, clear=False):
            ciphertext = enc._aes_encrypt(plaintext)
            decrypted = enc._aes_decrypt(ciphertext)
        assert decrypted == plaintext

    def test_derive_key_uses_logname_when_user_unset(self, monkeypatch):
        """D10-001: USER → LOGNAME → USERNAME → unknown fallback chain.

        When USER is unset and LOGNAME=alice, derived material must include
        'alice', not 'unknown'.
        """
        import socket

        monkeypatch.delenv("USER", raising=False)
        monkeypatch.delenv("USERNAME", raising=False)
        monkeypatch.delenv("APCORE_CLI_CONFIG_PASSPHRASE", raising=False)
        monkeypatch.setenv("LOGNAME", "alice")

        enc = ConfigEncryptor()
        # Capture material via patching pbkdf2_hmac to inspect input.
        captured: dict[str, bytes] = {}
        import hashlib as _hl

        real_pbkdf2 = _hl.pbkdf2_hmac

        def _spy(algo, material, salt, iterations, dklen=None):
            captured["material"] = material
            return real_pbkdf2(algo, material, salt, iterations) if dklen is None else real_pbkdf2(
                algo, material, salt, iterations, dklen
            )

        with patch.object(_hl, "pbkdf2_hmac", side_effect=_spy):
            enc._derive_key(b"x" * 16)

        hostname = socket.gethostname()
        expected = f"{hostname}:alice".encode()
        assert captured["material"] == expected
        assert b"alice" in captured["material"]
        assert b"unknown" not in captured["material"]

    def test_v1_material_uses_logname_when_user_unset(self, monkeypatch):
        """D10-001: _v1_material() must use USER → LOGNAME → USERNAME → unknown."""
        import socket

        monkeypatch.delenv("USER", raising=False)
        monkeypatch.delenv("USERNAME", raising=False)
        monkeypatch.setenv("LOGNAME", "alice")

        enc = ConfigEncryptor()
        material = enc._v1_material()
        hostname = socket.gethostname()
        assert material == f"{hostname}:alice".encode()
        assert b"alice" in material
        assert b"unknown" not in material

    def test_store_wraps_keyring_set_password_failure(self):
        """D11-004: keyring.set_password failures must be wrapped in ConfigDecryptionError.

        Raw backend errors (e.g. KeyringError, PasswordSetError) leak
        implementation details and confuse users. The wrapper should
        suggest unsetting APCORE_CLI_USE_KEYRING.
        """
        enc = ConfigEncryptor()
        mock_kr = MagicMock()
        mock_kr.set_password.side_effect = RuntimeError("keyring backend not available")
        with (
            patch.object(enc, "_keyring_available", return_value=True),
            patch.dict("sys.modules", {"keyring": mock_kr}),
            pytest.raises(ConfigDecryptionError, match="Failed to store"),
        ):
            enc.store("auth.api_key", "secret123")

    def test_store_keyring_failure_mentions_unset_env_var(self):
        """D11-004: error message must guide the user to unset APCORE_CLI_USE_KEYRING."""
        enc = ConfigEncryptor()
        mock_kr = MagicMock()
        mock_kr.set_password.side_effect = RuntimeError("backend broken")
        with (
            patch.object(enc, "_keyring_available", return_value=True),
            patch.dict("sys.modules", {"keyring": mock_kr}),
        ):
            try:
                enc.store("auth.api_key", "secret123")
            except ConfigDecryptionError as exc:
                assert "APCORE_CLI_USE_KEYRING" in str(exc)
            else:
                pytest.fail("Expected ConfigDecryptionError")

    def test_store_fallback_warning_names_obfuscation_not_encryption(self, caplog):
        """W7: wording correction — log must NOT promise strong 'encryption'."""
        import logging

        enc = ConfigEncryptor()
        with (
            patch.object(enc, "_keyring_available", return_value=False),
            caplog.at_level(logging.WARNING, logger="apcore_cli.security"),
        ):
            enc.store("auth.api_key", "secret123")
        warning_text = " ".join(rec.getMessage() for rec in caplog.records)
        assert "obfuscation" in warning_text.lower()
        assert "NOT strong encryption" in warning_text or "not strong encryption" in warning_text.lower()
