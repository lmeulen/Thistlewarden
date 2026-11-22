from __future__ import annotations

import base64
import html
import io
import json
import os
import re
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from evaluator import create_app
from evaluator.api.errors import ApiError, ValidationApiError

pytestmark = pytest.mark.integration


@pytest.fixture
def app(tmp_path: Path):
    return create_app(
        env_file=None,
        settings_environment={
            "SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
            "DATABASE_PATH": str(tmp_path / "gui-step27.db"),
            "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "LOG_LEVEL": "WARNING",
        },
        initialize_database=True,
        test_config={"TESTING": True},
    )


def _csrf(client, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
    assert match
    return match.group(1)


def _create_set(client) -> str:
    token = _csrf(client, "/golden-sets/new")
    response = client.post(
        "/golden-sets/new",
        data={
            "csrf_token": token,
            "name": "GUI set",
            "version": "1.0",
            "tags": "gui, test",
            "active": "on",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    return response.headers["Location"].rsplit("/", 1)[-1]


def test_item_form_creates_and_edits_ordered_user_prompts(app) -> None:
    client = app.test_client()
    set_id = _create_set(client)
    path = f"/golden-sets/{set_id}/items/new"
    page = client.get(path).get_data(as_text=True)
    assert "User prompts" in page
    assert 'name="user_prompt"' in page
    assert 'id="add-user-prompt"' in page
    assert 'class="secondary compact move-user-prompt-up" disabled' in page
    assert 'class="secondary compact move-user-prompt-down" disabled' in page
    assert 'class="secondary compact remove-user-prompt" disabled' in page
    assert "final response evaluated" in page
    assert "Move up" in page and "Move down" in page
    token = _csrf(client, path)
    created = client.post(
        path,
        data={
            "csrf_token": token,
            "case_id": "multi-turn",
            "user_prompt": ["First question", "Follow-up question"],
            "evaluation_method": "exact_match",
            "evaluator_config": '{"expected_value":"final"}',
            "weight": "1",
            "enabled": "on",
        },
        follow_redirects=False,
    )
    assert created.status_code == 302
    item_path = created.headers["Location"]
    item = app.extensions["golden_set_service"].get_item(UUID(item_path.rsplit("/", 1)[-1]))
    assert item.data.user_prompts == ["First question", "Follow-up question"]
    edit_page = client.get(item_path).get_data(as_text=True)
    assert "First question" in edit_page and "Follow-up question" in edit_page
    assert "final response evaluated" in edit_page
    assert "move-user-prompt-up" in edit_page and "move-user-prompt-down" in edit_page


def test_golden_set_testcases_can_be_filtered_by_test_type(app) -> None:
    client = app.test_client()
    set_id = _create_set(client)
    service = app.extensions["golden_set_service"]
    service.create_item(
        UUID(set_id),
        {
            "case_id": "exact-case",
            "prompt": "Exact prompt",
            "evaluation_method": "exact_match",
            "evaluator_config": {"expected_value": "answer"},
        },
    )
    service.create_item(
        UUID(set_id),
        {
            "case_id": "regex-case",
            "prompt": "Regex prompt",
            "evaluation_method": "regex",
            "evaluator_config": {"pattern": "answer"},
        },
    )

    filtered = client.get(f"/golden-sets/{set_id}?evaluation_method=regex").get_data(as_text=True)
    assert 'class="testcase-heading-actions"' in filtered
    assert 'role="group" aria-label="Export test cases"' in filtered
    assert "<span>Export</span>" in filtered
    assert re.search(r'class="button secondary"[^>]*>JSON</a>', filtered)
    assert re.search(r'class="button secondary"[^>]*>CSV</a>', filtered)
    assert '<label>Test type<select name="evaluation_method">' in filtered
    assert '<option value="">All types</option>' in filtered
    assert '<option value="regex" selected>Regular expression</option>' in filtered
    assert "regex-case" in filtered
    assert "exact-case" not in filtered
    assert "Showing 1–1 of 1 test cases." in filtered

    all_types = client.get(f"/golden-sets/{set_id}").get_data(as_text=True)
    assert "regex-case" in all_types
    assert "exact-case" in all_types
    assert "Showing 1–2 of 2 test cases." in all_types

    invalid = client.get(f"/golden-sets/{set_id}?evaluation_method=unknown").get_data(as_text=True)
    assert "regex-case" in invalid
    assert "exact-case" in invalid
    assert 'value="unknown"' not in invalid


def test_judge_page_can_delete_profile_with_confirmation(app) -> None:
    client = app.test_client()
    endpoint = client.post(
        "/api/v1/endpoints",
        json={"name": "Judge provider", "completions_url": "https://example.test/chat"},
    ).get_json()["endpoint"]
    model = client.post(
        f"/api/v1/endpoints/{endpoint['id']}/models",
        json={"model_id": "judge", "display_name": "Judge model"},
    ).get_json()["model"]
    judge = client.post(
        "/api/v1/judge-profiles",
        json={"name": "Disposable judge", "model_profile_id": model["id"]},
    ).get_json()["judge_profile"]

    page = client.get("/judges").get_data(as_text=True)
    assert "delete-judge-form" in page
    assert f"/judges/{judge['id']}/delete" in page

    token = _csrf(client, "/judges")
    denied = client.post(
        f"/judges/{judge['id']}/delete",
        data={"csrf_token": token},
        follow_redirects=True,
    )
    assert "Explicitly confirm permanent deletion" in denied.get_data(as_text=True)
    assert client.get(f"/api/v1/judge-profiles/{judge['id']}").status_code == 200

    token = _csrf(client, "/judges")
    deleted = client.post(
        f"/judges/{judge['id']}/delete",
        data={"csrf_token": token, "confirm": "on"},
        follow_redirects=True,
    )
    assert "Judge profile deleted" in deleted.get_data(as_text=True)
    assert client.get(f"/api/v1/judge-profiles/{judge['id']}").status_code == 404


def test_step27_empty_pages_render_and_navigation(app) -> None:
    client = app.test_client()
    for path, text in (
        ("/golden-sets", "Golden sets"),
        ("/judges", "Judge profiles"),
        ("/backup", "Backup and restore"),
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert text in response.get_data(as_text=True)
    legacy_judges = client.get("/judge-rules")
    assert legacy_judges.status_code == 302
    assert legacy_judges.headers["Location"].endswith("/judges")
    combined = client.get("/judges").get_data(as_text=True)
    assert "Evaluation rules" in combined
    assert "Judge profiles" in combined
    configuration = client.get("/configuration")
    assert configuration.status_code == 308
    assert configuration.headers["Location"].endswith("/backup")
    assert client.get("/configuratie").status_code == 404
    page = client.get("/").get_data(as_text=True)
    navigation = page[page.index('<nav aria-label="Main navigation">') : page.index("</nav>")]
    labels = (
        "Overview",
        "Runs",
        "Golden sets",
        "Endpoints and models",
        "Judges",
        "Backup",
    )
    assert all(label in navigation for label in labels)
    assert [navigation.index(f">{label}</a>") for label in labels] == sorted(
        navigation.index(f">{label}</a>") for label in labels
    )
    assert 'href="/backup">Backup</a>' in navigation
    assert ">Configuration</a>" not in navigation


def test_item_form_shows_examples_and_preserves_saved_config(app) -> None:
    client = app.test_client()
    set_id = _create_set(client)
    response = client.get(f"/golden-sets/{set_id}/items/new")
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'id="evaluation-method"' in page
    assert 'id="evaluator-config"' in page
    assert 'name="expected_answer"' not in page
    assert 'id="exact-match-mode"' in page
    assert 'id="show-exact-form"' in page
    assert 'id="show-exact-json"' in page
    assert 'id="exact-expected-value"' in page
    assert all(
        f'id="exact-{field}"' in page
        for field in ("trim", "case-sensitive", "normalize-newlines", "collapse-whitespace")
    )
    assert "method.addEventListener('change'" in page
    assert "JSON.stringify(examples[method.value], null, 2)" in page
    assert "value = {expected_value: expectedValue.value}" in page
    assert "jsonEditor.style.display = useForm ? 'none' : '';" in page
    assert "Fix invalid JSON before switching to form fields." in page
    assert 'id="contains-mode"' in page
    assert 'id="show-contains-form"' in page
    assert 'id="show-contains-json"' in page
    assert 'id="contains-fragments"' in page
    assert 'id="add-contains-fragment"' in page
    assert 'id="contains-match-mode"' in page
    assert all(
        f'id="contains-{field}"' in page
        for field in ("case-sensitive", "normalize-newlines", "collapse-whitespace")
    )
    assert 'id="regex-mode"' in page
    assert 'id="show-regex-form"' in page
    assert 'id="show-regex-json"' in page
    assert 'id="regex-pattern"' in page
    assert all(
        f'id="regex-{field}"' in page
        for field in ("full-match", "case-sensitive", "multiline", "dot-all")
    )
    assert 'id="numeric-mode"' in page
    assert 'class="field-with-info numeric-checkbox"' in page
    assert 'id="show-numeric-form"' in page
    assert 'id="show-numeric-json"' in page
    assert all(
        f'id="numeric-{field}"' in page
        for field in (
            "expected-value",
            "absolute-tolerance",
            "relative-tolerance",
            "extraction",
            "decimal-separator",
            "allow-thousands",
        )
    )
    assert 'id="json-schema-mode"' in page
    assert 'id="show-json-schema-form"' in page
    assert 'id="show-json-schema-json"' in page
    assert 'id="json-schema-schema"' in page
    assert 'id="json-schema-draft"' in page
    assert 'id="json-schema-code-fence"' in page
    assert 'id="json-schema-case-sensitive"' in page
    assert 'id="json-schema-use-expected"' in page
    assert 'id="json-schema-expected"' in page
    assert "configToJsonSchemaFields" in page
    assert "jsonSchemaFieldsToConfig" in page
    assert "if (method.value === 'json_schema') setJsonSchemaEditor(true);" in page
    assert 'id="llm-judge-mode"' in page
    assert 'id="show-llm-form"' in page
    assert 'id="show-llm-json"' in page
    assert 'id="llm-expected-value"' in page
    assert 'id="llm-context"' in page
    assert 'id="llm-preset" disabled' in page
    assert "No published judges available" in page
    assert "General response quality" not in page
    assert "Grounded answer / RAG faithfulness" not in page
    assert "Create and publish a judge" in page
    assert '"llm_judge": {}' in page
    assert "if (llmPreset.disabled && !value.judge_definition_id && !value.preset_id)" in page
    assert 'class="help llm-preset-description"' in page
    assert 'class="content llm-preset-details"' in page
    assert 'class="help llm-preset-help"' in page
    assert 'href="/judge-rules/new"' in page
    assert 'class="llm-rubric-weight"' not in page
    assert 'id="llm-threshold"' not in page
    assert page.count('class="info-button"') == 28
    assert page.count('role="tooltip" hidden') == 28
    assert "configToLlmFields" in page
    assert "llmFieldsToConfig" in page
    assert "configToNumericFields" in page
    assert "numericFieldsToConfig" in page
    assert "configToRegexFields" in page
    assert "regexFieldsToConfig" in page
    assert "configToContainsFields" in page
    assert "containsFieldsToConfig" in page
    assert "addFragmentField" in page
    assert "document.createElement('input')" in page
    assert "field.type = 'text'" in page
    assert "document.createElement('textarea')" not in page
    assert "closeInfoPopups" in page
    assert "event.key === 'Escape'" in page
    assert re.search(r'<script nonce="[^"]+">', page)
    assert all(
        f"&quot;{method}&quot;" in page or f'"{method}"' in page
        for method in (
            "exact_match",
            "contains",
            "regex",
            "numeric",
            "json_schema",
            "llm_judge",
        )
    )
    config_match = re.search(
        r'<textarea name="evaluator_config"[^>]*>(.*?)</textarea>', page, re.DOTALL
    )
    assert config_match
    default_config = json.loads(html.unescape(config_match.group(1)))
    assert default_config["trim"] is True
    assert default_config["normalize_newlines"] is True

    service = app.extensions["golden_set_service"]
    saved_config = {
        "expected_fragments": ["saved custom fragment"],
        "match_mode": "any",
        "case_sensitive": False,
    }
    item = service.create_item(
        UUID(set_id),
        {
            "case_id": "saved-config",
            "prompt": "Answer with custom fragment.",
            "evaluation_method": "contains",
            "evaluator_config": saved_config,
        },
    )
    edit_page = client.get(f"/golden-items/{item.id}").get_data(as_text=True)
    edit_match = re.search(
        r'<textarea name="evaluator_config"[^>]*>(.*?)</textarea>', edit_page, re.DOTALL
    )
    assert edit_match
    rendered_config = json.loads(html.unescape(edit_match.group(1)))
    assert rendered_config["expected_fragments"] == ["saved custom fragment"]
    assert rendered_config["match_mode"] == "any"
    assert rendered_config["case_sensitive"] is False


def test_output_tester_uses_unsaved_fields_without_persistence(app) -> None:
    client = app.test_client()
    set_id = _create_set(client)
    path = f"/golden-sets/{set_id}/items/new"
    token = _csrf(client, path)
    page = client.get(path).get_data(as_text=True)
    assert 'id="test-output"' in page
    assert 'id="verify-output"' in page
    assert 'id="verification-result" aria-live="polite"' in page
    assert f'data-verify-url="/golden-sets/{set_id}/items/verify"' in page

    base = {
        "csrf_token": token,
        "case_id": "",
        "prompt": "",
        "expected_answer": "Expected unsaved value",
        "evaluation_method": "regex",
        "evaluator_config": json.dumps({"pattern": r"^Expected unsaved value$"}),
        "weight": "1",
        "enabled": "on",
    }
    passed = client.post(
        f"/golden-sets/{set_id}/items/verify",
        data={**base, "test_output": "Expected unsaved value"},
    )
    assert passed.status_code == 200
    assert passed.get_json() == {
        "ok": True,
        "method": "regex",
        "passed": True,
        "score": "1",
        "reason": "Regular expression matched.",
    }

    marker = "candidate-must-not-be-returned"
    failed = client.post(
        f"/golden-sets/{set_id}/items/verify",
        data={**base, "test_output": marker},
    )
    assert failed.status_code == 200
    assert failed.get_json()["passed"] is False
    assert marker not in failed.get_data(as_text=True)
    service = app.extensions["golden_set_service"]
    assert (
        service.list_items(
            UUID(set_id), page=1, per_page=100, category=None, tag=None, enabled=None
        )[1]
        == 0
    )


def test_output_tester_rejects_invalid_config_and_llm_judge(app) -> None:
    client = app.test_client()
    set_id = _create_set(client)
    path = f"/golden-sets/{set_id}/items/verify"
    token = _csrf(client, f"/golden-sets/{set_id}/items/new")
    base = {
        "csrf_token": token,
        "case_id": "preview",
        "prompt": "Prompt",
        "expected_answer": "Expected",
        "weight": "1",
        "test_output": "candidate",
    }
    invalid = client.post(
        path,
        data={
            **base,
            "evaluation_method": "regex",
            "evaluator_config": "not-json",
        },
    )
    assert invalid.status_code == 400
    assert invalid.get_json()["message"] == "Evaluator configuration is invalid JSON."

    judge = client.post(
        path,
        data={
            **base,
            "evaluation_method": "llm_judge",
            "evaluator_config": json.dumps(
                {"preset_id": "general-quality", "preset_version": "1.0"}
            ),
        },
    )
    assert judge.status_code == 400
    assert "requires a run" in judge.get_json()["message"]


@pytest.mark.parametrize(
    ("method", "expected", "config"),
    [
        ("exact_match", None, {"expected_value": "ja"}),
        ("contains", None, {"expected_fragments": ["ja"], "match_mode": "all"}),
        ("regex", None, {"pattern": "^ja$"}),
        ("numeric", None, {"expected_value": "42", "absolute_tolerance": "0.1"}),
        ("json_schema", None, {"schema": {"type": "object"}}),
        (
            "llm_judge",
            None,
            {
                "preset_id": "reference-correctness",
                "preset_version": "1.0",
                "expected_value": "ja",
            },
        ),
    ],
)
def test_gui_saves_every_evaluator_method(
    app, method: str, expected: str | None, config: dict
) -> None:
    client = app.test_client()
    set_id = _create_set(client)
    token = _csrf(client, f"/golden-sets/{set_id}/items/new")
    response = client.post(
        f"/golden-sets/{set_id}/items/new",
        data={
            "csrf_token": token,
            "case_id": f"case-{method}",
            "prompt": "Antwoord.",
            "expected_answer": expected or "",
            "evaluation_method": method,
            "evaluator_config": json.dumps(config),
            "weight": "1.5",
            "enabled": "on",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Test case created" in response.get_data(as_text=True)


def test_existing_legacy_testcase_shows_only_its_historical_judge(app) -> None:
    client = app.test_client()
    set_id = _create_set(client)
    token = _csrf(client, f"/golden-sets/{set_id}/items/new")
    response = client.post(
        f"/golden-sets/{set_id}/items/new",
        data={
            "csrf_token": token,
            "case_id": "legacy-judge",
            "prompt": "Answer.",
            "evaluation_method": "llm_judge",
            "evaluator_config": json.dumps(
                {"preset_id": "general-quality", "preset_version": "1.0"}
            ),
            "weight": "1",
            "enabled": "on",
        },
        follow_redirects=True,
    )
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Legacy judge used by this test case" in page
    assert "General response quality · v1.0" in page
    assert "Grounded answer / RAG faithfulness" not in page


def test_golden_set_delete_requires_name_and_removes_items(app) -> None:
    client = app.test_client()
    set_id = _create_set(client)
    token = _csrf(client, f"/golden-sets/{set_id}")
    client.post(
        f"/golden-sets/{set_id}/items/new",
        data={
            "csrf_token": token,
            "case_id": "delete-me",
            "prompt": "Prompt",
            "evaluation_method": "exact_match",
            "expected_answer": "Answer",
            "evaluator_config": "{}",
            "weight": "1",
            "enabled": "on",
        },
    )

    page = client.get(f"/golden-sets/{set_id}").get_data(as_text=True)
    assert "Delete golden set" in page
    assert "Type <strong>GUI set</strong> to confirm" in page

    token = _csrf(client, f"/golden-sets/{set_id}")
    denied = client.post(
        f"/golden-sets/{set_id}/delete",
        data={"csrf_token": token, "confirmation_name": "wrong"},
        follow_redirects=True,
    )
    assert "Type the exact golden set name" in denied.get_data(as_text=True)
    assert client.get(f"/api/v1/golden-sets/{set_id}").status_code == 200

    token = _csrf(client, f"/golden-sets/{set_id}")
    deleted = client.post(
        f"/golden-sets/{set_id}/delete",
        data={"csrf_token": token, "confirmation_name": "GUI set"},
        follow_redirects=True,
    )
    assert "Golden set deleted" in deleted.get_data(as_text=True)
    assert client.get(f"/api/v1/golden-sets/{set_id}").status_code == 404


def test_duplicate_set_and_quick_delete_item(app) -> None:
    client = app.test_client()
    set_id = _create_set(client)
    service = app.extensions["golden_set_service"]
    original = service.create_item(
        UUID(set_id),
        {
            "case_id": "duplicate-me",
            "prompt": "Answer yes.",
            "expected_answer": "yes",
            "evaluation_method": "exact_match",
        },
    )
    app.extensions["quality_gate_service"].set_defaults(UUID(set_id), {"min_pass_rate": "0.9"})

    listing = client.get("/golden-sets").get_data(as_text=True)
    assert "Duplicate" in listing and "duplicate-set-form" in listing
    token = _csrf(client, "/golden-sets")
    response = client.post(
        f"/golden-sets/{set_id}/duplicate",
        data={"csrf_token": token, "name": "GUI set kopie"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    duplicate_id = response.headers["Location"].rsplit("/", 1)[-1]
    duplicate = service.get_set(UUID(duplicate_id))
    duplicate_items, total = service.list_items(
        duplicate.id, page=1, per_page=100, category=None, tag=None, enabled=None
    )
    assert duplicate.data.name == "GUI set kopie"
    assert total == 1 and duplicate_items[0].data.case_id == "duplicate-me"
    assert app.extensions["quality_gate_service"].defaults(duplicate.id).min_pass_rate == Decimal(
        "0.9"
    )

    second = service.create_item(
        UUID(set_id),
        {
            "case_id": "keep-me",
            "prompt": "Answer no.",
            "expected_answer": "no",
            "evaluation_method": "exact_match",
        },
    )
    page = client.get(f"/golden-sets/{set_id}").get_data(as_text=True)
    assert "inline-delete-form" in page and ">Delete</button>" in page
    assert page.count('name="item_ids"') == 2
    assert '<option value="delete">Delete selected test cases</option>' in page
    token = _csrf(client, f"/golden-sets/{set_id}")
    deleted = client.post(
        f"/golden-sets/{set_id}/items/delete",
        data={"csrf_token": token, "item_ids": [str(original.id), str(second.id)]},
        follow_redirects=True,
    )
    assert deleted.status_code == 200 and "2 test case(s) deleted" in deleted.get_data(as_text=True)
    assert (
        service.list_items(
            UUID(set_id), page=1, per_page=100, category=None, tag=None, enabled=None
        )[1]
        == 0
    )


def test_golden_set_and_case_pagination_reaches_101st_record(app) -> None:
    client = app.test_client()
    service = app.extensions["golden_set_service"]
    records = [
        service.create_set({"name": f"Paged set {index:03}", "version": "1.0"})
        for index in range(1, 102)
    ]

    first_page = client.get("/golden-sets").get_data(as_text=True)
    assert "Showing 1–100 of 101 golden sets." in first_page
    assert "Paged set 101" not in first_page
    second_page = client.get("/golden-sets?page=2").get_data(as_text=True)
    assert "Showing 101–101 of 101 golden sets." in second_page
    assert "Paged set 101" in second_page
    assert "Page 2 of 2" in second_page
    assert "Paged set 101" in client.get("/golden-sets?page=999").get_data(as_text=True)

    set_id = records[0].id
    cases = [
        service.create_item(
            set_id,
            {
                "case_id": f"case-{index:03}",
                "prompt": "Prompt",
                "evaluation_method": "exact_match",
                "evaluator_config": {"expected_value": "yes"},
                "category": "paged",
                "tags": ["last"],
                "enabled": index != 101,
            },
        )
        for index in range(1, 102)
    ]
    case_page = client.get(f"/golden-sets/{set_id}?page=2").get_data(as_text=True)
    assert "Showing 101–101 of 101 test cases." in case_page
    assert "case-101" in case_page
    assert f"/golden-items/{cases[-1].id}" in case_page

    filtered = client.get(f"/golden-sets/{set_id}?category=paged&tag=last").get_data(as_text=True)
    assert "category=paged" in filtered and "tag=last" in filtered
    assert 'value="paged"' in filtered and 'value="last"' in filtered
    disabled = client.get(f"/golden-sets/{set_id}?enabled=false").get_data(as_text=True)
    assert "case-101" in disabled
    assert '<option value="false" selected>' in disabled

    token = _csrf(client, f"/golden-sets/{set_id}?page=2")
    deleted = client.post(
        f"/golden-items/{cases[-1].id}/delete",
        data={"csrf_token": token},
        follow_redirects=True,
    )
    assert deleted.status_code == 200
    assert "Test case deleted" in deleted.get_data(as_text=True)
    assert (
        service.list_items(set_id, page=1, per_page=100, category=None, tag=None, enabled=None)[1]
        == 100
    )


def test_run_form_can_find_and_select_101st_golden_set(app) -> None:
    client = app.test_client()
    service = app.extensions["golden_set_service"]
    records = [
        service.create_set({"name": f"Run set {index:03}", "version": "1.0"})
        for index in range(1, 102)
    ]

    page = client.get("/runs/new?set_page=5").get_data(as_text=True)
    assert "Showing 101–101 of 101 matching active golden sets." in page
    assert f'value="{records[-1].id}"' in page
    assert "Run set 101 · 1.0" in page
    assert "Page 5 of 5" in page

    searched = client.get("/runs/new?set_query=Run+set+101").get_data(as_text=True)
    assert "Showing 1–1 of 1 matching active golden sets." in searched
    assert f'value="{records[-1].id}"' in searched
    assert 'name="set_query" value="Run set 101"' in searched
    assert "set_query=Run+set+101" in searched or "set_query=Run%20set%20101" in searched


def test_quality_gates_and_golden_json_import_preview(app) -> None:
    client = app.test_client()
    set_id = _create_set(client)
    token = _csrf(client, f"/golden-sets/{set_id}")
    response = client.post(
        f"/golden-sets/{set_id}/gates",
        data={"csrf_token": token, "min_pass_rate": "0.9", "max_total_cost": "2.5"},
        follow_redirects=True,
    )
    assert response.status_code == 200 and "Quality gates saved" in response.get_data(as_text=True)

    exported = client.get(f"/golden-sets/{set_id}/download/json")
    token = _csrf(client, "/golden-sets/import")
    preview = client.post(
        "/golden-sets/import",
        data={
            "csrf_token": token,
            "format": "json",
            "file": (io.BytesIO(exported.data), "set.json"),
        },
        content_type="multipart/form-data",
    )
    assert preview.status_code == 200
    assert "Valid preview" in preview.get_data(as_text=True)


def test_judge_form_and_secret_free_configuration_preview(app) -> None:
    client = app.test_client()
    endpoint = client.post(
        "/api/v1/endpoints",
        json={
            "name": "Provider",
            "completions_url": "https://example.test/v1/chat/completions",
            "api_key": "never-export-this",
        },
    ).get_json()["endpoint"]
    model = client.post(
        f"/api/v1/endpoints/{endpoint['id']}/models",
        json={"model_id": "m1", "display_name": "Model een"},
    ).get_json()["model"]
    token = _csrf(client, "/judges/new")
    judge = client.post(
        "/judges/new",
        data={
            "csrf_token": token,
            "name": "Hoofdjudge",
            "model_profile_id": model["id"],
            "reasoning_value": "high",
            "is_global_default": "on",
            "active": "on",
        },
        follow_redirects=True,
    )
    assert judge.status_code == 200
    judge_text = judge.get_data(as_text=True)
    assert "Judge profile created" in judge_text
    assert 'name="implementation_config" rows="5">{}</textarea>' in judge_text
    assert "has no implementation settings" in judge_text
    assert "Test cases select immutable evaluation presets with rubric and threshold." in judge_text
    connection = app.extensions["database_factory"].connect()
    try:
        stored_semantics = connection.execute(
            "SELECT prompt_template,template_version,threshold,rubric_json "
            "FROM judge_profiles WHERE name=?",
            ("Hoofdjudge",),
        ).fetchone()
    finally:
        connection.close()
    assert tuple(stored_semantics) == ("preset-v1", "1.0", "0", "[]")
    profile = bundle_profile = client.get("/api/v1/judge-profiles").get_json()["judge_profiles"][0]
    assert profile["temperature"] is None
    assert profile["reasoning_value"] == "high"
    form = client.get("/judges").get_data(as_text=True)
    assert "Judge profiles" in form
    assert "Evaluation rules" in form
    assert "Rubrics, thresholds, and prompt templates" not in form

    exported = client.get("/backup/download")
    assert exported.status_code == 200
    assert b"never-export-this" not in exported.data
    bundle = json.loads(exported.data)
    assert bundle["schema_version"] == "1.0"
    exported_judge = bundle["judge_profiles"][0]["data"]
    assert exported_judge == {
        key: bundle_profile[key]
        for key in (
            "name",
            "model_profile_id",
            "temperature",
            "reasoning_value",
            "implementation_id",
            "implementation_version",
            "implementation_config",
            "max_output_tokens",
            "is_global_default",
            "active",
        )
    }
    assert (
        not {
            "prompt_template",
            "template_version",
            "threshold",
            "rubric",
        }
        & exported_judge.keys()
    )
    rejected = client.post(
        "/api/v1/judge-profiles",
        json={
            "name": "Legacy semantics",
            "model_profile_id": model["id"],
            "threshold": "0.9",
        },
    )
    assert rejected.status_code == 400
    token = _csrf(client, "/backup")
    preview = client.post(
        "/backup/restore",
        data={
            "csrf_token": token,
            "mode": "merge",
            "file": (io.BytesIO(exported.data), "config.json"),
        },
        content_type="multipart/form-data",
    )
    text = preview.get_data(as_text=True)
    assert preview.status_code == 200 and "Restore preview" in text
    assert "Endpoints with missing secrets" in text
    assert 'name="preview_payload"' in text
    assert "Restore preview" in text

    token = _csrf(client, "/backup")
    privacy_browser_preview = client.post(
        "/backup/restore",
        data={
            "csrf_token": token,
            "mode": "merge",
            "file": (io.BytesIO(exported.data), "config.json"),
        },
        content_type="multipart/form-data",
        headers={"Origin": "null"},
    )
    assert privacy_browser_preview.status_code == 200
    assert "Restore preview" in privacy_browser_preview.get_data(as_text=True)


def test_configuration_bundle_can_reference_previously_imported_model(app) -> None:
    client = app.test_client()
    endpoint = client.post(
        "/api/v1/endpoints",
        json={"name": "Provider", "completions_url": "https://example.test/v1/chat/completions"},
    ).get_json()["endpoint"]
    model = client.post(
        f"/api/v1/endpoints/{endpoint['id']}/models",
        json={"model_id": "judge-model", "display_name": "Judge model"},
    ).get_json()["model"]
    service = app.extensions["configuration_exchange_service"]
    bundle = {
        "schema_version": "1.0",
        "endpoints": [],
        "model_profiles": [],
        "judge_profiles": [
            {
                "id": "a3000000-0000-4000-8000-000000000001",
                "data": {
                    "name": "Imported default",
                    "model_profile_id": model["id"],
                    "is_global_default": True,
                },
            }
        ],
        "golden_sets": [],
        "golden_set_items": [],
    }
    assert service.import_bundle(bundle, "merge", preview=True).creates == 1
    service.import_bundle(bundle, "merge", preview=False)

    bundle["judge_profiles"][0]["id"] = "a3000000-0000-4000-8000-000000000002"
    bundle["judge_profiles"][0]["data"]["model_profile_id"] = "a2000000-0000-4000-8000-000000000099"
    with pytest.raises(ValidationApiError, match="Configuration file is invalid"):
        service.import_bundle(bundle, "merge", preview=True)


def test_configuration_preview_can_be_committed_without_reselecting_file(app) -> None:
    client = app.test_client()
    bundle = {
        "schema_version": "1.0",
        "endpoints": [],
        "model_profiles": [],
        "judge_profiles": [],
        "golden_sets": [],
        "golden_set_items": [],
    }
    token = _csrf(client, "/backup")
    preview = client.post(
        "/backup/restore",
        data={
            "csrf_token": token,
            "mode": "merge",
            "file": (io.BytesIO(json.dumps(bundle).encode()), "config.json"),
        },
        content_type="multipart/form-data",
    )
    text = preview.get_data(as_text=True)
    assert preview.status_code == 200
    assert 'name="preview_payload"' in text

    token = _csrf(client, "/backup")
    committed = client.post(
        "/backup/restore",
        data={
            "csrf_token": token,
            "mode": "merge",
            "preview_payload": json.dumps(bundle),
            "commit": "",
        },
        follow_redirects=True,
    )
    assert committed.status_code == 200
    assert "Configuration restored atomically" in committed.get_data(as_text=True)


def test_bulk_maintenance_search_preview_apply_and_stale_conflict(app) -> None:
    client = app.test_client()
    set_id = UUID(_create_set(client))
    service = app.extensions["golden_set_service"]
    first = service.create_item(
        set_id,
        {
            "case_id": "needle-id",
            "prompt": "ordinary",
            "evaluation_method": "exact_match",
            "evaluator_config": {"expected_value": "a"},
        },
    )
    second = service.create_item(
        set_id,
        {
            "case_id": "other",
            "prompt": "contains Needle text",
            "evaluation_method": "exact_match",
            "evaluator_config": {"expected_value": "b"},
        },
    )
    service.create_item(
        set_id,
        {
            "case_id": "hidden",
            "prompt": "different",
            "evaluation_method": "exact_match",
            "evaluator_config": {"expected_value": "c"},
        },
    )

    page = client.get(f"/golden-sets/{set_id}?query=needle").get_data(as_text=True)
    assert ">needle-id</a>" in page and ">other</a>" in page and ">hidden</a>" not in page
    assert "Selection applies only to visible page" in page
    token = _csrf(client, f"/golden-sets/{set_id}?query=needle")
    preview_response = client.post(
        f"/golden-sets/{set_id}/items/bulk-preview",
        data={
            "csrf_token": token,
            "item_ids": [str(first.id), str(second.id)],
            "action": "set_weight",
            "bulk_weight": "2.5",
        },
    )
    assert preview_response.status_code == 200
    preview = preview_response.get_data(as_text=True)
    assert (
        "Affected test cases</dt><dd>2" in preview
        and "contains Needle text" not in preview
        and "ordinary" not in preview
    )
    version_match = re.search(r'name="expected_updated_at" value="([^"]+)"', preview)
    assert version_match
    service.update_item(first.id, {"weight": "3"})
    token = re.search(r'name="csrf_token" value="([^"]+)"', preview).group(1)
    stale = client.post(
        f"/golden-sets/{set_id}/items/bulk-apply",
        data={
            "csrf_token": token,
            "item_ids": [str(first.id), str(second.id)],
            "action": "set_weight",
            "bulk_weight": "2.5",
            "expected_updated_at": html.unescape(version_match.group(1)),
        },
        follow_redirects=True,
    ).get_data(as_text=True)
    assert "changed after preview" in stale
    assert service.get_item(first.id).data.weight == Decimal("3")
    assert service.get_item(second.id).data.weight == Decimal("1.0")


def test_bulk_set_judge_updates_only_selected_llm_judge_cases(app) -> None:
    client = app.test_client()
    set_id = UUID(_create_set(client))
    service = app.extensions["golden_set_service"]
    definitions = app.extensions["judge_definition_service"]
    definition = definitions.create(
        {
            "name": "Bulk judge",
            "mode": "single_prompt",
            "threshold": "0.7",
            "general_instructions": "Assess correctness.",
            "metrics": [{"metric_id": "correctness", "enabled": True, "weight": "1"}],
        }
    )
    definitions.publish(definition.id, "1.0")
    llm_case = service.create_item(
        set_id,
        {
            "case_id": "judge-me",
            "prompt": "p",
            "evaluation_method": "llm_judge",
            "evaluator_config": {
                "judge_definition_id": definition.id,
                "judge_definition_version": "1.0",
            },
        },
    )
    deterministic = service.create_item(
        set_id,
        {
            "case_id": "leave-me",
            "prompt": "p",
            "evaluation_method": "exact_match",
            "evaluator_config": {"expected_value": "x"},
        },
    )

    page = client.get(f"/golden-sets/{set_id}").get_data(as_text=True)
    assert '<option value="set_judge">Set judge</option>' in page
    assert f'value="{definition.id}|1.0"' in page
    token = _csrf(client, f"/golden-sets/{set_id}")
    preview_response = client.post(
        f"/golden-sets/{set_id}/items/bulk-preview",
        data={
            "csrf_token": token,
            "item_ids": [str(llm_case.id), str(deterministic.id)],
            "action": "set_judge",
            "judge_version_ref": f"{definition.id}|1.0",
        },
    )
    preview = preview_response.get_data(as_text=True)
    assert preview_response.status_code == 200
    assert "Affected test cases</dt><dd>1" in preview
    token = re.search(r'name="csrf_token" value="([^"]+)"', preview).group(1)
    versions = html.unescape(
        re.search(r'name="expected_updated_at" value="([^"]+)"', preview).group(1)
    )
    applied = client.post(
        f"/golden-sets/{set_id}/items/bulk-apply",
        data={
            "csrf_token": token,
            "item_ids": [str(llm_case.id), str(deterministic.id)],
            "action": "set_judge",
            "judge_definition_id": definition.id,
            "judge_definition_version": "1.0",
            "expected_updated_at": versions,
        },
        follow_redirects=True,
    )
    assert "exactly 1 test case(s)" in applied.get_data(as_text=True)
    assert service.get_item(llm_case.id).data.evaluator_config == {
        "judge_definition_id": definition.id,
        "judge_definition_version": "1.0",
    }
    unchanged = service.get_item(deterministic.id).data.evaluator_config
    assert unchanged["expected_value"] == "x"
    assert "judge_definition_id" not in unchanged
    assert "judge_definition_version" not in unchanged


def test_bulk_delete_is_selectable_reviewed_and_applied(app) -> None:
    client = app.test_client()
    set_id = UUID(_create_set(client))
    service = app.extensions["golden_set_service"]
    selected = [
        service.create_item(
            set_id,
            {
                "case_id": case_id,
                "prompt": case_id,
                "evaluation_method": "exact_match",
                "evaluator_config": {"expected_value": case_id},
            },
        )
        for case_id in ("delete-one", "delete-two")
    ]
    kept = service.create_item(
        set_id,
        {
            "case_id": "keep",
            "prompt": "keep",
            "evaluation_method": "exact_match",
            "evaluator_config": {"expected_value": "keep"},
        },
    )

    page = client.get(f"/golden-sets/{set_id}").get_data(as_text=True)
    assert '<option value="delete">Delete selected test cases</option>' in page
    assert "bulk-delete-selection" not in page
    token = _csrf(client, f"/golden-sets/{set_id}")
    preview_response = client.post(
        f"/golden-sets/{set_id}/items/bulk-preview",
        data={
            "csrf_token": token,
            "item_ids": [str(item.id) for item in selected],
            "action": "delete",
        },
    )
    preview = preview_response.get_data(as_text=True)
    assert preview_response.status_code == 200
    assert "Deletion is permanent" in preview
    assert "Permanently delete 2 test case(s)" in preview
    token = re.search(r'name="csrf_token" value="([^"]+)"', preview).group(1)
    versions = html.unescape(
        re.search(r'name="expected_updated_at" value="([^"]+)"', preview).group(1)
    )

    applied = client.post(
        f"/golden-sets/{set_id}/items/bulk-apply",
        data={
            "csrf_token": token,
            "item_ids": [str(item.id) for item in selected],
            "action": "delete",
            "expected_updated_at": versions,
        },
        follow_redirects=True,
    )
    assert "exactly 2 test case(s)" in applied.get_data(as_text=True)
    assert [item.id for item in service._repository.list_all_items(set_id)] == [kept.id]
    assert service.get_item(kept.id).data.sort_order == 0


def test_bulk_move_duplicate_conflict_and_deterministic_order(app) -> None:
    client = app.test_client()
    source = UUID(_create_set(client))
    token = _csrf(client, "/golden-sets/new")
    response = client.post(
        "/golden-sets/new",
        data={"csrf_token": token, "name": "Destination", "version": "1", "active": "on"},
    )
    destination = UUID(response.headers["Location"].rsplit("/", 1)[-1])
    service = app.extensions["golden_set_service"]
    one = service.create_item(
        source,
        {
            "case_id": "one",
            "prompt": "one",
            "evaluation_method": "exact_match",
            "evaluator_config": {"expected_value": "1"},
        },
    )
    two = service.create_item(
        source,
        {
            "case_id": "two",
            "user_prompts": ["two", "follow-up"],
            "evaluation_method": "exact_match",
            "evaluator_config": {"expected_value": "2"},
        },
    )
    service.create_item(
        destination,
        {
            "case_id": "two",
            "prompt": "conflict",
            "evaluation_method": "exact_match",
            "evaluator_config": {"expected_value": "x"},
        },
    )
    with pytest.raises(ApiError, match="Destination already contains"):
        service.preview_bulk_items(
            source,
            {"item_ids": [one.id, two.id], "action": "move", "destination_set_id": destination},
        )
    assert [item.data.case_id for item in service._repository.list_all_items(source)] == [
        "one",
        "two",
    ]
    service.delete_item(service._repository.list_all_items(destination)[0].id)
    preview = service.preview_bulk_items(
        source, {"item_ids": [two.id], "action": "move", "destination_set_id": destination}
    )
    assert service.apply_bulk_items(source, preview.request.model_dump()) == 1
    assert service.get_item(two.id).golden_set_id == destination
    duplicate = service.preview_bulk_items(
        destination, {"item_ids": [two.id], "action": "duplicate", "destination_set_id": source}
    )
    service.apply_bulk_items(destination, duplicate.request.model_dump())
    source_items = service._repository.list_all_items(source)
    assert [item.data.sort_order for item in source_items] == [0, 1]
    copied = next(item for item in source_items if item.data.case_id == "two")
    assert copied.id != two.id
    assert copied.data.user_prompts == ["two", "follow-up"]
    reorder = service.preview_bulk_items(
        source, {"item_ids": [copied.id], "action": "reorder", "start_order": 0}
    )
    service.apply_bulk_items(source, reorder.request.model_dump())
    assert [item.data.case_id for item in service._repository.list_all_items(source)] == [
        "two",
        "one",
    ]


def test_bulk_field_actions_share_single_item_validation(app) -> None:
    service = app.extensions["golden_set_service"]
    set_id = service.create_set({"name": "Bulk fields", "version": "1"}).id
    item = service.create_item(
        set_id,
        {
            "case_id": "fields",
            "prompt": "p",
            "evaluation_method": "exact_match",
            "evaluator_config": {"expected_value": "x"},
            "tags": ["old"],
        },
    )

    for action, extra in [
        ("disable", {}),
        ("enable", {}),
        ("set_category", {"category": "safety"}),
        ("add_tags", {"tags": ["new"]}),
        ("remove_tags", {"tags": ["old"]}),
        ("replace_tags", {"tags": ["final"]}),
        ("set_weight", {"weight": "2.5"}),
    ]:
        preview = service.preview_bulk_items(
            set_id, {"item_ids": [item.id], "action": action, **extra}
        )
        assert service.apply_bulk_items(set_id, preview.request.model_dump()) == 1
        item = service.get_item(item.id)

    assert item.data.enabled is True
    assert item.data.category == "safety"
    assert item.data.tags == ["final"]
    assert item.data.weight == Decimal("2.5")
    for payload in [
        {"item_ids": [], "action": "enable"},
        {"item_ids": [item.id], "action": "set_weight"},
        {"item_ids": [item.id], "action": "add_tags", "tags": []},
        {"item_ids": [item.id], "action": "set_weight", "weight": "0"},
        {"item_ids": [item.id], "action": "move", "destination_set_id": set_id},
    ]:
        with pytest.raises(ApiError):
            service.preview_bulk_items(set_id, payload)


def test_bulk_apply_route_reports_exact_count(app) -> None:
    client = app.test_client()
    set_id = UUID(_create_set(client))
    service = app.extensions["golden_set_service"]
    item = service.create_item(
        set_id,
        {
            "case_id": "route",
            "prompt": "p",
            "evaluation_method": "exact_match",
            "evaluator_config": {"expected_value": "x"},
        },
    )
    token = _csrf(client, f"/golden-sets/{set_id}")
    preview_response = client.post(
        f"/golden-sets/{set_id}/items/bulk-preview",
        data={"csrf_token": token, "item_ids": str(item.id), "action": "disable"},
    )
    text = preview_response.get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', text).group(1)
    versions = html.unescape(
        re.search(r'name="expected_updated_at" value="([^"]+)"', text).group(1)
    )
    applied = client.post(
        f"/golden-sets/{set_id}/items/bulk-apply",
        data={
            "csrf_token": token,
            "item_ids": str(item.id),
            "action": "disable",
            "expected_updated_at": versions,
        },
        follow_redirects=True,
    )
    assert "exactly 1 test case(s)" in applied.get_data(as_text=True)
    assert service.get_item(item.id).data.enabled is False


def test_bulk_apply_rejects_external_return_redirect(app) -> None:
    client = app.test_client()
    set_id = UUID(_create_set(client))
    service = app.extensions["golden_set_service"]
    item = service.create_item(
        set_id,
        {
            "case_id": "safe-redirect",
            "prompt": "p",
            "evaluation_method": "exact_match",
            "evaluator_config": {"expected_value": "x"},
        },
    )
    preview = service.preview_bulk_items(set_id, {"item_ids": [item.id], "action": "disable"})
    token = _csrf(client, f"/golden-sets/{set_id}")
    response = client.post(
        f"/golden-sets/{set_id}/items/bulk-apply",
        data={
            "csrf_token": token,
            "item_ids": str(item.id),
            "action": "disable",
            "expected_updated_at": json.dumps(
                {
                    str(item_id): value.isoformat()
                    for item_id, value in preview.request.expected_updated_at.items()
                }
            ),
            "return_query": "//evil.example/path?query=lost&enabled=true",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].startswith(f"/golden-sets/{set_id}?")
    assert "evil.example" not in response.headers["Location"]
    assert response.headers["Location"].endswith("enabled=true#testcases")
