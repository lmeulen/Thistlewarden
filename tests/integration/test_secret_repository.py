from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

import pytest

from evaluator.database import SQLiteConnectionFactory
from evaluator.logging_config import configure_logging, log_context
from evaluator.migrations import MigrationManager
from evaluator.secrets import EncryptionKeyring, SecretEncryptionService, SQLiteSecretRepository

pytestmark = pytest.mark.integration


@pytest.fixture
def repository(tmp_path: Path):
    factory = SQLiteConnectionFactory(tmp_path / "secrets.db")
    MigrationManager(factory).migrate()
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    repository = SQLiteSecretRepository(
        factory,
        SecretEncryptionService(EncryptionKeyring.from_base64(key)),
    )
    return factory, repository


def test_repository_round_trip_stores_no_plaintext(repository) -> None:
    factory, secrets = repository
    record_id = secrets.create("api_key", "fixture-secret")

    record = secrets.get(record_id)
    assert record is not None
    assert record.value == "fixture-secret"
    assert record.key_version == 1

    database_bytes = factory.database_path.read_bytes()
    assert b"fixture-secret" not in database_bytes
    connection = factory.connect()
    try:
        row = connection.execute(
            "SELECT kind, ciphertext, nonce, key_version FROM secret_records WHERE id = ?",
            (str(record_id),),
        ).fetchone()
        assert row["kind"] == "api_key"
        assert bytes(row["ciphertext"]) != b"fixture-secret"
        assert len(row["nonce"]) == 12
        assert row["key_version"] == 1
    finally:
        connection.close()


def test_empty_update_preserves_existing_secret(repository) -> None:
    _, secrets = repository
    record_id = secrets.create("header", "original-secret")

    assert secrets.update(record_id, "") is False
    assert secrets.get(record_id).value == "original-secret"
    assert secrets.update(record_id, "replacement-secret") is True
    assert secrets.get(record_id).value == "replacement-secret"


def test_provider_failure_logs_only_allowlisted_metadata(repository, capsys) -> None:
    _, _ = repository
    configure_logging("INFO", secrets=("fixture-secret",))
    logger = logging.getLogger("evaluator.provider")

    with log_context(run_id="run-1", case_id="case-1", execution_id="exec-1"):
        try:
            raise RuntimeError("provider body fixture-secret prompt-content")
        except RuntimeError:
            # Provider boundary logs category and IDs, never exception/body text or exc_info.
            logger.error("Provider request failed category=%s", "network_error")

    output = capsys.readouterr().err
    assert "run_id=run-1" in output
    assert "case_id=case-1" in output
    assert "execution_id=exec-1" in output
    assert "fixture-secret" not in output
    assert "prompt-content" not in output
    assert "provider body" not in output
