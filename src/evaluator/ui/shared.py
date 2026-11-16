"""Shared GUI blueprint, security, service access, and form helpers."""

# ruff: noqa: F401

from __future__ import annotations

import hmac
import json
import logging
import secrets
from decimal import Decimal
from io import BytesIO
from typing import Any, Literal, cast
from urllib.parse import parse_qs, urlencode
from uuid import UUID

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.wrappers import Response

from evaluator.aggregates import AggregateService
from evaluator.api.errors import ApiError, ValidationApiError
from evaluator.config_io import ConfigurationExchangeService, ImportPlan
from evaluator.dashboard import DashboardService, DashboardStatistics
from evaluator.discovery import ModelDiscoveryService
from evaluator.endpoints import EndpointService, HeaderInput
from evaluator.errors import ConfigurationError
from evaluator.evaluation_preview import EvaluationPreviewService
from evaluator.evaluators.contracts import evaluator_config_examples
from evaluator.gates import QualityGateService
from evaluator.golden_set_service import GoldenSetService
from evaluator.judges import JUDGE_PRESETS, JudgeProfileService
from evaluator.logging_config import log_event
from evaluator.manual import manual_asset, manual_pages, render_manual_page
from evaluator.models import ModelProfileService
from evaluator.onboarding import OnboardingService
from evaluator.run_comparisons import ComparisonFilter, RunComparisonService
from evaluator.run_exports import RunExportService, safe_filename
from evaluator.run_views import RESULT_PAGE_SIZE, ResultQuery, RunViewService
from evaluator.runs import RunQueue
from evaluator.settings import ResolvedSettings, SettingLifecycle, SettingsService
from evaluator.time import to_utc_iso

__all__ = [
    "Any",
    "ApiError",
    "ComparisonFilter",
    "ConfigurationError",
    "DashboardService",
    "DashboardStatistics",
    "Decimal",
    "ImportPlan",
    "JUDGE_PRESETS",
    "Literal",
    "OnboardingService",
    "RESULT_PAGE_SIZE",
    "ResolvedSettings",
    "Response",
    "ResultQuery",
    "SettingLifecycle",
    "SettingsService",
    "UUID",
    "ValidationApiError",
    "_GUI_PAGE_SIZE",
    "_LOG",
    "_RUN_SET_PAGE_SIZE",
    "_TEST_CASE_TYPES",
    "_TEST_CASE_TYPE_VALUES",
    "_aggregates",
    "_all_models",
    "_bulk_item_payload",
    "_config_service",
    "_decimal",
    "_discovery_service",
    "_endpoint_payload",
    "_endpoint_service",
    "_error_message",
    "_evaluation_preview_service",
    "_gate_service",
    "_golden_service",
    "_golden_set_payload",
    "_item_payload",
    "_json_field",
    "_judge_payload",
    "_judge_service",
    "_model_payload",
    "_model_service",
    "_model_tester",
    "_onboarding_service",
    "_optional",
    "_page_details",
    "_requested_page",
    "_run_comparisons",
    "_run_exports",
    "_run_queue",
    "_run_views",
    "abort",
    "cast",
    "current_app",
    "evaluator_config_examples",
    "flash",
    "g",
    "gui",
    "json",
    "jsonify",
    "log_event",
    "logging",
    "parse_qs",
    "redirect",
    "render_template",
    "request",
    "safe_filename",
    "to_utc_iso",
    "url_for",
    "urlencode",
]

gui = Blueprint("gui", __name__)
_LOG = logging.getLogger(__name__)
_GUI_PAGE_SIZE = 100
_RUN_SET_PAGE_SIZE = 25
_TEST_CASE_TYPES = (
    ("exact_match", "Exact match"),
    ("contains", "Contains"),
    ("regex", "Regular expression"),
    ("numeric", "Numeric"),
    ("json_schema", "JSON Schema"),
    ("llm_judge", "LLM judge"),
)
_TEST_CASE_TYPE_VALUES = {value for value, _label in _TEST_CASE_TYPES}


@gui.get("/manual/")
@gui.get("/manual/<page>")
def manual_page(page: str = "index") -> str:
    try:
        content = render_manual_page(page)
    except FileNotFoundError:
        abort(404)
    return render_template(
        "manual.html",
        content=content,
        current_page=page,
        manual_pages=manual_pages(),
    )


@gui.get("/manual/assets/<name>")
def manual_image(name: str) -> Response:
    try:
        content = manual_asset(name)
    except FileNotFoundError:
        abort(404)
    return send_file(BytesIO(content), mimetype="image/png", max_age=3600)


