from __future__ import annotations

from decimal import Decimal

import pytest

from evaluator.golden_set_io import export_csv, export_json, parse_import
from evaluator.golden_sets import GoldenSetInput, GoldenSetItemInput

pytestmark = pytest.mark.unit


def _items() -> list[GoldenSetItemInput]:
    return [
        GoldenSetItemInput(
            case_id="unicode-é",
            prompt='line 1\nline 2, "quoted"',
            system_prompt="systeem",
            expected_answer="ja",
            evaluation_method="exact_match",
            evaluator_config={"trim": True},
            category="cat",
            tags=["één", "twee"],
            weight=Decimal("1.2500"),
            enabled=False,
            sort_order=0,
        )
    ]


def test_json_round_trip_is_lossless_and_unicode() -> None:
    data = GoldenSetInput(name="Set é", description="multi\nline", tags=["é"])
    exported = export_json(data, _items())
    assert '"schema_version": "2.0"' in exported.decode()
    assert '"user_prompts"' in exported.decode()
    parsed = parse_import(exported, "json", None)
    assert GoldenSetInput.model_validate(parsed.set_payload) == data
    restored = GoldenSetItemInput.model_validate(parsed.item_payloads[0])
    assert restored.model_dump(exclude={"sort_order"}) == _items()[0].model_dump(
        exclude={"sort_order"}
    )


def test_csv_round_trip_handles_multiline_quotes_decimal_tags_and_booleans() -> None:
    exported = export_csv(_items())
    assert b"user_prompts" in exported.splitlines()[0]
    parsed = parse_import(exported, "csv", "CSV é")
    restored = GoldenSetItemInput.model_validate(parsed.item_payloads[0])
    assert restored.model_dump(exclude={"sort_order"}) == _items()[0].model_dump(
        exclude={"sort_order"}
    )
    assert parsed.row_numbers == [2]


def test_multi_turn_json_and_csv_round_trip_preserves_turn_order() -> None:
    item = _items()[0].model_copy(update={"user_prompts": ["first", "second", "third"]})
    data = GoldenSetInput(name="Multi-turn")
    assert GoldenSetItemInput.model_validate(
        parse_import(export_json(data, [item]), "json", None).item_payloads[0]
    ).user_prompts == ["first", "second", "third"]
    assert GoldenSetItemInput.model_validate(
        parse_import(export_csv([item]), "csv", "Multi-turn").item_payloads[0]
    ).user_prompts == ["first", "second", "third"]


def test_legacy_json_and_csv_import_as_single_turn() -> None:
    legacy_json = (
        b'{"name":"Legacy","items":[{"case_id":"one","prompt":"hello",'
        b'"evaluation_method":"exact_match","evaluator_config":{"expected_value":"yes"}}]}'
    )
    assert GoldenSetItemInput.model_validate(
        parse_import(legacy_json, "json", None).item_payloads[0]
    ).user_prompts == ["hello"]
    legacy_csv = (
        b"case_id,prompt,system_prompt,expected_answer,evaluation_method,evaluator_config,"
        b'category,tags,weight,enabled\r\none,hello,,,exact_match,"{""expected_value"":""yes""}",,[],1,true\r\n'
    )
    assert GoldenSetItemInput.model_validate(
        parse_import(legacy_csv, "csv", "Legacy").item_payloads[0]
    ).user_prompts == ["hello"]


def test_csv_rejects_invalid_boolean_with_row_and_case() -> None:
    content = export_csv(_items()).replace(b",false\r\n", b",maybe\r\n")
    with pytest.raises(ValueError, match=r"CSV row 2 \(unicode-.\).+enabled"):
        parse_import(content, "csv", "Set")
