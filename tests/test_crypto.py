"""G5 — Encryption focused tests."""

from __future__ import annotations

import pytest

from cam.security.crypto import CryptoService, DecryptionError

TEST_KEK = b"K" * 32


def make_svc() -> CryptoService:
    return CryptoService(TEST_KEK)


# 1. Round-trip
def test_encrypt_decrypt_roundtrip() -> None:
    svc = make_svc()
    plaintext = b"super-secret-value"
    token = svc.encrypt(plaintext)
    assert svc.decrypt(token) == plaintext


# 2. Wrong key fails
def test_wrong_key_fails() -> None:
    svc1 = CryptoService(b"A" * 32)
    svc2 = CryptoService(b"B" * 32)
    token = svc1.encrypt(b"data")
    with pytest.raises(DecryptionError):
        svc2.decrypt(token)


# 3. Tampered ciphertext fails GCM auth
def test_tampered_ciphertext_fails() -> None:
    svc = make_svc()
    token = bytearray(svc.encrypt(b"original"))
    token[-1] ^= 0xFF  # flip a bit
    with pytest.raises(DecryptionError):
        svc.decrypt(bytes(token))


# 4. Empty input handled
def test_empty_plaintext() -> None:
    svc = make_svc()
    token = svc.encrypt(b"")
    assert svc.decrypt(token) == b""


# 5. Ciphertext != plaintext
def test_ciphertext_differs_from_plaintext() -> None:
    svc = make_svc()
    plaintext = b"hello world"
    token = svc.encrypt(plaintext)
    assert token != plaintext


# 6. DEK wrapping round-trip
def test_dek_wrap_and_load() -> None:
    svc1 = CryptoService(TEST_KEK)
    plaintext = b"wrapped test"
    # ensure DEK is created
    token = svc1.encrypt(plaintext)
    wrapped = svc1.wrap_dek()

    # new service loads wrapped DEK
    svc2 = CryptoService(TEST_KEK)
    svc2.load_wrapped_dek(wrapped)
    assert svc2.decrypt(token) == plaintext


# 7. KEK rotation: historical tokens still decrypt
def test_kek_rotation_historical_tokens() -> None:
    svc = make_svc()
    token = svc.encrypt(b"old message")
    new_kek = b"Z" * 32
    svc.rotate_kek(new_kek)
    # DEK is the same; historical token still decrypts
    assert svc.decrypt(token) == b"old message"


# 8. Wrong KEK for wrapped DEK raises DecryptionError
def test_bad_kek_for_wrapped_dek() -> None:
    svc = make_svc()
    _ = svc.encrypt(b"data")
    wrapped = svc.wrap_dek()

    svc2 = CryptoService(b"B" * 32)
    with pytest.raises(DecryptionError):
        svc2.load_wrapped_dek(wrapped)
