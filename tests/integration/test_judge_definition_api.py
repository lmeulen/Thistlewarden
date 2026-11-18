from __future__ import annotations

import base64
import os
import re
from pathlib import Path

import pytest

from evaluator import create_app
from evaluator.judging.definitions import JudgeDefinitionInput, semantic_hash

pytestmark = pytest.mark.integration


@pytest.fixture
def app(tmp_path: Path):
    return create_app(
        env_file=None,
        settings_environment={
            "SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
            "DATABASE_PATH": str(tmp_path / "judge-definitions.db"),
            "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "LOG_LEVEL": "WARNING",
        },
        initialize_database=True,
        test_config={"TESTING": True},
    )


def _payload(name: str = "My quality judge") -> dict[str, object]:
    return {
        "name": name,
        "description": "Review answers.",
        "mode": "single_prompt",
        "threshold": "0.75",
        "general_instructions": "Assess each metric fairly.",
        "metrics": [
            {"metric_id": "correctness", "enabled": True, "weight": "3"},
            {"metric_id": "relevance", "enabled": True, "weight": "2"},
        ],
    }


def test_judge_rule_form_switches_instruction_fields_by_assessment_method(app) -> None:
    page = app.test_client().get("/judge-rules/new")
    assert page.status_code == 200
    content = page.get_data(as_text=True)
    assert content.count('type="radio" name="mode"') == 2
    assert 'role="radiogroup"' in content
    assert "One assessment for all metrics" in content
    assert "Separate assessment per metric" in content
    assert 'id="single-prompt-editor"' in content
    assert 'id="fixed-output-requirements"' in content
    assert 'name="general_instructions"' in content
    assert "Assess the candidate answer against every selected metric" in content
    assert "Give a concise, concrete reason for every metric score." in content
    assert 'class="metric-instructions"' in content
    assert "selectedMode() === 'per_metric'" in content
    assert "singlePrompt.hidden = perMetric" in content
    assert "metricInstructions.forEach" in content
    assert "Appended automatically" in content
    assert "Return one JSON object only" in content
    assert "One sequential judge call per metric" in content


def test_new_judge_uses_recommended_ten_metrics_for_both_modes(app) -> None:
    content = app.test_client().get("/judge-rules/new").get_data(as_text=True)
    metric_inputs = re.findall(
        r'<input type="checkbox" name="metrics" value="([a-z_]+)"([^>]*)>', content
    )
    selected = {metric_id for metric_id, attributes in metric_inputs if "checked" in attributes}
    assert {metric_id for metric_id, _attributes in metric_inputs} == selected
    assert selected == {
        "correctness",
        "relevance",
        "instruction_compliance",
        "completeness",
        "clarity",
        "conciseness",
        "internal_consistency",
        "groundedness",
        "harm_prevention",
        "privacy_protection",
    }
    for metric_id, weight in {
        "correctness": "3",
        "relevance": "2",
        "instruction_compliance": "2",
        "completeness": "2",
        "clarity": "1",
        "conciseness": "1",
        "internal_consistency": "2",
        "groundedness": "2",
        "harm_prevention": "2",
        "privacy_protection": "2",
    }.items():
        expected = f'name="weight_{metric_id}" type="number" min="0" step="0.1" value="{weight}"'
        assert expected in content
    assert "Claims and conclusions are correct." in content
    assert "Answer protects sensitive personal data." in content
    assert "selectedMode() === 'per_metric'" in content


def test_recommended_ten_metrics_can_be_saved_in_per_metric_mode(app) -> None:
    client = app.test_client()
    page = client.get("/judge-rules/new").get_data(as_text=True)
    token = page.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    selected = {
        "correctness": "3",
        "relevance": "2",
        "instruction_compliance": "2",
        "completeness": "2",
        "clarity": "1",
        "conciseness": "1",
        "internal_consistency": "2",
        "groundedness": "2",
        "harm_prevention": "2",
        "privacy_protection": "2",
    }
    data: dict[str, str | list[str]] = {
        "csrf_token": token,
        "name": "Recommended separate judge",
        "mode": "per_metric",
        "threshold": "0.75",
        "metrics": list(selected),
    }
    for metric_id, weight in selected.items():
        data[f"weight_{metric_id}"] = weight
        data[f"instructions_{metric_id}"] = f"Assess {metric_id}."
    response = client.post("/judge-rules/new", data=data, follow_redirects=True)
    assert response.status_code == 200
    assert "Judge draft created." in response.get_data(as_text=True)
    judges = app.extensions["judge_definition_service"].list_all()
    saved = next(judge for judge in judges if judge.name == "Recommended separate judge")
    assert saved.draft is not None
    assert saved.draft.mode == "per_metric"
    assert {metric.metric_id for metric in saved.draft.metrics if metric.enabled} == set(selected)
    saved_weights = {
        metric.metric_id: str(metric.weight) for metric in saved.draft.metrics if metric.enabled
    }
    assert saved_weights == selected


