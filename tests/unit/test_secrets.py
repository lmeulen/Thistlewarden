from __future__ import annotations

import base64
import os
from uuid import uuid4

import pytest

from evaluator.secrets import (
    EncryptedSecret,
    EncryptionKeyring,
    SecretEncryptionError,
    SecretEncryptionService,
)

pytestmark = pytest.mark.unit


def _key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def test_encrypt_decrypt_round_trip() -> None:
    service = SecretEncryptionService(EncryptionKeyring.from_base64(_key()))
    record_id = uuid4()

    encrypted = service.encrypt("fixture-secret", record_id=record_id, kind="api_key")

    assert service.decrypt(encrypted, record_id=record_id, kind="api_key") == "fixture-secret"
    assert b"fixture-secret" not in encrypted.ciphertext


def test_same_plaintext_uses_different_nonce_and_ciphertext() -> None:
    service = SecretEncryptionService(EncryptionKeyring.from_base64(_key()))
    record_id = uuid4()

    first = service.encrypt("same-secret", record_id=record_id, kind="header")
    second = service.encrypt("same-secret", record_id=record_id, kind="header")

    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext


def test_wrong_key_fails_safely() -> None:
    first = SecretEncryptionService(EncryptionKeyring.from_base64(_key()))
    second = SecretEncryptionService(EncryptionKeyring.from_base64(_key()))
    record_id = uuid4()
    encrypted = first.encrypt("secret", record_id=record_id, kind="api_key")

    with pytest.raises(SecretEncryptionError, match="authentication failed"):
        second.decrypt(encrypted, record_id=record_id, kind="api_key")


def test_tampered_ciphertext_and_aad_fail_safely() -> None:
    service = SecretEncryptionService(EncryptionKeyring.from_base64(_key()))
    record_id = uuid4()
    encrypted = service.encrypt("secret", record_id=record_id, kind="api_key")
    tampered = EncryptedSecret(
        bytes([encrypted.ciphertext[0] ^ 1]) + encrypted.ciphertext[1:],
        encrypted.nonce,
        encrypted.key_version,
    )

    with pytest.raises(SecretEncryptionError):
        service.decrypt(tampered, record_id=record_id, kind="api_key")
    with pytest.raises(SecretEncryptionError):
        service.decrypt(encrypted, record_id=record_id, kind="other_kind")
    with pytest.raises(SecretEncryptionError):
        service.decrypt(encrypted, record_id=uuid4(), kind="api_key")


@pytest.mark.parametrize("value", ["change-me", "", base64.b64encode(b"short").decode()])
def test_invalid_key_format_is_rejected(value: str) -> None:
    with pytest.raises(SecretEncryptionError):
        EncryptionKeyring.from_base64(value)


def test_old_key_version_remains_decryptable() -> None:
    old_key = os.urandom(32)
    new_key = os.urandom(32)
    record_id = uuid4()
    old_service = SecretEncryptionService(EncryptionKeyring({1: old_key}, active_version=1))
    encrypted = old_service.encrypt("old-secret", record_id=record_id, kind="api_key")
    rotated = SecretEncryptionService(EncryptionKeyring({1: old_key, 2: new_key}, active_version=2))

    assert rotated.decrypt(encrypted, record_id=record_id, kind="api_key") == "old-secret"
    assert rotated.encrypt("new-secret", record_id=record_id, kind="api_key").key_version == 2
