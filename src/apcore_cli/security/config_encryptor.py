"""Encrypted configuration storage (FE-05)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import os
import socket

from cryptography.exceptions import InvalidTag

logger = logging.getLogger("apcore_cli.security")

# PBKDF2-HMAC-SHA256 iteration count — follows OWASP 2024+ minimum for SHA-256.
_PBKDF2_ITERATIONS = 600_000

# Static salt used only for backward-compatible reads of legacy enc: (v1) values.
_V1_STATIC_SALT = b"apcore-cli-config-v1"


class ConfigDecryptionError(Exception):
    pass


class ConfigEncryptor:
    SERVICE_NAME = "apcore-cli"

    def store(self, key: str, value: str) -> str:
        if self._keyring_available():
            import keyring as kr

            kr.set_password(self.SERVICE_NAME, key, value)
            return f"keyring:{key}"
        else:
            logger.warning(
                "OS keyring unavailable. Falling back to file-based obfuscation "
                "with a host+user-derived key. This is NOT strong encryption: any "
                "local user who can read the config AND observe hostname+username "
                "can recover the value. Install a real keyring backend "
                "(macOS Keychain / GNOME Keyring / KWallet / Windows Credential "
                "Manager) for real protection."
            )
            ciphertext = self._aes_encrypt(value)
            return f"enc:v2:{base64.b64encode(ciphertext).decode()}"

    def retrieve(self, config_value: str, key: str) -> str:
        if config_value.startswith("keyring:"):
            import keyring as kr

            ref_key = config_value[len("keyring:") :]
            result = kr.get_password(self.SERVICE_NAME, ref_key)
            if result is None:
                raise ConfigDecryptionError(f"Keyring entry not found for '{ref_key}'.")
            return result
        elif config_value.startswith("enc:v2:"):
            try:
                data = base64.b64decode(config_value[len("enc:v2:") :])
                return self._aes_decrypt(data)
            except (InvalidTag, ValueError, binascii.Error, UnicodeDecodeError) as exc:
                raise ConfigDecryptionError(
                    f"Failed to decrypt configuration value '{key}'. Re-configure with 'apcore-cli config set {key}'."
                ) from exc
        elif config_value.startswith("enc:"):
            try:
                data = base64.b64decode(config_value[len("enc:") :])
                return self._aes_decrypt_v1(data)
            except (InvalidTag, ValueError, binascii.Error, UnicodeDecodeError, ConfigDecryptionError) as exc:
                raise ConfigDecryptionError(
                    f"Failed to decrypt configuration value '{key}'. Re-configure with 'apcore-cli config set {key}'."
                ) from exc
        else:
            return config_value

    def _keyring_available(self) -> bool:
        try:
            import keyring as kr

            backend = kr.get_keyring()
            return not (
                hasattr(kr, "backends")
                and hasattr(kr.backends, "fail")
                and isinstance(backend, kr.backends.fail.Keyring)
            )
        except Exception:
            return False

    def _derive_key(self, salt: bytes) -> bytes:
        """Derive a 32-byte AES key using PBKDF2-HMAC-SHA256 with a provided salt."""
        passphrase = os.getenv("APCORE_CLI_CONFIG_PASSPHRASE")
        if passphrase:
            material = passphrase.encode()
        else:
            hostname = socket.gethostname()
            username = os.getenv("USER", os.getenv("USERNAME", "unknown"))
            material = f"{hostname}:{username}".encode()
        return hashlib.pbkdf2_hmac("sha256", material, salt, iterations=_PBKDF2_ITERATIONS)

    def _aes_encrypt(self, plaintext: str) -> bytes:
        """Encrypt using AES-256-GCM with a random per-encryption salt (v2 format).

        Wire layout: salt(16) + nonce(12) + tag(16) + ciphertext.
        """
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        salt = os.urandom(16)
        key = self._derive_key(salt)
        nonce = os.urandom(12)
        encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        ct = encryptor.update(plaintext.encode("utf-8")) + encryptor.finalize()
        tag = encryptor.tag
        # Wire format v2: salt(16) + nonce(12) + tag(16) + ciphertext
        return salt + nonce + tag + ct

    def _aes_decrypt(self, data: bytes) -> str:
        """Decrypt v2-format ciphertext (salt(16) + nonce(12) + tag(16) + ct)."""
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        salt = data[:16]
        nonce = data[16:28]
        tag = data[28:44]
        ct = data[44:]
        key = self._derive_key(salt)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        return (decryptor.update(ct) + decryptor.finalize()).decode("utf-8")

    def _aes_decrypt_v1(self, data: bytes) -> str:
        """Decrypt legacy v1-format ciphertext (nonce(12) + tag(16) + ct).

        D11-003: tries each available key material in turn — when
        ``APCORE_CLI_CONFIG_PASSPHRASE`` is set, the passphrase-derived key
        is attempted first so v1 payloads written by Rust/TS under a
        passphrase decrypt cleanly. The host:user fallback mirrors v1
        ciphertexts written before passphrase support existed. Within each
        material, both 600k (Rust-written v1) and 100k (early Python/TS)
        iteration counts are tried.
        """
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        nonce = data[:12]
        tag = data[12:28]
        ct = data[28:]

        materials: list[bytes] = []
        passphrase = os.getenv("APCORE_CLI_CONFIG_PASSPHRASE")
        if passphrase:
            materials.append(passphrase.encode("utf-8"))
        materials.append(self._v1_material())

        last_exc: Exception = ValueError("no iterations tried")
        for material in materials:
            for iterations in (600_000, 100_000):
                try:
                    key = hashlib.pbkdf2_hmac("sha256", material, _V1_STATIC_SALT, iterations=iterations)
                    decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
                    return (decryptor.update(ct) + decryptor.finalize()).decode("utf-8")
                except (InvalidTag, ValueError, UnicodeDecodeError) as exc:
                    last_exc = exc
                    continue
        raise ConfigDecryptionError("v1 decryption failed") from last_exc

    def _v1_material(self) -> bytes:
        hostname = socket.gethostname()
        username = os.getenv("USER", os.getenv("USERNAME", "unknown"))
        return f"{hostname}:{username}".encode()