def test_judge_definition_draft_publish_and_immutable_version(app) -> None:
    client = app.test_client()
    empty = client.get("/api/v1/judges")
    assert empty.status_code == 200
    assert empty.get_json()["judges"] == []
    assert client.get("/api/v1/judges/general-quality").status_code == 404
    assert client.get("/api/v1/judges/general-quality/versions/1.0").status_code == 404
    rules_page = client.get("/judges").get_data(as_text=True)
    assert "No judges yet" in rules_page
    assert "Judge profiles" in rules_page
    assert "Built-in" not in rules_page

    created = client.post("/api/v1/judges", json=_payload())
    assert created.status_code == 201
    judge = created.get_json()["judge"]
    assert judge["lifecycle"] == "draft"
    assert judge["draft"]["threshold"] == "0.75"

    published = client.post(f"/api/v1/judges/{judge['id']}/publish")
    assert published.status_code == 201
    value = published.get_json()["judge_version"]
    assert value["version"] == "1.0"
    assert len(value["semantic_hash"]) == 64

    version = client.get(f"/api/v1/judges/{judge['id']}/versions/1.0")
    assert version.get_json()["judge_version"] == value

    second_publish = client.post(f"/api/v1/judges/{judge['id']}/publish")
    assert second_publish.status_code == 409


def test_configuration_round_trip_preserves_published_judge_definition(app, tmp_path: Path) -> None:
    client = app.test_client()
    created = client.post("/api/v1/judges", json=_payload("Portable judge")).get_json()["judge"]
    assert client.post(f"/api/v1/judges/{created['id']}/publish").status_code == 201

    bundle = app.extensions["configuration_exchange_service"].export().model_dump(mode="json")
    exported = bundle["judge_definitions"]
    assert len(exported) == 1
    assert exported[0]["versions"][0]["version"] == "1.0"

    restored = create_app(
        env_file=None,
        settings_environment={
            "SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
            "DATABASE_PATH": str(tmp_path / "restored-judge-definitions.db"),
            "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "LOG_LEVEL": "WARNING",
        },
        initialize_database=True,
        test_config={"TESTING": True},
    )
    exchange = restored.extensions["configuration_exchange_service"]
    assert exchange.import_bundle(bundle, "merge", preview=True).creates == 1
    exchange.import_bundle(bundle, "merge", preview=False)

    restored_version = restored.extensions["judge_definition_service"].get_version(
        created["id"], "1.0"
    )
    assert restored_version.semantic_hash == exported[0]["versions"][0]["semantic_hash"]
    assert restored_version.data.model_dump(mode="json") == exported[0]["versions"][0]["data"]


def test_gui_publishes_next_immutable_version_and_shows_history(app) -> None:
    client = app.test_client()
    judge = client.post("/api/v1/judges", json=_payload("Versioned judge")).get_json()["judge"]
    assert client.post(f"/api/v1/judges/{judge['id']}/publish").status_code == 201
    draft = _payload("Versioned judge")
    draft["threshold"] = "0.8"
    assert client.patch(f"/api/v1/judges/{judge['id']}/draft", json=draft).status_code == 200

    page = client.get(f"/judge-rules/{judge['id']}")
    text = page.get_data(as_text=True)
    assert "Published versions:" in text
    assert "Publish version 1.1" in text
    token = text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    published = client.post(
        f"/judge-rules/{judge['id']}/publish",
        data={"csrf_token": token, "version": "1.1"},
        follow_redirects=True,
    )
    assert "Judge version 1.1 published" in published.get_data(as_text=True)
    first = client.get(f"/api/v1/judges/{judge['id']}/versions/1.0").get_json()["judge_version"]
    second = client.get(f"/api/v1/judges/{judge['id']}/versions/1.1").get_json()["judge_version"]
    assert first["data"]["threshold"] == "0.75"
    assert second["data"]["threshold"] == "0.8"