@gui.errorhandler(ApiError)
def handle_gui_api_error(error: ApiError) -> tuple[str, int]:
    return render_template(
        "gui_error.html", message=error.message, status=error.status_code
    ), error.status_code


def csrf_token() -> str:
    token = session.get("csrf_token")
    if not isinstance(token, str):
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@gui.before_app_request
def protect_gui_mutations() -> None:
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.blueprint == "gui":
        expected = session.get("csrf_token")
        supplied = request.form.get("csrf_token", "")
        if not isinstance(expected, str) or not hmac.compare_digest(expected, supplied):
            abort(400, description="Invalid CSRF token.")


def _endpoint_service() -> EndpointService:
    return cast(EndpointService, current_app.extensions["endpoint_service"])


def _model_service() -> ModelProfileService:
    return cast(ModelProfileService, current_app.extensions["model_profile_service"])


def _model_tester() -> Any:
    return current_app.extensions["model_profile_tester"]


def _onboarding_service() -> OnboardingService:
    return cast(OnboardingService, current_app.extensions["onboarding_service"])


def _discovery_service() -> ModelDiscoveryService:
    return cast(ModelDiscoveryService, current_app.extensions["model_discovery_service"])


def _golden_service() -> GoldenSetService:
    return cast(GoldenSetService, current_app.extensions["golden_set_service"])


def _requested_page(name: str = "page") -> int:
    return max(request.args.get(name, 1, type=int) or 1, 1)


