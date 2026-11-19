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
    parsed = parse_import(export_json(data, _items()), "json", None)
    assert GoldenSetInput.model_validate(parsed.set_payload) == data
    restored = GoldenSetItemInput.model_validate(parsed.item_payloads[0])
    assert restored.model_dump(exclude={"sort_order"}) == _items()[0].model_dump(
        exclude={"sort_order"}
    )


def test_csv_round_trip_handles_multiline_quotes_decimal_tags_and_booleans() -> None:
    parsed = parse_import(export_csv(_items()), "csv", "CSV é")
    restored = GoldenSetItemInput.model_validate(parsed.item_payloads[0])
    assert restored.model_dump(exclude={"sort_order"}) == _items()[0].model_dump(
        exclude={"sort_order"}
    )
    assert parsed.row_numbers == [2]


def test_csv_rejects_invalid_boolean_with_row_and_case() -> None:
    content = export_csv(_items()).replace(b",false\r\n", b",maybe\r\n")
    with pytest.raises(ValueError, match=r"CSV row 2 \(unicode-.\).+enabled"):
        parse_import(content, "csv", "Set")
