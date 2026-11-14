# Custom LLM judge extensions

Thistlewarden supports trusted local Python judge implementations through extension API `1.0`. Extensions execute with application process privileges. Install and review them as application code. Thistlewarden does not sandbox plugins, install them from browser/API input, or load arbitrary import paths from stored data.

## Contract and safeguards

Public types live in `evaluator.judge_extensions` and carry strict Mypy-compatible annotations:

- `JudgeEvaluationRequest` contains immutable canonical `messages`, explicit `expected_result` and `actual_result`, optional `context`, immutable preset snapshot, and validated implementation configuration.
- Current cases map optional system prompt to one `system` message and required prompt to one `user` message. Results remain separate untrusted data.
- `JudgeProvider` is only runtime capability. It exposes `complete(prompt=..., system_prompt=...)`, not credentials, endpoint records, Flask objects, database connections, mutable records, or unrelated run data.
- Provider calls still use selected judge model, endpoint SSRF checks, concurrency/rate limits, provider retries, cancellation, shutdown transport, normalized usage, request IDs, and cost calculation. One evaluation may make at most two calls, supporting one parse retry. Exceeding limit fails job.
- Core validates score bounds, exact criterion IDs/order, criterion reasons, weighted score consistency, threshold/pass result, JSON metadata, and 32 KiB implementation-metadata limit.
- Extension exceptions become stable `judge_extension_error` job failures. Prompt, expected result, actual result, provider body, and configuration are not logged with error.

`implementation_metadata` is displayed and exported with result. Keep it non-sensitive, bounded, and namespaced by implementation identity already stored beside it.

## Complete minimal implementation

See [`examples/custom_judge.py`](../examples/custom_judge.py) for runnable source. Essential shape:

```python
from decimal import Decimal
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from evaluator.judge_extensions import (
    JUDGE_EXTENSION_API_VERSION,
    JudgeCriterionDecision,
    JudgeDecision,
    JudgeEvaluationRequest,
    JudgeImplementationRegistry,
    JudgeProvider,
)


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    instruction: str = "Evaluate response."


class MyJudge:
    implementation_id: ClassVar[str] = "acme.my-judge"
    implementation_version: ClassVar[str] = "1.0.0"
    api_version: ClassVar[str] = JUDGE_EXTENSION_API_VERSION
    configuration_model: ClassVar[type[BaseModel]] = Configuration

    def evaluate(
        self, request: JudgeEvaluationRequest, provider: JudgeProvider
    ) -> JudgeDecision:
        config = Configuration.model_validate(request.configuration)
        provider.complete(prompt=request.actual_result, system_prompt=config.instruction)
        criteria = tuple(
            JudgeCriterionDecision(id=item.id, score=Decimal("1"), reason="Pass")
            for item in request.preset.rubric
        )
        return JudgeDecision(
            score=Decimal("1"), reason="Accepted", criteria=criteria,
            metadata={"strategy": "minimal"},
        )


def register_judges(registry: JudgeImplementationRegistry) -> None:
    registry.register(MyJudge())
```

Real judges should quote or structurally encode every untrusted request field and parse provider output strictly. Never concatenate expected/actual text into instruction context without unambiguous data delimiters.

## Install and register

Package extension as normal Python package in same environment as Thistlewarden. Pin package version and dependencies in deployment requirements. Do not add extension module to Thistlewarden source.

Application bootstrap:

```python
from evaluator import create_app
from my_judge_package import register_judges

app = create_app(register_judges=register_judges)
```

Built-in `openai-compatible@1.0` registers first. Hook runs synchronously before repositories, worker threads, and request handling. Duplicate identity, malformed identity, incompatible API version, or invalid configuration model aborts startup. Registry freezes after hook; late registration fails.

For standard `thistlewarden` CLI, create small deployment launcher calling `create_app` with hook and serve returned Flask application. Registration is intentionally code-only: no package entry-point discovery, environment import path, GUI upload, or API registration exists in API v1.

## Configure profile

Create/edit judge execution profile in GUI and select `implementation_id @ implementation_version`. Enter configuration as JSON object. API example:

```json
{
  "name": "Acme judge",
  "model_profile_id": "00000000-0000-4000-8000-000000000001",
  "implementation_id": "acme.my-judge",
  "implementation_version": "1.0.0",
  "implementation_config": {"instruction": "Evaluate response."},
  "active": true
}
```

Configuration gets validated by selected Pydantic model on create/update, import, and run start. Keys resembling credentials (`secret`, `token`, `password`, `api_key`, or `credential`) are rejected. Reference existing endpoint/model profile for provider credentials; never put secrets in extension configuration.

Run snapshot records exact implementation ID/version, validated configuration, preset ID/version (inside testcase snapshot), model profile, and inference overrides. Missing implementation/version blocks new affected runs. No fallback occurs. Existing completed snapshots/results/exports remain readable without extension package because viewing does not execute extension code.

## Test

Extension package should run:

```bash
python -m mypy --strict src
python -m pytest
```

Tests should register implementation into fresh `JudgeImplementationRegistry`, freeze it, validate good/bad configuration, call `evaluate` with fake `JudgeProvider`, then pass decision through `validate_decision`. Include malformed provider output, cancellation propagation, two-call maximum, metadata boundary, criterion order, score mismatch, and exception sanitization.

Thistlewarden's contract tests cover registry collisions/freezing, unavailable versions, strict config, canonical messages, malformed decisions, metadata limit, cancellation, profile selection, snapshots, exports, missing-plugin restart, and unchanged built-in prompt/parse behavior.

## Version and upgrade

Treat `(implementation_id, implementation_version)` as immutable semantics:

1. Never change behavior or configuration meaning for released identity/version.
2. Register new version for semantic or schema changes; keep old implementation registered while pending runs or profiles use it.
3. Create/update profiles to select new version and run regression suite.
4. Retain old package until no pending/running work references it.
5. Old package may then be removed; historical completed results remain readable/exportable.

Changing extension API requires new `api_version`. Current registry rejects incompatible versions at startup. Configuration migrations belong in extension package and must produce new versioned configuration; core never guesses conversion.
