"""Dependency-injected fenced worker pipeline with lease heartbeats."""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import CancelledError
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4

from evaluator.api.errors import (
    AuthenticationError,
    InvalidProviderResponseError,
    NetworkError,
    ProviderClientError,
    ProviderServerError,
    RateLimitError,
    TimeoutError,
)
from evaluator.evaluators import EvaluationResult, EvaluatorRegistry
from evaluator.gates import QualityGateService
from evaluator.golden_set_contracts import GoldenSetItemInput, GoldenSetItemRecord
from evaluator.judging.extensions import (
    JudgeCancelledError,
    JudgeExecutionError,
    JudgeExtensionError,
)
from evaluator.logging_config import log_context, log_event
from evaluator.provider import ProviderGuardrailError
from evaluator.runs import JobLease, LeaseLost, RunQueue
from evaluator.time import utc_now

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TargetOutcome:
    content: str
    evaluate: bool = True
    metrics: dict[str, Any] = field(default_factory=dict)
    attempts: tuple[dict[str, Any], ...] = ()
    error_code: str | None = None
    outcome_category: str = "normal"
    provider_evidence: dict[str, Any] = field(default_factory=dict)


class TargetCaller(Protocol):
    def __call__(self, case: dict[str, Any], model: dict[str, Any]) -> TargetOutcome: ...


class JudgeCaller(Protocol):
    def __call__(self, run_id: UUID, case: dict[str, Any], candidate: str) -> EvaluationResult: ...


