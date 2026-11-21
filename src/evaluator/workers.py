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
from evaluator.logging_config import log_context, log_event, log_unexpected_exception
from evaluator.provider import ProviderGuardrailError
from evaluator.runs import IncompleteTargetTurn, JobLease, LeaseLost, RunQueue
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
            outcome, transcript = self._execute_target_turns(
                lease, case, model, cancelled=cancelled, lost=lost
            )
            if cancelled is not None and cancelled.is_set():
                raise CancelledError
            if lost.is_set():
                raise LeaseLost
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
                evaluation_case = {**case, "target_transcript": transcript}
                result = self._evaluate(lease.run_id, evaluation_case, outcome.content)
                metrics = dict(outcome.metrics)
                if case["evaluation_method"] == "llm_judge":
                    metrics["judge_cost"] = result.metadata.get("judge_cost")
                self._queue.complete(
                    lease,
                    status="passed" if result.passed else "failed",
                    score=str(result.score),
                    weight=str(case["weight"]),
                    result={
                        **result.model_dump(mode="json"),
                        "candidate": outcome.content,
                        "evaluated_answer": outcome.content,
                        "transcript": transcript,
                    },
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
        except IncompleteTargetTurn:
            log_event(_LOG, logging.WARNING, "target_turn_not_replayed", job_id=lease.job_id)
            try:
                self._queue.fail(
                    lease,
                    "incomplete_target_turn",
                    {
                        "outcome_category": "interrupted",
                        "outcome_stage": "target",
                        "outcome_code": "incomplete_target_turn",
                        "incomplete": True,
                    },
                )
            except LeaseLost:
                log_event(_LOG, logging.WARNING, "lease_lost", job_id=lease.job_id)
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
            log_unexpected_exception(
                _LOG,
                "job_failed",
                exc,
                job_id=lease.job_id,
                error_code="pipeline_error",
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

    def _execute_target_turns(
        self,
        lease: JobLease,
        case: dict[str, Any],
        model: dict[str, Any],
        *,
        cancelled: threading.Event | None,
        lost: threading.Event,
    ) -> tuple[TargetOutcome, list[dict[str, str]]]:
        prompts = case.get("user_prompts", [case["prompt"]])
        completed = self._queue.turn_state(lease)
        if len(completed) > len(prompts):
            raise RuntimeError("target turn checkpoints exceed case prompts")
        transcript: list[dict[str, str]] = []
        outcomes: list[TargetOutcome] = []
        for turn in completed:
            if turn["user_prompt"] != prompts[turn["turn_index"]]:
                raise RuntimeError("target turn checkpoint prompt does not match case snapshot")
            response = turn["assistant_response"]
            if not isinstance(response, str):
                raise RuntimeError("completed target turn has no assistant response")
            transcript.extend(
                (
                    {"role": "user", "content": turn["user_prompt"]},
                    {"role": "assistant", "content": response},
                )
            )
            outcomes.append(
                TargetOutcome(
                    response,
                    metrics=json.loads(turn["metrics_json"] or "{}"),
                    attempts=tuple(json.loads(turn["attempts_json"] or "[]")),
                    provider_evidence=json.loads(turn["provider_evidence_json"] or "{}"),
                )
            )
        for index in range(len(completed), len(prompts)):
            if cancelled is not None and cancelled.is_set():
                raise CancelledError
            if lost.is_set():
                raise LeaseLost
            prompt = prompts[index]
            self._queue.start_turn(lease, index, prompt)
            turn_case = dict(case)
            turn_case["prompt"] = prompt
            turn_case["conversation_messages"] = list(transcript) + [
                {"role": "user", "content": prompt}
            ]
            outcome = self._target(turn_case, model)
            if cancelled is not None and cancelled.is_set():
                raise CancelledError
            if lost.is_set():
                raise LeaseLost
            self._queue.complete_turn(
                lease,
                index,
                assistant_response=outcome.content,
                metrics=outcome.metrics,
                attempts=outcome.attempts,
                provider_evidence=outcome.provider_evidence,
            )
            for attempt in outcome.attempts:
                self._queue.record_attempt(lease, attempt.get("category"), attempt)
            if not outcome.evaluate:
                outcomes.append(outcome)
                return (
                    TargetOutcome(
                        outcome.content,
                        evaluate=False,
                        metrics=self._aggregate_target_metrics(outcomes),
                        attempts=outcome.attempts,
                        error_code=outcome.error_code,
                        outcome_category=outcome.outcome_category,
                        provider_evidence=outcome.provider_evidence,
                    ),
                    transcript + [{"role": "user", "content": prompt}],
                )
            transcript.extend(
                (
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": outcome.content},
                )
            )
            outcomes.append(outcome)
        if not outcomes:
            raise RuntimeError("case has no target turns")
        final = outcomes[-1]
        return (
            TargetOutcome(
                final.content,
                metrics=self._aggregate_target_metrics(outcomes),
                attempts=final.attempts,
                error_code=final.error_code,
                outcome_category=final.outcome_category,
                provider_evidence=final.provider_evidence,
            ),
            transcript,
        )

    @staticmethod
    def _aggregate_target_metrics(outcomes: list[TargetOutcome]) -> dict[str, Any]:
        metrics: dict[str, Any] = {}
        for key in (
            "target_cost",
            "target_latency_ms",
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "reasoning_tokens",
        ):
            values = [outcome.metrics.get(key) for outcome in outcomes]
            if all(value is not None for value in values):
                total = sum((Decimal(str(value)) for value in values), Decimal(0))
                token_key = key in {
                    "input_tokens",
                    "output_tokens",
                    "cached_input_tokens",
                    "reasoning_tokens",
                }
                metrics[key] = int(total) if token_key else str(total)
        final_metrics = outcomes[-1].metrics
        metrics["ttft_ms"] = final_metrics.get("ttft_ms")
        metrics["tps"] = final_metrics.get("tps")
        return metrics

    def _evaluate(self, run_id: UUID, case: dict[str, Any], candidate: str) -> EvaluationResult:
        config = json.loads(case["evaluator_config_json"])
        data = GoldenSetItemInput(
            case_id=case["case_id"],
            user_prompts=case.get("user_prompts", [case["prompt"]]),
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
