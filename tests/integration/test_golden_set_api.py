from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from evaluator import create_app
from evaluator.golden_sets import SQLiteGoldenSetRepository

pytestmark = pytest.mark.integration


@pytest.fixture
def app(tmp_path: Path):
    return create_app(
        env_file=None,
        settings_environment={
            "SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
            "DATABASE_PATH": str(tmp_path / "golden.db"),
            "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "LOG_LEVEL": "WARNING",
        },
        initialize_database=True,
    )


def _set(client, name: str = "Set A") -> str:
    response = client.post(
        "/api/v1/golden-sets",
        json={"name": name, "description": "Beschrijving", "version": "1.0", "tags": ["support"]},
    )
    assert response.status_code == 201
    return response.get_json()["golden_set"]["id"]


def _item(case_id: str, category: str = "cat", tags: list[str] | None = None) -> dict[str, object]:
    return {
        "case_id": case_id,
        "prompt": f"Prompt {case_id}",
        "expected_answer": "ok",
        "evaluation_method": "exact_match",
        "evaluator_config": {"trim": True},
        "category": category,
        "tags": tags or ["basic"],
        "weight": "1.25",
        "enabled": True,
    }


def test_golden_set_and_item_crud_round_trip(app) -> None:
    client = app.test_client()
    set_id = _set(client)
    created = client.post(f"/api/v1/golden-sets/{set_id}/items", json=_item("case-1"))
    assert created.status_code == 201
    item = created.get_json()["item"]
    assert item["weight"] == "1.25"
    assert item["evaluator_config"]["case_sensitive"] is True
    patched = client.patch(
        f"/api/v1/golden-set-items/{item['id']}", json={"prompt": "Gewijzigd", "enabled": False}
    )
    assert patched.status_code == 200
    assert patched.get_json()["item"]["prompt"] == "Gewijzigd"
    assert client.get(f"/api/v1/golden-sets/{set_id}").status_code == 200
    assert client.patch(f"/api/v1/golden-sets/{set_id}", json={"version": "2.0"}).status_code == 200
    assert client.delete(f"/api/v1/golden-set-items/{item['id']}").status_code == 204
    assert client.delete(f"/api/v1/golden-sets/{set_id}").status_code == 204


def test_duplicate_case_id_is_case_insensitive_conflict(app) -> None:
    client = app.test_client()
    set_id = _set(client)
    assert (
        client.post(f"/api/v1/golden-sets/{set_id}/items", json=_item("Case-A")).status_code == 201
    )
    assert (
        client.post(f"/api/v1/golden-sets/{set_id}/items", json=_item("case-a")).status_code == 409
    )


def test_transactional_reorder_and_delete_compacts_order(app) -> None:
    client = app.test_client()
    set_id = _set(client)
    ids = [
        client.post(f"/api/v1/golden-sets/{set_id}/items", json=_item(f"c-{n}")).get_json()["item"][
            "id"
        ]
        for n in range(3)
    ]
    assert (
        client.patch(f"/api/v1/golden-set-items/{ids[2]}", json={"sort_order": 0}).status_code
        == 200
    )
    items = client.get(f"/api/v1/golden-sets/{set_id}/items?per_page=100").get_json()["items"]
    assert [x["id"] for x in items] == [ids[2], ids[0], ids[1]]
    assert [x["sort_order"] for x in items] == [0, 1, 2]
    assert client.delete(f"/api/v1/golden-set-items/{ids[0]}").status_code == 204
    items = client.get(f"/api/v1/golden-sets/{set_id}/items").get_json()["items"]
    assert [x["sort_order"] for x in items] == [0, 1]


def test_pagination_and_filters(app) -> None:
    client = app.test_client()
    set_id = _set(client)
    for payload in [
        _item("a", "one", ["red"]),
        _item("b", "two", ["blue"]),
        _item("c", "one", ["red", "blue"]),
    ]:
        assert client.post(f"/api/v1/golden-sets/{set_id}/items", json=payload).status_code == 201
    client.patch(
        f"/api/v1/golden-set-items/"
        f"{client.get(f'/api/v1/golden-sets/{set_id}/items?category=one&tag=red').get_json()['items'][0]['id']}",
        json={"enabled": False},
    )
    response = client.get(
        f"/api/v1/golden-sets/{set_id}/items?category=one&tag=red&enabled=true&page=1&per_page=1"
    )
    assert response.status_code == 200
    assert response.get_json()["pagination"] == {"page": 1, "per_page": 1, "total": 1}
    assert client.get("/api/v1/golden-sets?page=0").status_code == 400


def test_database_config_is_revalidated_on_read(app) -> None:
    client = app.test_client()
    set_id = _set(client)
    item_id = client.post(f"/api/v1/golden-sets/{set_id}/items", json=_item("case")).get_json()[
        "item"
    ]["id"]
    factory = app.extensions["database_factory"]
    connection = factory.connect()
    try:
        connection.execute(
            "UPDATE golden_set_items SET evaluator_config_json = ? WHERE id = ?",
            ('{"unknown":true}', item_id),
        )
    finally:
        connection.close()
    repository = SQLiteGoldenSetRepository(factory)
    with pytest.raises(ValidationError):
        repository.get_item(item_id)


def test_input_limits_are_enforced(app) -> None:
    client = app.test_client()
    set_id = _set(client)
    app.extensions["golden_set_service"]._limits = app.extensions[
        "golden_set_service"
    ]._limits.model_copy(update={"max_prompt_chars": 3})
    assert client.post(f"/api/v1/golden-sets/{set_id}/items", json=_item("long")).status_code == 400
