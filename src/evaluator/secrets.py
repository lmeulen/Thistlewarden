"""Authenticated secret encryption and persistent secret records."""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from evaluator.database import SQLiteConnectionFactory, transaction
from evaluator.time import to_utc_iso, utc_now

NONCE_BYTES = 12
KEY_BYTES = 32


class SecretEncryptionError(RuntimeError):
    """Raised when key material or authenticated ciphertext is invalid."""


@dataclass(frozen=True, slots=True)
class EncryptedSecret:
    ciphertext: bytes
    nonce: bytes
    key_version: int


@dataclass(frozen=True, slots=True)
class SecretRecord:
    id: UUID
    kind: str
    value: str
    key_version: int


class EncryptionKeyring:
    """Versioned AES-256 keyring with one active encryption key."""

    def __init__(self, keys: dict[int, bytes], *, active_version: int) -> None:
        if active_version not in keys:
            raise SecretEncryptionError("Active encryption key version is unavailable")
        if not keys or any(version <= 0 for version in keys):
            raise SecretEncryptionError("Key versions must be positive integers")
        if any(len(key) != KEY_BYTES for key in keys.values()):
            raise SecretEncryptionError("Every encryption key must contain exactly 32 bytes")
        self._keys = dict(keys)
        self.active_version = active_version

    @classmethod
    def from_base64(cls, value: str, *, version: int = 1) -> EncryptionKeyring:
        try:
            key = base64.b64decode(value, altchars=b"-_", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SecretEncryptionError(
                "ENCRYPTION_KEY must be valid base64 encoding exactly 32 bytes"
            ) from exc
        if len(key) != KEY_BYTES:
            raise SecretEncryptionError("ENCRYPTION_KEY must decode to exactly 32 bytes")
        return cls({version: key}, active_version=version)

    def key(self, version: int) -> bytes:
        try:
            return self._keys[version]
        except KeyError as exc:
            raise SecretEncryptionError(f"Encryption key version {version} is unavailable") from exc


class SecretEncryptionService:
    """Encrypt secrets with AES-256-GCM and record-bound AAD."""

    def __init__(self, keyring: EncryptionKeyring) -> None:
        self._keyring = keyring

    @staticmethod
    def _aad(record_id: UUID, kind: str, key_version: int) -> bytes:
        if not kind:
            raise SecretEncryptionError("Secret kind must not be empty")
        return f"secret-record|{record_id}|{kind}|v{key_version}".encode()

    def encrypt(self, plaintext: str, *, record_id: UUID, kind: str) -> EncryptedSecret:
        if not plaintext:
            raise SecretEncryptionError("Secret plaintext must not be empty")
        version = self._keyring.active_version
        nonce = os.urandom(NONCE_BYTES)
        ciphertext = AESGCM(self._keyring.key(version)).encrypt(
            nonce,
            plaintext.encode(),
            self._aad(record_id, kind, version),
        )
        return EncryptedSecret(ciphertext, nonce, version)

    def decrypt(self, encrypted: EncryptedSecret, *, record_id: UUID, kind: str) -> str:
        if len(encrypted.nonce) != NONCE_BYTES:
            raise SecretEncryptionError("Encrypted secret has invalid nonce length")
        try:
            plaintext = AESGCM(self._keyring.key(encrypted.key_version)).decrypt(
                encrypted.nonce,
                encrypted.ciphertext,
                self._aad(record_id, kind, encrypted.key_version),
            )
            return plaintext.decode()
        except (InvalidTag, UnicodeDecodeError) as exc:
            raise SecretEncryptionError("Secret authentication failed") from exc


class SQLiteSecretRepository:
    """Store only authenticated ciphertext and metadata in SQLite."""

    def __init__(
        self,
        factory: SQLiteConnectionFactory,
        encryption: SecretEncryptionService,
    ) -> None:
        self._factory = factory
        self._encryption = encryption

    def create(self, kind: str, value: str) -> UUID:
        record_id = uuid4()
        encrypted = self._encryption.encrypt(value, record_id=record_id, kind=kind)
        now = to_utc_iso(utc_now())
        connection = self._factory.connect()
        try:
            with transaction(connection):
                connection.execute(
                    """
                    INSERT INTO secret_records(
                        id, kind, ciphertext, nonce, key_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(record_id),
                        kind,
                        encrypted.ciphertext,
                        encrypted.nonce,
                        encrypted.key_version,
                        now,
                        now,
                    ),
                )
        finally:
            connection.close()
        return record_id

    def get(self, record_id: UUID) -> SecretRecord | None:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                """
                SELECT kind, ciphertext, nonce, key_version
                FROM secret_records WHERE id = ?
                """,
                (str(record_id),),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        kind = str(row["kind"])
        encrypted = EncryptedSecret(
            ciphertext=bytes(row["ciphertext"]),
            nonce=bytes(row["nonce"]),
            key_version=int(row["key_version"]),
        )
        value = self._encryption.decrypt(encrypted, record_id=record_id, kind=kind)
        return SecretRecord(record_id, kind, value, encrypted.key_version)

    def update(self, record_id: UUID, value: str) -> bool:
        if value == "":
            return False
        connection = self._factory.connect()
        try:
            row = connection.execute(
                "SELECT kind FROM secret_records WHERE id = ?", (str(record_id),)
            ).fetchone()
            if row is None:
                return False
            kind = str(row["kind"])
            encrypted = self._encryption.encrypt(value, record_id=record_id, kind=kind)
            with transaction(connection):
                connection.execute(
                    """
                    UPDATE secret_records
                    SET ciphertext = ?, nonce = ?, key_version = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        encrypted.ciphertext,
                        encrypted.nonce,
                        encrypted.key_version,
                        to_utc_iso(utc_now()),
                        str(record_id),
                    ),
                )
            return True
        finally:
            connection.close()

    def delete(self, record_id: UUID) -> bool:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                cursor = connection.execute(
                    "DELETE FROM secret_records WHERE id = ?", (str(record_id),)
                )
            return cursor.rowcount == 1
        finally:
            connection.close()