def _page_details(page: int, per_page: int, total: int) -> dict[str, int]:
    pages = max(1, (total + per_page - 1) // per_page)
    return {
        "page": min(page, pages),
        "pages": pages,
        "total": total,
        "range_start": (page - 1) * per_page + 1 if total else 0,
        "range_end": min(page * per_page, total),
    }


def _evaluation_preview_service() -> EvaluationPreviewService:
    return cast(EvaluationPreviewService, current_app.extensions["evaluation_preview_service"])


def _judge_service() -> JudgeProfileService:
    return cast(JudgeProfileService, current_app.extensions["judge_profile_service"])


def _gate_service() -> QualityGateService:
    return cast(QualityGateService, current_app.extensions["quality_gate_service"])


def _run_queue() -> RunQueue:
    return cast(RunQueue, current_app.extensions["run_queue"])


def _run_views() -> RunViewService:
    return cast(RunViewService, current_app.extensions["run_view_service"])


def _run_exports() -> RunExportService:
    return cast(RunExportService, current_app.extensions["run_export_service"])


def _run_comparisons() -> RunComparisonService:
    return cast(RunComparisonService, current_app.extensions["run_comparison_service"])


def _aggregates() -> AggregateService:
    return cast(AggregateService, current_app.extensions["aggregate_service"])


def _config_service() -> ConfigurationExchangeService:
    return cast(
        ConfigurationExchangeService,
        current_app.extensions["configuration_exchange_service"],
    )


def _all_models() -> list[Any]:
    return [
        model
        for endpoint in _endpoint_service().list_all()
        for model in _model_service().list_for_endpoint(endpoint.id)
    ]


def _error_message(exc: Exception) -> str:
    if isinstance(exc, ApiError):
        details = "; ".join(f"{x.field}: {x.message}" for x in exc.details)
        return f"{exc.message} {details}".strip()
    return str(exc)


def _optional(name: str) -> str | None:
    value = request.form.get(name, "").strip()
    return value or None


def _integer(name: str, default: int | None = None) -> int | None:
    value = _optional(name)
    return default if value is None else int(value)


def _decimal(name: str, default: str | None = None) -> Decimal | None:
    value = _optional(name)
    return (
        Decimal(default)
        if value is None and default is not None
        else Decimal(value)
        if value
        else None
    )


def _headers_from_form() -> list[HeaderInput]:
    result = []
    text = request.form.get("extra_headers", "")
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split("|", 2)
        visibility = parts[1].strip().lower() if len(parts) == 3 else ""
        if visibility not in {"public", "secret", "openbaar", "geheim"}:
            raise ValueError(f"Header line {number}: use Name|public|Value or Name|secret|Value")
        result.append(
            HeaderInput(
                name=parts[0].strip(),
                secret=visibility in {"secret", "geheim"},
                value=parts[2],
            )
        )
    return result


def _endpoint_payload() -> dict[str, Any]:
    return {
        "name": request.form.get("name", ""),
        "api_format": request.form.get("api_format", "openai"),
        "completions_url": request.form.get("completions_url", ""),
        "models_url": _optional("models_url"),
        "api_key": request.form.get("api_key") or None,
        "api_key_header": request.form.get("api_key_header", "Authorization"),
        "api_key_prefix": request.form.get("api_key_prefix", ""),
        "extra_headers": _headers_from_form(),
        "timeout_seconds": _integer("timeout_seconds", 60),
        "max_retries": _integer("max_retries", 2),
        "requests_per_minute": _integer("requests_per_minute"),
        "tokens_per_minute": _integer("tokens_per_minute"),
        "max_concurrency": _integer("max_concurrency"),
        "active": "active" in request.form,
        "trusted_internal_http": "trusted_internal_http" in request.form,
        "security_warning_confirmed": "security_warning_confirmed" in request.form,
    }


def _tags(name: str = "tags") -> list[str]:
    return [value.strip() for value in request.form.get(name, "").split(",") if value.strip()]


def _json_field(name: str, default: str = "{}") -> Any:
    return json.loads(request.form.get(name, default) or default)


def _golden_set_payload() -> dict[str, Any]:
    return {
        "name": request.form.get("name", ""),
        "description": _optional("description"),
        "version": request.form.get("version", "1.0"),
        "tags": _tags(),
        "active": "active" in request.form,
    }


def _bulk_item_payload(*, include_versions: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "item_ids": request.form.getlist("item_ids"),
        "action": request.form.get("action", ""),
        "category": request.form.get("category") or None,
        "tags": _tags("bulk_tags"),
        "weight": request.form.get("bulk_weight") or None,
        "destination_set_id": request.form.get("destination_set_id") or None,
        "start_order": request.form.get("start_order") or None,
    }
    payload["expected_updated_at"] = (
        json.loads(request.form.get("expected_updated_at", "{}")) if include_versions else {}
    )
    return payload


def _item_payload() -> dict[str, Any]:
    method = request.form.get("evaluation_method", "exact_match")
    config = _json_field("evaluator_config")
    expected_answer = request.form.get("expected_answer") or None
    if method in {"exact_match", "llm_judge"}:
        expected_answer = None
    return {
        "case_id": request.form.get("case_id", ""),
        "prompt": request.form.get("prompt", ""),
        "system_prompt": _optional("system_prompt"),
        "expected_answer": expected_answer,
        "evaluation_method": method,
        "evaluator_config": config,
        "category": _optional("category"),
        "tags": _tags(),
        "weight": _decimal("weight", "1"),
        "enabled": "enabled" in request.form,
    }


def _judge_payload() -> dict[str, Any]:
    return {
        "name": request.form.get("name", ""),
        "model_profile_id": request.form.get("model_profile_id", ""),
        "implementation_id": request.form.get("implementation_id", "openai-compatible"),
        "implementation_version": request.form.get("implementation_version", "1.0"),
        "implementation_config": _json_field("implementation_config"),
        "temperature": _decimal("temperature"),
        "reasoning_value": _optional("reasoning_value"),
        "max_output_tokens": _integer("max_output_tokens"),
        "is_global_default": "is_global_default" in request.form,
        "active": "active" in request.form,
    }


def _model_payload() -> dict[str, Any]:
    custom = json.loads(request.form.get("custom_parameters", "{}") or "{}")
    price_names = (
        "input_per_million",
        "output_per_million",
        "cached_input_per_million",
        "reasoning_per_million",
        "fixed_per_request",
        "high_context_input_per_million",
        "high_context_output_per_million",
        "high_context_cached_input_per_million",
        "high_context_reasoning_per_million",
    )
    pricing: dict[str, Any] = {
        name: _decimal(name, "0")
        if name in {"input_per_million", "output_per_million"}
        else _decimal(name)
        for name in price_names
    }
    pricing["context_threshold"] = _integer("context_threshold")
    return {
        "model_id": request.form.get("model_id", ""),
        "display_name": request.form.get("display_name", ""),
        "description": _optional("description"),
        "temperature": _decimal("temperature"),
        "top_p": _decimal("top_p"),
        "max_tokens": _integer("max_tokens"),
        "max_tokens_field_path": request.form.get("max_tokens_field_path", "max_tokens"),
        "reasoning_field_path": _optional("reasoning_field_path"),
        "reasoning_value": _optional("reasoning_value"),
        "send_reasoning_off": "send_reasoning_off" in request.form,
        "custom_parameters": custom,
        "pricing": pricing,
        "active": "active" in request.form,
    }
