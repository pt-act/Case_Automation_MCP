"""AES-256-GCM encryption with envelope key management — spec §4.6, §5.4.

Envelope pattern:
  - Key Encryption Key (KEK) — loaded from the secret store at startup.
  - Data Encryption Key (DEK) — one per application instance, randomly
    generated on first use or loaded from a pre-existing wrapped form.
  - Wrapped DEK — AES-256-GCM(DEK, KEK).  Stored in settings / secret store.

Rotation:  supply a new KEK → re-wrap the DEK → historical ciphertexts
still decrypt (same DEK, new wrapping).

Wire-format for `encrypt` output: base64(nonce[12] + tag[16] + ciphertext).
"""

from __future__ import annotations

import base64
import os
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

if TYPE_CHECKING:
    pass

_NONCE_SIZE = 12  # 96 bits — recommended for AES-GCM
_KEY_SIZE = 32    # 256 bits


class DecryptionError(Exception):
    """Raised when decryption fails (wrong key, tampered ciphertext, or bad nonce)."""


class CryptoService:
    """Application-level AES-256-GCM encryption service.

    Usage::

        svc = CryptoService(kek_bytes)
        ciphertext = svc.encrypt(b"plaintext")
        plaintext = svc.decrypt(ciphertext)
    """

    def __init__(self, kek: bytes) -> None:
        if len(kek) != _KEY_SIZE:
            raise ValueError(f"KEK must be exactly {_KEY_SIZE} bytes; got {len(kek)}.")
        self._kek = kek
        self._dek: bytes | None = None

    # ------------------------------------------------------------------
    # DEK management (envelope)
    # ------------------------------------------------------------------

    def _get_dek(self) -> bytes:
        if self._dek is None:
            self._dek = os.urandom(_KEY_SIZE)
        return self._dek

    def wrap_dek(self) -> bytes:
        """Encrypt the DEK with the KEK.  Store the returned bytes in the
        secret store so the DEK survives restarts."""
        dek = self._get_dek()
        nonce = os.urandom(_NONCE_SIZE)
        aesgcm = AESGCM(self._kek)
        ciphertext = aesgcm.encrypt(nonce, dek, None)
        return base64.b64encode(nonce + ciphertext)

    def load_wrapped_dek(self, wrapped: bytes) -> None:
        """Decrypt a previously wrapped DEK and set it as the active DEK."""
        raw = base64.b64decode(wrapped)
        nonce = raw[:_NONCE_SIZE]
        ciphertext = raw[_NONCE_SIZE:]
        aesgcm = AESGCM(self._kek)
        try:
            self._dek = aesgcm.decrypt(nonce, ciphertext, None)
        except Exception as exc:
            raise DecryptionError("Failed to unwrap DEK — wrong KEK or tampered blob.") from exc

    def rotate_kek(self, new_kek: bytes) -> bytes:
        """Re-wrap the current DEK under a new KEK.  Returns the new wrapped DEK.
        Historical ciphertexts decrypt unchanged (same DEK)."""
        if len(new_kek) != _KEY_SIZE:
            raise ValueError(f"New KEK must be {_KEY_SIZE} bytes.")
        _ = self._get_dek()  # ensure DEK exists
        old_kek, self._kek = self._kek, new_kek
        try:
            return self.wrap_dek()
        except Exception:
            self._kek = old_kek
            raise

    # ------------------------------------------------------------------
    # Data encryption / decryption
    # ------------------------------------------------------------------

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt plaintext bytes.  Returns base64(nonce + tag + ciphertext)."""
        dek = self._get_dek()
        nonce = os.urandom(_NONCE_SIZE)
        aesgcm = AESGCM(dek)
        ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext, None)
        return base64.b64encode(nonce + ciphertext_with_tag)

    def decrypt(self, token: bytes) -> bytes:
        """Decrypt a token produced by `encrypt`.  Raises DecryptionError on failure."""
        try:
            raw = base64.b64decode(token)
            nonce = raw[:_NONCE_SIZE]
            ciphertext_with_tag = raw[_NONCE_SIZE:]
            dek = self._get_dek()
            aesgcm = AESGCM(dek)
            return aesgcm.decrypt(nonce, ciphertext_with_tag, None)
        except DecryptionError:
            raise
        except Exception as exc:
            raise DecryptionError("Decryption failed — wrong key or tampered ciphertext.") from exc


# ---------------------------------------------------------------------------
# Module-level singleton (initialised at startup via configure_crypto)
# ---------------------------------------------------------------------------

_service: CryptoService | None = None


def configure_crypto(kek: bytes, wrapped_dek: bytes | None = None) -> None:
    """Initialise the module-level CryptoService.  Call once at startup."""
    global _service
    svc = CryptoService(kek)
    if wrapped_dek:
        svc.load_wrapped_dek(wrapped_dek)
    _service = svc


def get_crypto_service() -> CryptoService:
    if _service is None:
        raise RuntimeError(
            "CryptoService is not initialised.  Call configure_crypto() at startup."
        )
    return _service


# ---------------------------------------------------------------------------
# Convenience functions (delegate to module singleton)
# ---------------------------------------------------------------------------


def encrypt(plaintext: bytes) -> bytes:
    return get_crypto_service().encrypt(plaintext)


def decrypt(token: bytes) -> bytes:
    return get_crypto_service().decrypt(token)


# ---------------------------------------------------------------------------
# SQLAlchemy EncryptedStr column type
# ---------------------------------------------------------------------------

from sqlalchemy import String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class EncryptedStr(TypeDecorator[str]):
    """SQLAlchemy column type that transparently encrypts on write and
    decrypts on read using the module-level CryptoService.

    At-rest value:  base64(nonce + tag + ciphertext) stored as TEXT.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return encrypt(value.encode()).decode()

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return decrypt(value.encode()).decode()
