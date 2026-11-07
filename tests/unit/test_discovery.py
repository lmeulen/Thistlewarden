from __future__ import annotations

import json

import pytest

from evaluator.api.errors import InvalidProviderResponseError
from evaluator.discovery import parse_models_response, suggest_models_url

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://api.test/v1/chat/completions", "https://api.test/v1/models"),
        ("https://api.test/v1/completions", "https://api.test/v1/models"),
        ("https://api.test/v1/chat/completions?version=1", "https://api.test/v1/models?version=1"),
        ("https://api.test/v1/responses", None),
        ("https://api.test/completions/", None),
    ],
)
def test_safe_models_url_suggestion(url: str, expected: str | None) -> None:
    assert suggest_models_url(url) == expected


def test_models_parser_deduplicates_stably_and_ignores_extra_fields() -> None:
    content = json.dumps(
        {"data": [{"id": "b", "owned_by": "x"}, {"id": "a"}, {"id": "b"}], "extra": True}
    ).encode()
    assert parse_models_response(content) == ["b", "a"]


@pytest.mark.parametrize(
    "payload", [{}, {"data": {}}, {"data": [{}]}, {"data": [{"id": ""}]}, {"data": [{"id": 3}]}]
)
def test_models_parser_rejects_invalid_contract_without_body(payload: object) -> None:
    with pytest.raises(InvalidProviderResponseError) as caught:
        parse_models_response(json.dumps(payload).encode())
    assert str(payload) not in caught.value.message