def test_combined_page_renders_imported_published_judge_without_draft(app) -> None:
    service = app.extensions["configuration_exchange_service"]
    draft = _payload("Imported published judge")
    data = JudgeDefinitionInput.model_validate(draft)
    bundle = {
        "schema_version": "1.0",
        "endpoints": [],
        "model_profiles": [],
        "judge_profiles": [],
        "judge_definitions": [
            {
                "id": "imported-published",
                "draft": None,
                "versions": [
                    {
                        "definition_id": "imported-published",
                        "version": "1.0",
                        "origin": "local",
                        "lifecycle": "published",
                        "data": data.model_dump(mode="json"),
                        "semantic_hash": semantic_hash(data),
                    }
                ],
            }
        ],
        "golden_sets": [],
        "golden_set_items": [],
    }
    service.import_bundle(bundle, "merge", preview=False)

    page = app.test_client().get("/judges")
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert "Imported published judge" in text
    assert "1 published version" in text
    editor = app.test_client().get("/judge-rules/imported-published")
    assert editor.status_code == 200
    assert "Publish version 1.1" in editor.get_data(as_text=True)


def test_testcase_editor_offers_published_judge_versions(app) -> None:
    client = app.test_client()
    created = client.post("/api/v1/judges", json=_payload("Published judge"))
    judge = created.get_json()["judge"]
    assert client.post(f"/api/v1/judges/{judge['id']}/publish").status_code == 201
    golden_set = app.extensions["golden_set_service"].create_set(
        {"name": "Judge set", "version": "1.0"}
    )

    page = client.get(f"/golden-sets/{golden_set.id}/items/new").get_data(as_text=True)
    assert f'option value="judge:{judge["id"]}@1.0"' in page
    assert "Published judge · v1.0 · one assessment for all metrics · threshold 0.75" in page
    assert "judge_definition_id" in page
    assert "judge_definition_version" in page
    assert "Legacy built-in presets" not in page
    assert "Legacy judge used by this test case" not in page


def test_gui_creates_testcase_with_published_judge_version(app) -> None:
    client = app.test_client()
    created = client.post("/api/v1/judges", json=_payload("GUI judge"))
    judge = created.get_json()["judge"]
    assert client.post(f"/api/v1/judges/{judge['id']}/publish").status_code == 201
    golden_set = app.extensions["golden_set_service"].create_set({"name": "GUI judge set"})
    page = client.get(f"/golden-sets/{golden_set.id}/items/new")
    token = page.get_data(as_text=True).split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = client.post(
        f"/golden-sets/{golden_set.id}/items/new",
        data={
            "csrf_token": token,
            "case_id": "versioned",
            "prompt": "Question",
            "evaluation_method": "llm_judge",
            "evaluator_config": (
                '{"judge_definition_id":"' + judge["id"] + '","judge_definition_version":"1.0"}'
            ),
            "weight": "1",
            "enabled": "on",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Test case created." in response.get_data(as_text=True)
    item = app.extensions["golden_set_service"].list_items(
        golden_set.id, page=1, per_page=10, category=None, tag=None, enabled=None
    )[0][0]
    assert item.data.evaluator_config == {
        "judge_definition_id": judge["id"],
        "judge_definition_version": "1.0",
    }


def test_gui_testcase_create_keeps_unexpected_failure_out_of_http_500(app, monkeypatch) -> None:
    client = app.test_client()
    golden_set = app.extensions["golden_set_service"].create_set({"name": "Safe GUI failure"})
    page = client.get(f"/golden-sets/{golden_set.id}/items/new")
    token = page.get_data(as_text=True).split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    def fail(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("unexpected persistence failure")

    monkeypatch.setattr(app.extensions["golden_set_service"], "create_item", fail)
    response = client.post(
        f"/golden-sets/{golden_set.id}/items/new",
        data={
            "csrf_token": token,
            "case_id": "safe-error",
            "prompt": "Question",
            "evaluation_method": "exact_match",
            "evaluator_config": '{"expected_value":"Answer"}',
            "weight": "1",
        },
    )
    assert response.status_code == 200
    assert "Test case could not be created. Check its fields and try again." in response.get_data(
        as_text=True
    )


def test_judge_definition_rejects_unknown_and_removed_metrics(app) -> None:
    client = app.test_client()
    payload = _payload()
    payload["metrics"] = [{"metric_id": "invented", "enabled": True, "weight": "1"}]
    response = client.post("/api/v1/judges", json=payload)
    assert response.status_code == 400
    assert "unknown metrics" in response.get_json()["error"]["details"][0]["message"]

    payload["metrics"] = [{"metric_id": "coherence", "enabled": True, "weight": "1"}]
    removed = client.post("/api/v1/judges", json=payload)
    assert removed.status_code == 400
    assert "Unsupported metrics: coherence" in removed.get_json()["error"]["message"]
