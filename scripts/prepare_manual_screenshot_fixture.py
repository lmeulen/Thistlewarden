"""Create deterministic, non-production data for user-manual screenshots.

Usage: PYTHONPATH=src python scripts/prepare_manual_screenshot_fixture.py OUTPUT.db
The output path must not exist. No provider call or credential is created.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import UUID, uuid5

from evaluator.config_io import ConfigurationExchangeService
from evaluator.database import SQLiteConnectionFactory, transaction
from evaluator.judging.builtin import register_builtin_judge
from evaluator.judging.extensions import JudgeImplementationRegistry
from evaluator.migrations import MigrationManager
from evaluator.runs import RunQueue

ENDPOINT_ID = "10000000-0000-4000-8000-000000000001"
MODEL_IDS = [
    "20000000-0000-4000-8000-000000000001",
    "20000000-0000-4000-8000-000000000002",
]
JUDGE_ID = "30000000-0000-4000-8000-000000000001"
SET_ID = "40000000-0000-4000-8000-000000000001"
ITEM_NAMESPACE = UUID("50000000-0000-4000-8000-000000000000")
RUN_NAMESPACE = UUID("60000000-0000-4000-8000-000000000000")

CASES = [
    (
        "exact-status",
        "Return approved status label.",
        "APPROVED",
        "exact_match",
        {
            "expected_value": "APPROVED",
            "trim": True,
            "case_sensitive": True,
            "normalize_newlines": True,
            "collapse_whitespace": False,
        },
    ),
    (
        "refund-details",
        "Explain synthetic refund timing.",
        None,
        "contains",
        {
            "expected_fragments": ["refund", "five business days"],
            "match_mode": "all",
            "case_sensitive": False,
            "normalize_newlines": True,
            "collapse_whitespace": True,
        },
    ),
    (
        "ticket-code",
        "Return synthetic ticket identifier.",
        None,
        "regex",
        {
            "pattern": "^ORDER-[0-9]{6}$",
            "full_match": True,
            "case_sensitive": True,
            "multiline": False,
            "dot_all": False,
        },
    ),
    (
        "estimated-days",
        "Return estimated duration in days.",
        None,
        "numeric",
        {
            "expected_value": "5",
            "absolute_tolerance": "0.5",
            "relative_tolerance_percent": "0",
            "extraction": "entire_answer",
            "decimal_separator": ".",
            "allow_thousands_separator": False,
        },
    ),
    (
        "structured-status",
        "Return synthetic status as JSON.",
        None,
        "json_schema",
        {
            "schema": {
                "type": "object",
                "required": ["status", "reference"],
                "properties": {
                    "status": {"const": "approved"},
                    "reference": {"type": "string"},
                },
                "additionalProperties": False,
            },
            "allow_markdown_code_fence": False,
            "case_sensitive": True,
            "schema_draft": "2020-12",
        },
    ),
    (
        "response-quality",
        "Give a concise synthetic support response.",
        "Response is relevant, clear, and complete.",
        "llm_judge",
        {
            "preset_id": "reference-correctness",
            "preset_version": "1.0",
            "expected_value": "Response is relevant, clear, and complete.",
            "context": "Synthetic documentation example only.",
        },
    ),
]


def bundle() -> dict[str, object]:
    items = []
    for order, (case_id, prompt, expected, method, config) in enumerate(CASES):
        items.append(
            {
                "id": str(uuid5(ITEM_NAMESPACE, case_id)),
                "data": {
                    "golden_set_id": SET_ID,
                    "case_id": case_id,
                    "prompt": prompt,
                    "system_prompt": "You are a synthetic documentation assistant.",
                    "expected_answer": expected,
                    "evaluation_method": method,
                    "evaluator_config": config,
                    "category": "documentation example",
                    "tags": ["synthetic", "manual"],
                    "weight": "1",
                    "enabled": True,
                    "sort_order": order,
                },
            }
        )
    return {
        "schema_version": "1.0",
        "endpoints": [
            {
                "id": ENDPOINT_ID,
                "data": {
                    "name": "Synthetic documentation endpoint",
                    "completions_url": "https://provider.example.invalid/v1/chat/completions",
                    "models_url": "https://provider.example.invalid/v1/models",
                    "api_key": None,
                    "api_key_header": "Authorization",
                    "api_key_prefix": "Bearer ",
                    "extra_headers": [],
                    "timeout_seconds": 30,
                    "max_retries": 1,
                    "requests_per_minute": 60,
                    "tokens_per_minute": 100000,
                    "max_concurrency": 2,
                    "active": True,
                    "trusted_internal_http": False,
                    "security_warning_confirmed": False,
                    "secret_missing": False,
                    "api_format": "openai",
                },
            }
        ],
        "model_profiles": [
            {
                "id": model_id,
                "data": {
                    "endpoint_id": ENDPOINT_ID,
                    "model_id": machine_name,
                    "display_name": display_name,
                    "description": "Synthetic documentation profile; not a benchmark.",
                    "temperature": temperature,
                    "top_p": None,
                    "max_tokens": 256,
                    "max_tokens_field_path": "max_tokens",
                    "reasoning_field_path": None,
                    "reasoning_value": None,
                    "send_reasoning_off": False,
                    "custom_parameters": {},
                    "input_per_million": input_price,
                    "output_per_million": output_price,
                    "cached_input_per_million": None,
                    "reasoning_per_million": None,
                    "fixed_per_request": "0",
                    "context_threshold": None,
                    "high_context_input_per_million": None,
                    "high_context_output_per_million": None,
                    "high_context_cached_input_per_million": None,
                    "high_context_reasoning_per_million": None,
                    "active": True,
                },
            }
            for model_id, machine_name, display_name, temperature, input_price, output_price in [
                (MODEL_IDS[0], "synthetic-fast", "Synthetic Fast", "0", "1.00", "4.00"),
                (MODEL_IDS[1], "synthetic-careful", "Synthetic Careful", "0.2", "2.00", "8.00"),
            ]
        ],
        "judge_profiles": [
            {
                "id": JUDGE_ID,
                "data": {
                    "name": "Synthetic documentation judge",
                    "model_profile_id": MODEL_IDS[1],
                    "temperature": "0",
                    "reasoning_value": None,
                    "max_output_tokens": 256,
                    "is_global_default": True,
                    "active": True,
                    "implementation_id": "openai-compatible",
                    "implementation_version": "1.0",
                    "implementation_config": {},
                },
            }
        ],
        "golden_sets": [
            {
                "id": SET_ID,
                "data": {
                    "name": "Synthetic support examples",
                    "description": "Documentation-only examples; no benchmark claims.",
                    "version": "1.0",
                    "tags": ["synthetic", "manual"],
                    "active": True,
                    "judge_profile_id": JUDGE_ID,
                    "quality_gates": {"min_pass_rate": "0.8", "max_error_rate": "0.1"},
                },
            }
        ],
        "golden_set_items": items,
    }


def finish_run(
    factory: SQLiteConnectionFactory, run_id: UUID, completed_at: str, failed: bool
) -> None:
    connection = factory.connect()
    try:
        with transaction(connection):
            jobs = connection.execute(
                "SELECT id,case_snapshot_id,model_snapshot_id FROM run_jobs "
                "WHERE run_id=? ORDER BY id",
                (str(run_id),),
            ).fetchall()
            for index, job in enumerate(jobs):
                status = "failed" if failed and index in {2, 7} else "passed"
                score = "0" if status == "failed" else ("0.9" if index % 3 == 0 else "1")
                connection.execute(
                    "UPDATE run_jobs SET status='completed',attempt_count=1,"
                    "updated_at=? WHERE id=?",
                    (completed_at, job["id"]),
                )
                connection.execute(
                    """INSERT INTO run_results(
                    id,job_id,run_id,model_snapshot_id,case_snapshot_id,status,score,weight,
                    result_json,target_cost,judge_cost,target_latency_ms,ttft_ms,tps,input_tokens,
                    output_tokens,cached_input_tokens,reasoning_tokens,created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid5(RUN_NAMESPACE, f"result-{run_id}-{index}")),
                        job["id"],
                        str(run_id),
                        job["model_snapshot_id"],
                        job["case_snapshot_id"],
                        status,
                        score,
                        "1",
                        json.dumps({"reason": "Synthetic fixture outcome."}),
                        f"0.00{index + 1}",
                        None,
                        str(620 + index * 37),
                        str(105 + index * 9),
                        str(34 + index),
                        80 + index,
                        24 + index,
                        None,
                        None,
                        completed_at,
                    ),
                )
            connection.execute(
                """UPDATE runs SET status='completed',started_at=?,completed_at=?,gate_status=?
                WHERE id=?""",
                (
                    completed_at.replace("10:00:00", "09:59:30").replace("11:30:00", "11:29:30"),
                    completed_at,
                    "failed" if failed else "passed",
                    str(run_id),
                ),
            )
    finally:
        connection.close()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: prepare_manual_screenshot_fixture.py OUTPUT.db")
    output = Path(sys.argv[1]).resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite existing file: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    factory = SQLiteConnectionFactory(output)
    MigrationManager(factory).migrate()
    registry = JudgeImplementationRegistry()
    register_builtin_judge(registry)
    registry.freeze()
    ConfigurationExchangeService(factory, registry).import_bundle(bundle(), "merge", preview=False)
    queue = RunQueue(factory, max_profiles=10, judge_registry=registry)
    runs = [
        (
            "Synthetic baseline run",
            [MODEL_IDS[0]],
            "2026-10-15T10:00:00.000000Z",
            False,
        ),
        (
            "Synthetic comparison run",
            MODEL_IDS,
            "2026-10-16T11:30:00.000000Z",
            True,
        ),
    ]
    for name, model_ids, completed_at, failed in runs:
        run_id = queue.create_run(
            {
                "name": name,
                "golden_set_id": SET_ID,
                "model_profile_ids": model_ids,
                "judge_profile_id": JUDGE_ID,
                "note": "Synthetic documentation fixture; not a benchmark.",
            }
        )
        finish_run(factory, run_id, completed_at, failed)
    print(output)


if __name__ == "__main__":
    main()