class WorkerPipeline:
    def __init__(
        self,
        queue: RunQueue,
        target: TargetCaller,
        *,
        evaluators: EvaluatorRegistry | None = None,
        judge: JudgeCaller | None = None,
        heartbeat_interval: float = 10.0,
        gates: QualityGateService | None = None,
    ) -> None:
        self._queue, self._target = queue, target
        self._evaluators, self._judge = evaluators or EvaluatorRegistry(), judge
        self._heartbeat_interval = heartbeat_interval
        self._gates = gates

    def run_once(self, owner: str, *, cancelled: threading.Event | None = None) -> bool:
        if cancelled is not None and cancelled.is_set():
            return False
        lease = self._queue.claim(owner)
        if lease is None:
            return False
        stop = threading.Event()
        lost = threading.Event()
        thread = threading.Thread(target=self._heartbeat, args=(lease, stop, lost), daemon=True)
        thread.start()
        try:
            case = self._queue.snapshot(lease.case_snapshot_id, "case")
            with log_context(
                run_id=str(lease.run_id),
                case_id=str(case["case_id"]),
                execution_id=str(uuid4()),
            ):
                return self._execute_claimed(lease, case, stop, lost, thread, cancelled)
        finally:
            if thread.is_alive():
                stop.set()
                thread.join(timeout=max(self._heartbeat_interval * 2, 0.1))

    def _execute_claimed(
        self,
        lease: JobLease,
        case: dict[str, Any],
        stop: threading.Event,
        lost: threading.Event,
        thread: threading.Thread,
        cancelled: threading.Event | None,
    ) -> bool:
        log_event(
            _LOG,
            logging.DEBUG,
            "job_claimed",
            job_id=lease.job_id,
            generation=lease.generation,
            evaluator_method=case["evaluation_method"],
        )
        stage = "target"
        try:
            model = self._queue.snapshot(lease.model_snapshot_id, "model")
            log_event(
                _LOG,
                logging.DEBUG,
                "target_call_started",
                model_id=model["model_id"],
                endpoint_id=model["endpoint_id"],
            )
            if cancelled is not None and cancelled.is_set():
                raise CancelledError
            outcome = self._target(case, model)
            if cancelled is not None and cancelled.is_set():
                raise CancelledError
            if lost.is_set():
                raise LeaseLost
            for attempt in outcome.attempts:
                self._queue.record_attempt(lease, attempt.get("category"), attempt)
            if not outcome.evaluate:
                self._queue.fail(
                    lease,
                    outcome.error_code or "target_error",
                    {
                        "outcome_category": outcome.outcome_category,
                        "outcome_stage": "target",
                        "outcome_code": outcome.error_code,
                        "provider_evidence": outcome.provider_evidence,
                        "candidate": outcome.content,
                        "partial_candidate": bool(outcome.content),
                    },
                    metrics=outcome.metrics,
                )
            else:
                stage = "judge" if case["evaluation_method"] == "llm_judge" else "evaluator"
                log_event(
                    _LOG,
                    logging.DEBUG,
                    "evaluation_started",
                    evaluator_method=case["evaluation_method"],
                )
                result = self._evaluate(lease.run_id, case, outcome.content)
                metrics = dict(outcome.metrics)
                if case["evaluation_method"] == "llm_judge":
                    metrics["judge_cost"] = result.metadata.get("judge_cost")
                self._queue.complete(
                    lease,
                    status="passed" if result.passed else "failed",
                    score=str(result.score),
                    weight=str(case["weight"]),
                    result={**result.model_dump(mode="json"), "candidate": outcome.content},
                    metrics=metrics,
                )
                log_event(
                    _LOG,
                    logging.DEBUG,
                    "evaluation_completed",
                    evaluator_method=case["evaluation_method"],
                    score=result.score,
                    outcome="passed" if result.passed else "failed",
                )
        except JudgeCancelledError as exc:
            log_event(_LOG, logging.WARNING, "job_interrupted", job_id=lease.job_id)
            try:
                self._queue.fail(
                    lease,
                    "judge_cancelled",
                    {
                        "outcome_category": "cancelled",
                        "outcome_stage": "judge",
                        "outcome_code": "judge_cancelled",
                        "provider_calls": list(exc.provider_calls),
                        "incomplete": True,
                    },
                    metrics={"judge_cost": str(exc.judge_cost)},
                )
            except LeaseLost:
                log_event(_LOG, logging.WARNING, "lease_lost", job_id=lease.job_id)
            return True
        except CancelledError:
            log_event(_LOG, logging.WARNING, "job_interrupted", job_id=lease.job_id)
            return True
        except LeaseLost:
            log_event(_LOG, logging.WARNING, "lease_lost", job_id=lease.job_id)
            return True
        except ProviderGuardrailError as exc:
            evidence = exc.evidence.as_dict()
            log_event(
                _LOG,
                logging.WARNING,
                "provider_guardrail_intervened",
                job_id=lease.job_id,
                outcome_stage=stage,
                provider_code=evidence.get("provider_code"),
                finish_reason=evidence.get("finish_reason"),
            )
            try:
                self._queue.fail(
                    lease,
                    "provider_guardrail",
                    {
                        "outcome_category": "provider_guardrail",
                        "outcome_stage": stage,
                        "outcome_code": "provider_guardrail",
                        "provider_evidence": evidence,
                    },
                )
            except LeaseLost:
                log_event(_LOG, logging.WARNING, "lease_lost", job_id=lease.job_id)
        except (
            AuthenticationError,
            InvalidProviderResponseError,
            NetworkError,
            ProviderClientError,
            ProviderServerError,
            RateLimitError,
            TimeoutError,
        ) as exc:
            log_event(
                _LOG,
                logging.ERROR,
                "provider_call_failed",
                job_id=lease.job_id,
                error_code=exc.code.lower(),
                exception_type=type(exc).__name__,
                outcome_stage=stage,
            )
            try:
                self._queue.fail(
                    lease,
                    exc.code.lower(),
                    {
                        "outcome_category": "provider_error",
                        "outcome_stage": stage,
                        "outcome_code": exc.code.lower(),
                    },
                )
            except LeaseLost:
                log_event(_LOG, logging.WARNING, "lease_lost", job_id=lease.job_id)
        except JudgeExtensionError as exc:
            log_event(
                _LOG,
                logging.ERROR,
                "judge_extension_failed",
                job_id=lease.job_id,
                error_code="judge_extension_error",
                exception_type=type(exc).__name__,
                evaluator_method=case["evaluation_method"],
            )
            try:
                provider_calls = (
                    list(exc.provider_calls) if isinstance(exc, JudgeExecutionError) else []
                )
                judge_cost = str(exc.judge_cost) if isinstance(exc, JudgeExecutionError) else None
                self._queue.fail(
                    lease,
                    "judge_extension_error",
                    {
                        "error_type": type(exc).__name__,
                        "outcome_category": "processing_error",
                        "outcome_stage": "judge",
                        "outcome_code": "judge_extension_error",
                        "provider_calls": provider_calls,
                        "incomplete": True,
                    },
                    metrics={"judge_cost": judge_cost},
                )
            except LeaseLost:
                log_event(_LOG, logging.WARNING, "lease_lost", job_id=lease.job_id)
        except Exception as exc:
            log_event(
                _LOG,
                logging.ERROR,
                "job_failed",
                job_id=lease.job_id,
                error_code="pipeline_error",
                exception_type=type(exc).__name__,
                evaluator_method=case["evaluation_method"],
            )
            try:
                self._queue.fail(
                    lease,
                    "pipeline_error",
                    {
                        "error_type": type(exc).__name__,
                        "outcome_category": "processing_error",
                        "outcome_stage": stage,
                        "outcome_code": "pipeline_error",
                    },
                )
            except LeaseLost:
                log_event(_LOG, logging.WARNING, "lease_lost", job_id=lease.job_id)
        finally:
            stop.set()
            thread.join(timeout=max(self._heartbeat_interval * 2, 0.1))
            status = self._queue.refresh_run_status(lease.run_id)
            if self._gates is not None and status in {"completed", "completed_with_errors"}:
                gate_status = self._gates.evaluate_run(lease.run_id)
                log_event(
                    _LOG,
                    logging.INFO,
                    "run_completed",
                    status=status,
                    gate_status=gate_status,
                )
        return True

    def _evaluate(self, run_id: UUID, case: dict[str, Any], candidate: str) -> EvaluationResult:
        config = json.loads(case["evaluator_config_json"])
        data = GoldenSetItemInput(
            case_id=case["case_id"],
            prompt=case["prompt"],
            system_prompt=case["system_prompt"],
            expected_answer=case["expected_answer"],
            evaluation_method=case["evaluation_method"],
            evaluator_config=config,
            category=case["category"],
            tags=json.loads(case["tags_json"]),
            weight=Decimal(case["weight"]),
            sort_order=0,
        )
        item = GoldenSetItemRecord(
            id=UUID(int=0),
            golden_set_id=UUID(int=0),
            data=data,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        if data.evaluation_method == "llm_judge":
            if self._judge is None:
                raise RuntimeError("judge dependency unavailable")
            return self._judge(run_id, case, candidate)
        return self._evaluators.evaluate(item, candidate)

    def _heartbeat(self, lease: JobLease, stop: threading.Event, lost: threading.Event) -> None:
        current = lease
        while not stop.wait(self._heartbeat_interval):
            try:
                current = self._queue.heartbeat(current)
            except LeaseLost:
                lost.set()
                return
