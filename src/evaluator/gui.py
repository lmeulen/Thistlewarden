"""English server-rendered management GUI for settings, endpoints, and models."""

from __future__ import annotations

import hmac
import json
import logging
import secrets
from decimal import Decimal
from typing import Any, Literal, cast
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
from evaluator.gates import QualityGateService
from evaluator.golden_sets import GoldenSetService, evaluator_config_examples
from evaluator.judges import JudgeProfileService
from evaluator.logging_config import log_event
from evaluator.models import ModelProfileService
from evaluator.run_exports import RunExportService, safe_filename
from evaluator.run_views import RESULT_PAGE_SIZE, ResultQuery, RunViewService
from evaluator.runs import RunQueue
from evaluator.settings import ResolvedSettings, SettingsService

gui = Blueprint("gui", __name__)
_LOG = logging.getLogger(__name__)


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


def _discovery_service() -> ModelDiscoveryService:
    return cast(ModelDiscoveryService, current_app.extensions["model_discovery_service"])


def _golden_service() -> GoldenSetService:
    return cast(GoldenSetService, current_app.extensions["golden_set_service"])


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
        if len(parts) != 3 or parts[1].strip().lower() not in {"openbaar", "geheim"}:
            raise ValueError(f"Header line {number}: use Name|public|Value or Name|secret|Value")
        result.append(
            HeaderInput(
                name=parts[0].strip(), secret=parts[1].strip().lower() == "geheim", value=parts[2]
            )
        )
    return result


def _endpoint_payload() -> dict[str, Any]:
    return {
        "name": request.form.get("name", ""),
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


def _item_payload() -> dict[str, Any]:
    method = request.form.get("evaluation_method", "exact_match")
    config = _json_field("evaluator_config")
    return {
        "case_id": request.form.get("case_id", ""),
        "prompt": request.form.get("prompt", ""),
        "system_prompt": _optional("system_prompt"),
        "expected_answer": request.form.get("expected_answer") or None,
        "evaluation_method": method,
        "evaluator_config": config,
        "category": _optional("category"),
        "tags": _tags(),
        "weight": _decimal("weight", "1"),
        "enabled": "enabled" in request.form,
    }


def _rubric() -> list[dict[str, Any]]:
    result = []
    for line in request.form.get("rubric", "").splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 2)
        if len(parts) != 3:
            raise ValueError("Rubric line requires id|weight|description")
        result.append(
            {"id": parts[0].strip(), "weight": parts[1].strip(), "description": parts[2].strip()}
        )
    return result


def _judge_payload() -> dict[str, Any]:
    return {
        "name": request.form.get("name", ""),
        "model_profile_id": request.form.get("model_profile_id", ""),
        "prompt_template": request.form.get("prompt_template", "judge-v1"),
        "template_version": request.form.get("template_version", "1.0"),
        "threshold": _decimal("threshold", "0.7"),
        "rubric": _rubric(),
        "temperature": _decimal("temperature", "0"),
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


@gui.get("/")
def dashboard() -> str:
    service = current_app.extensions.get("dashboard_service")
    statistics = (
        cast(DashboardService, service).statistics()
        if service is not None
        else DashboardStatistics()
    )
    return render_template("index.html", statistics=statistics)


@gui.get("/instellingen")
def settings_page() -> str:
    resolved: ResolvedSettings = current_app.extensions["settings"]
    return render_template("settings.html", settings=resolved.metadata.values())


@gui.post("/instellingen/<name>")
def update_setting(name: str) -> Response:
    service: SettingsService = current_app.extensions["settings_service"]
    try:
        resolved = service.set_database_value(name, request.form.get("value", ""))
        current_app.extensions["settings"] = resolved
        flash("Setting saved.", "success")
    except (ConfigurationError, KeyError) as exc:
        flash(_error_message(exc), "error")
    return redirect(url_for("gui.settings_page"))


@gui.get("/endpoints")
def endpoints_page() -> str:
    return render_template("endpoints.html", endpoints=_endpoint_service().list_all())


@gui.route("/endpoints/nieuw", methods=["GET", "POST"])
def endpoint_new() -> str | Response:
    if request.method == "POST":
        try:
            record = _endpoint_service().create(_endpoint_payload())
            flash("Endpoint created.", "success")
            return redirect(url_for("gui.endpoint_edit", endpoint_id=record.id))
        except (ApiError, ValueError) as exc:
            flash(_error_message(exc), "error")
    return render_template("endpoint_form.html", endpoint=None)


@gui.route("/endpoints/<uuid:endpoint_id>", methods=["GET", "POST"])
def endpoint_edit(endpoint_id: UUID) -> str | Response:
    service = _endpoint_service()
    if request.method == "POST":
        try:
            service.update(endpoint_id, _endpoint_payload())
            flash("Endpoint saved; leaving secret blank preserves its current value.", "success")
            return redirect(url_for("gui.endpoint_edit", endpoint_id=endpoint_id))
        except (ApiError, ValueError) as exc:
            flash(_error_message(exc), "error")
    return render_template("endpoint_form.html", endpoint=service.get(endpoint_id))


@gui.post("/endpoints/<uuid:endpoint_id>/verwijderen")
def endpoint_delete(endpoint_id: UUID) -> Response:
    try:
        _endpoint_service().delete(endpoint_id)
        flash("Endpoint deleted.", "success")
    except ApiError as exc:
        flash(_error_message(exc), "error")
    return redirect(url_for("gui.endpoints_page"))


@gui.route("/endpoints/<uuid:endpoint_id>/ontdekken", methods=["GET", "POST"])
def discover(endpoint_id: UUID) -> str | Response:
    try:
        candidates = _discovery_service().discover(endpoint_id)
        if request.method == "POST":
            selected = request.form.getlist("model_id")
            records = _discovery_service().import_selected(endpoint_id, {"model_ids": selected})
            flash(f"{len(records)} model profile(s) created.", "success")
            return redirect(url_for("gui.endpoint_edit", endpoint_id=endpoint_id))
        flash("Connection succeeded; models retrieved.", "success")
        return render_template(
            "discovery.html", endpoint=_endpoint_service().get(endpoint_id), candidates=candidates
        )
    except ApiError as exc:
        flash(_error_message(exc), "error")
        return redirect(url_for("gui.endpoint_edit", endpoint_id=endpoint_id))


@gui.route("/endpoints/<uuid:endpoint_id>/modellen/nieuw", methods=["GET", "POST"])
def model_new(endpoint_id: UUID) -> str | Response:
    service = _model_service()
    endpoint = _endpoint_service().get(endpoint_id)
    profiles = service.list_for_endpoint(endpoint_id)
    if request.method == "POST":
        try:
            record = service.create(endpoint_id, _model_payload())
            flash("Model profile created.", "success")
            return redirect(url_for("gui.model_edit", profile_id=record.id))
        except (ApiError, ValueError, json.JSONDecodeError) as exc:
            flash(_error_message(exc), "error")

    source = None
    source_id = request.args.get("copy_from", "").strip()
    if source_id:
        try:
            source = service.get(UUID(source_id))
            if source.endpoint_id != endpoint_id:
                raise ValidationApiError("Copy source does not belong to this endpoint.")
        except (ApiError, ValueError) as exc:
            flash(_error_message(exc), "error")
            source = None

    candidates = []
    if request.args.get("discover") == "1":
        try:
            candidates = _discovery_service().discover(endpoint_id)
            flash(f"Retrieved {len(candidates)} available model(s).", "success")
        except ApiError as exc:
            flash(_error_message(exc), "error")

    selected_model_id = request.args.get("model_id", "").strip()
    if (
        selected_model_id
        and candidates
        and selected_model_id not in {candidate.model_id for candidate in candidates}
    ):
        flash("Selected model is not in the retrieved model list.", "error")
        selected_model_id = ""
    display_name = ""
    if source is not None:
        reserved = {profile.data.display_name.casefold() for profile in profiles}
        base = f"{source.data.display_name} (copy)"
        display_name = base
        suffix = 2
        while display_name.casefold() in reserved:
            display_name = f"{base} {suffix}"
            suffix += 1
    elif selected_model_id:
        display_name = selected_model_id

    return render_template(
        "model_form.html",
        model=None,
        endpoint=endpoint,
        copy_profiles=profiles,
        copy_source=source,
        candidates=candidates,
        selected_model_id=selected_model_id,
        suggested_display_name=display_name,
    )


@gui.route("/modellen/<uuid:profile_id>", methods=["GET", "POST"])
def model_edit(profile_id: UUID) -> str | Response:
    service = _model_service()
    model = service.get(profile_id)
    if request.method == "POST":
        try:
            service.update(profile_id, _model_payload())
            flash("Model profile saved.", "success")
            return redirect(url_for("gui.model_edit", profile_id=profile_id))
        except (ApiError, ValueError, json.JSONDecodeError) as exc:
            flash(_error_message(exc), "error")
    return render_template(
        "model_form.html",
        model=model,
        endpoint=_endpoint_service().get(model.endpoint_id),
        copy_profiles=[],
        copy_source=None,
        candidates=[],
        selected_model_id="",
        suggested_display_name="",
        test_result=None,
        test_error=None,
    )


@gui.post("/modellen/<uuid:profile_id>/testen")
def model_test(profile_id: UUID) -> str:
    model = _model_service().get(profile_id)
    endpoint = _endpoint_service().get(model.endpoint_id)
    result = None
    test_error = None
    try:
        result = _model_tester().test(model)
        if result.successful:
            flash("Model call succeeded.", "success")
        else:
            test_error = {
                "code": result.error_code or "MODEL_TEST_FAILED",
                "message": "Provider stream could not be processed successfully.",
                "request_id": g.request_id,
            }
    except ApiError as exc:
        diagnostic = getattr(exc, "provider_diagnostic", None)
        test_error = {
            "code": exc.code,
            "message": _error_message(exc),
            "request_id": g.request_id,
            "status_code": getattr(diagnostic, "status_code", None),
            "provider_code": getattr(diagnostic, "provider_code", None),
            "error_type": getattr(diagnostic, "error_type", None),
            "parameter": getattr(diagnostic, "parameter", None),
            "provider_message": getattr(diagnostic, "message", None),
            "provider_request_id": getattr(diagnostic, "provider_request_id", None),
            "raw_response": getattr(diagnostic, "raw_response", None),
            "response_truncated": getattr(diagnostic, "response_truncated", False),
        }
    except Exception as exc:
        log_event(
            _LOG,
            logging.ERROR,
            "model_test_failed",
            model_id=model.data.model_id,
            exception_type=type(exc).__name__,
            error_code="INTERNAL_ERROR",
        )
        test_error = {
            "code": "INTERNAL_ERROR",
            "message": "Unexpected error during model test.",
            "request_id": g.request_id,
        }
    if test_error is not None:
        flash("Model call failed. Review test result below.", "error")
    return render_template(
        "model_form.html",
        model=model,
        endpoint=endpoint,
        copy_profiles=[],
        copy_source=None,
        candidates=[],
        selected_model_id="",
        suggested_display_name="",
        test_result=result,
        test_error=test_error,
    )


@gui.get("/golden-sets")
def golden_sets_page() -> str:
    records, _ = _golden_service().list_sets(1, 100)
    return render_template("golden_sets.html", golden_sets=records)


@gui.post("/golden-sets/<uuid:set_id>/dupliceren")
def golden_set_duplicate(set_id: UUID) -> Response:
    try:
        record = _golden_service().duplicate_set(set_id, request.form.get("name", ""))
        flash("Golden set duplicated.", "success")
        return redirect(url_for("gui.golden_set_edit", set_id=record.id))
    except (ApiError, ValueError) as exc:
        flash(_error_message(exc), "error")
        return redirect(url_for("gui.golden_sets_page"))


@gui.route("/golden-sets/nieuw", methods=["GET", "POST"])
def golden_set_new() -> str | Response:
    if request.method == "POST":
        try:
            record = _golden_service().create_set(_golden_set_payload())
            flash("Golden set created.", "success")
            return redirect(url_for("gui.golden_set_edit", set_id=record.id))
        except (ApiError, ValueError, json.JSONDecodeError) as exc:
            flash(_error_message(exc), "error")
    return render_template("golden_set_form.html", golden_set=None, items=[])


@gui.route("/golden-sets/<uuid:set_id>", methods=["GET", "POST"])
def golden_set_edit(set_id: UUID) -> str | Response:
    service = _golden_service()
    if request.method == "POST":
        try:
            service.update_set(set_id, _golden_set_payload())
            flash("Golden set saved.", "success")
            return redirect(url_for("gui.golden_set_edit", set_id=set_id))
        except (ApiError, ValueError) as exc:
            flash(_error_message(exc), "error")
    record = service.get_set(set_id)
    items, _ = service.list_items(
        set_id, page=1, per_page=100, category=None, tag=None, enabled=None
    )
    return render_template(
        "golden_set_form.html",
        golden_set=record,
        items=items,
        gates=_gate_service().defaults(set_id),
        judges=_judge_service().list_all(),
        selected_judge_id=_judge_service().golden_set_override(set_id),
    )


@gui.post("/golden-sets/<uuid:set_id>/judge")
def golden_set_judge_update(set_id: UUID) -> Response:
    raw = request.form.get("judge_profile_id", "")
    try:
        _judge_service().set_golden_set_override(set_id, UUID(raw) if raw else None)
        flash("Judge override saved.", "success")
    except (ApiError, ValueError) as exc:
        flash(_error_message(exc), "error")
    return redirect(url_for("gui.golden_set_edit", set_id=set_id))


@gui.post("/golden-sets/<uuid:set_id>/gates")
def quality_gates_update(set_id: UUID) -> Response:
    names = (
        "min_weighted_score",
        "min_pass_rate",
        "max_error_rate",
        "max_mean_latency_ms",
        "max_p95_latency_ms",
        "max_p95_ttft_ms",
        "min_mean_tps",
        "max_total_cost",
    )
    try:
        _gate_service().set_defaults(set_id, {name: _decimal(name) for name in names})
        flash("Quality gates saved.", "success")
    except (ApiError, ValueError) as exc:
        flash(_error_message(exc), "error")
    return redirect(url_for("gui.golden_set_edit", set_id=set_id))


def _render_golden_item_form(*, item: Any, golden_set: Any) -> str:
    return render_template(
        "golden_item_form.html",
        item=item,
        golden_set=golden_set,
        evaluator_examples=evaluator_config_examples(),
    )


@gui.route("/golden-sets/<uuid:set_id>/items/nieuw", methods=["GET", "POST"])
def golden_item_new(set_id: UUID) -> str | Response:
    if request.method == "POST":
        try:
            item = _golden_service().create_item(set_id, _item_payload())
            flash("Test case created.", "success")
            return redirect(url_for("gui.golden_item_edit", item_id=item.id))
        except (ApiError, ValueError, json.JSONDecodeError) as exc:
            flash(_error_message(exc), "error")
    return _render_golden_item_form(item=None, golden_set=_golden_service().get_set(set_id))


@gui.post("/golden-sets/<uuid:set_id>/items/verify")
def golden_item_verify(set_id: UUID) -> tuple[Response, int] | Response:
    _golden_service().get_set(set_id)
    try:
        result = _evaluation_preview_service().evaluate(
            _item_payload(), request.form.get("test_output", "")
        )
    except (ValidationApiError, json.JSONDecodeError) as exc:
        message = (
            exc.message
            if isinstance(exc, ValidationApiError)
            else "Evaluator configuration is invalid JSON."
        )
        details = (
            [detail.as_dict() for detail in exc.details]
            if isinstance(exc, ValidationApiError)
            else []
        )
        return jsonify({"ok": False, "message": message, "details": details}), 400
    return jsonify({"ok": True, **result.model_dump()})


@gui.route("/golden-items/<uuid:item_id>", methods=["GET", "POST"])
def golden_item_edit(item_id: UUID) -> str | Response:
    item = _golden_service().get_item(item_id)
    if request.method == "POST":
        try:
            _golden_service().update_item(item_id, _item_payload())
            flash("Test case saved.", "success")
            return redirect(url_for("gui.golden_item_edit", item_id=item_id))
        except (ApiError, ValueError, json.JSONDecodeError) as exc:
            flash(_error_message(exc), "error")
    return _render_golden_item_form(
        item=item, golden_set=_golden_service().get_set(item.golden_set_id)
    )


@gui.post("/golden-sets/<uuid:set_id>/items/verwijderen")
def golden_items_delete(set_id: UUID) -> Response:
    try:
        item_ids = [UUID(value) for value in request.form.getlist("item_ids")]
        deleted = _golden_service().delete_items(set_id, item_ids)
        flash(f"{deleted} test case(s) deleted.", "success")
    except (ApiError, ValueError) as exc:
        flash(_error_message(exc), "error")
    return redirect(url_for("gui.golden_set_edit", set_id=set_id))


@gui.post("/golden-items/<uuid:item_id>/verwijderen")
def golden_item_delete(item_id: UUID) -> Response:
    item = _golden_service().get_item(item_id)
    try:
        _golden_service().delete_item(item_id)
        flash("Test case deleted.", "success")
    except ApiError as exc:
        flash(_error_message(exc), "error")
    return redirect(url_for("gui.golden_set_edit", set_id=item.golden_set_id))


@gui.route("/golden-sets/import", methods=["GET", "POST"])
def golden_set_import() -> str | Response:
    preview_data = None
    if request.method == "POST":
        upload = request.files.get("file")
        format_ = request.form.get("format", "json")
        if upload is None or format_ not in {"json", "csv"}:
            flash("Choose a valid JSON or CSV file.", "error")
        else:
            try:
                data, items, record = _golden_service().import_set(
                    upload.read(),
                    cast(Literal["json", "csv"], format_),
                    csv_set_name=request.form.get("name"),
                    preview="commit" not in request.form,
                )
                if record is None:
                    preview_data = {"name": data.name, "version": data.version, "count": len(items)}
                    flash("File is valid. Select it again to complete import.", "success")
                else:
                    flash("Golden set imported.", "success")
                    return redirect(url_for("gui.golden_set_edit", set_id=record.id))
            except ApiError as exc:
                flash(_error_message(exc), "error")
    return render_template("golden_import.html", preview=preview_data)


@gui.get("/golden-sets/<uuid:set_id>/download/<format_>")
def golden_set_download(set_id: UUID, format_: str) -> Response:
    if format_ not in {"json", "csv"}:
        abort(404)
    content = _golden_service().export_set(set_id, cast(Literal["json", "csv"], format_))
    return Response(
        content,
        content_type="application/json" if format_ == "json" else "text/csv",
        headers={"Content-Disposition": f'attachment; filename="golden-set.{format_}"'},
    )


@gui.get("/judges")
def judges_page() -> str:
    return render_template("judges.html", judges=_judge_service().list_all())


@gui.route("/judges/nieuw", methods=["GET", "POST"])
def judge_new() -> str | Response:
    if request.method == "POST":
        try:
            record = _judge_service().create(_judge_payload())
            flash("Judge profile created.", "success")
            return redirect(url_for("gui.judge_edit", profile_id=record.id))
        except (ApiError, ValueError) as exc:
            flash(_error_message(exc), "error")
    return render_template("judge_form.html", judge=None, models=_all_models())


@gui.route("/judges/<uuid:profile_id>", methods=["GET", "POST"])
def judge_edit(profile_id: UUID) -> str | Response:
    service = _judge_service()
    judge = service.get(profile_id)
    if request.method == "POST":
        try:
            service.update(profile_id, _judge_payload())
            flash("Judge profile saved.", "success")
            return redirect(url_for("gui.judge_edit", profile_id=profile_id))
        except (ApiError, ValueError) as exc:
            flash(_error_message(exc), "error")
    return render_template("judge_form.html", judge=judge, models=_all_models())


@gui.get("/runs")
def runs_page() -> str:
    return render_template("runs.html", runs=_run_views().list_runs())


@gui.route("/runs/nieuw", methods=["GET", "POST"])
def run_new() -> str | Response:
    golden_sets, _ = _golden_service().list_sets(1, 100)
    models = [model for model in _all_models() if model.data.active]
    judges = [judge for judge in _judge_service().list_all() if judge.data.active]
    if request.method == "POST":
        gate_names = (
            "min_weighted_score",
            "min_pass_rate",
            "max_error_rate",
            "max_mean_latency_ms",
            "max_p95_latency_ms",
            "max_p95_ttft_ms",
            "min_mean_tps",
            "max_total_cost",
        )
        try:
            payload: dict[str, Any] = {
                "name": request.form.get("name", ""),
                "golden_set_id": request.form.get("golden_set_id", ""),
                "model_profile_ids": request.form.getlist("model_profile_ids"),
                "judge_profile_id": _optional("judge_profile_id"),
                "note": _optional("note"),
            }
            if "override_gates" in request.form:
                payload["quality_gates"] = {
                    name: value for name in gate_names if (value := _optional(name)) is not None
                }
            run_id = _run_queue().create_run(payload)
            flash("Run created and queued.", "success")
            return redirect(url_for("gui.run_detail", run_id=run_id))
        except (ApiError, ValueError) as exc:
            flash(_error_message(exc), "error")
    settings = cast(ResolvedSettings, current_app.extensions["settings"]).values
    return render_template(
        "run_form.html",
        golden_sets=golden_sets,
        models=models,
        judges=judges,
        concurrency=settings.run_concurrency,
        max_profiles=settings.max_model_profiles_per_run,
        set_summaries=_run_views().golden_set_summaries(),
    )


def _cost_score_scatter(aggregates: list[dict[str, Any]]) -> dict[str, Any]:
    plot_left = Decimal(70)
    plot_width = Decimal(660)
    plot_top = Decimal(20)
    plot_height = Decimal(220)
    eligible = [
        item
        for item in aggregates
        if item["known_total_cost"] is not None and item["weighted_score"] is not None
    ]
    plotted_scores = [min(Decimal(1), max(Decimal(0), item["weighted_score"])) for item in eligible]
    lowest_score = min(plotted_scores, default=Decimal(0))
    y_min = max(
        (
            candidate
            for candidate in map(Decimal, ("0", "0.5", "0.8", "0.9"))
            if candidate <= lowest_score
        ),
        default=Decimal(0),
    )
    y_range = Decimal(1) - y_min
    y_ticks = [
        {
            "value": Decimal(1) - y_range * Decimal(index) / Decimal(4),
            "y": plot_top + plot_height * Decimal(index) / Decimal(4),
        }
        for index in range(5)
    ]
    max_cost = max((item["known_total_cost"] for item in eligible), default=Decimal(0))
    points = []
    for item, score in zip(eligible, plotted_scores, strict=True):
        cost = item["known_total_cost"]
        x_ratio = cost / max_cost if max_cost else Decimal(0)
        y_ratio = (Decimal(1) - score) / y_range
        points.append(
            {
                "model": item["model"],
                "cost": cost,
                "score": item["weighted_score"],
                "x": plot_left + x_ratio * plot_width,
                "y": plot_top + y_ratio * plot_height,
            }
        )
    return {
        "points": points,
        "max_cost": max_cost,
        "omitted": len(aggregates) - len(points),
        "y_min": y_min,
        "y_ticks": y_ticks,
    }


@gui.get("/runs/<uuid:run_id>")
def run_detail(run_id: UUID) -> str:
    run = _run_views().run(run_id)
    query_error = None
    try:
        query = ResultQuery.parse(request.args)
    except ApiError as exc:
        query, query_error = ResultQuery(), _error_message(exc)
    models = _run_views().models(run_id)
    aggregates = _aggregates().for_run(run_id)
    names = {model["id"]: model for model in models}
    for aggregate in aggregates:
        aggregate["model"] = names.get(aggregate["model_snapshot_id"], {})
    gate_results = _gate_service().results(run_id)
    gates_by_model: dict[str, list[dict[str, Any]]] = {}
    for gate in gate_results["gates"]:
        model_snapshot_id = gate["model_snapshot_id"]
        if model_snapshot_id is not None:
            gates_by_model.setdefault(model_snapshot_id, []).append(gate)
    for aggregate in aggregates:
        aggregate["gates"] = gates_by_model.get(aggregate["model_snapshot_id"], [])
    result_rows = _run_views().results(run_id, query)
    analysis = _run_views().analysis(run_id)
    has_next = len(result_rows) > RESULT_PAGE_SIZE
    return render_template(
        "run_detail.html",
        run=run,
        models=models,
        aggregates=aggregates,
        cost_score_scatter=_cost_score_scatter(aggregates),
        analysis=analysis,
        results=result_rows[:RESULT_PAGE_SIZE],
        query=query,
        query_error=query_error,
        has_next=has_next,
        gates=gate_results,
    )


@gui.get("/runs/<uuid:run_id>/status")
def run_status(run_id: UUID) -> Response:
    run = _run_views().run(run_id)
    return jsonify(
        {
            key: run[key]
            for key in (
                "id",
                "status",
                "gate_status",
                "total_jobs",
                "done_jobs",
                "progress_percent",
                "terminal",
            )
        }
    )


@gui.get("/runs/<uuid:run_id>/resultaten/<uuid:result_id>")
def run_result_detail(run_id: UUID, result_id: UUID) -> str:
    return render_template(
        "run_result.html",
        run=_run_views().run(run_id),
        result=_run_views().result(run_id, result_id),
    )


@gui.post("/runs/<uuid:run_id>/annuleren")
def run_cancel(run_id: UUID) -> Response:
    if "confirm" not in request.form:
        flash("Explicitly confirm cancellation.", "error")
    else:
        try:
            _run_queue().cancel(run_id)
            flash("Run cancelled.", "success")
        except ApiError as exc:
            flash(_error_message(exc), "error")
    return redirect(url_for("gui.run_detail", run_id=run_id))


@gui.post("/runs/<uuid:run_id>/mislukte-opnieuw")
def run_rerun_failures(run_id: UUID) -> Response:
    try:
        new_id = _run_queue().rerun_failures(run_id, request.form.get("name", ""))
        flash("New run created for failed jobs.", "success")
        return redirect(url_for("gui.run_detail", run_id=new_id))
    except ApiError as exc:
        flash(_error_message(exc), "error")
        return redirect(url_for("gui.run_detail", run_id=run_id))


@gui.post("/runs/<uuid:run_id>/verwijderen")
def run_delete(run_id: UUID) -> Response:
    if "confirm" not in request.form:
        flash("Explicitly confirm permanent deletion.", "error")
        return redirect(url_for("gui.run_detail", run_id=run_id))
    try:
        _run_exports().delete(run_id)
        flash("Run permanently deleted.", "success")
        return redirect(url_for("gui.runs_page"))
    except ApiError as exc:
        flash(_error_message(exc), "error")
        return redirect(url_for("gui.run_detail", run_id=run_id))


@gui.route("/runs/<uuid:run_id>/export", methods=["GET", "POST"])
def run_export(run_id: UUID) -> str | Response:
    run = _run_views().run(run_id)
    if request.method == "GET":
        return render_template("run_export.html", run=run)
    format_ = request.form.get("format", "json")
    if format_ not in {"json", "csv", "html"} or "confirm_sensitive" not in request.form:
        flash("Choose a format and confirm the warning.", "error")
        return render_template("run_export.html", run=run)
    service = _run_exports()
    exporters = {
        "json": service.export_json,
        "csv": service.export_csv,
        "html": service.export_html,
    }
    mimetypes = {"json": "application/json", "csv": "text/csv", "html": "text/html"}
    content = exporters[format_](run_id)
    return Response(
        content,
        content_type=mimetypes[format_],
        headers={
            "Content-Disposition": f'attachment; filename="{safe_filename(run["name"])}.{format_}"'
        },
    )


@gui.get("/configuratie")
def configuration_page() -> str:
    return render_template("configuration.html", plan=None, preview_payload=None)


@gui.get("/configuratie/export")
def configuration_download() -> Response:
    content = _config_service().export().model_dump_json(indent=2).encode()
    return Response(
        content,
        content_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="evaluator-config-1.0.json"'},
    )


@gui.post("/configuratie/import")
def configuration_import() -> str | Response:
    upload = request.files.get("file")
    preview_payload = request.form.get("preview_payload")
    mode = request.form.get("mode", "merge")
    if (upload is None and not preview_payload) or mode not in {"merge", "replace"}:
        flash("Choose a configuration file and import mode.", "error")
        return render_template("configuration.html", plan=None, preview_payload=None)
    try:
        payload = json.loads(preview_payload) if preview_payload else json.load(cast(Any, upload))
        commit = "commit" in request.form
        if commit and mode == "replace" and "confirm_replace" not in request.form:
            raise ValueError("Explicitly confirm that existing configuration will be archived.")
        plan: ImportPlan = _config_service().import_bundle(
            payload, cast(Literal["merge", "replace"], mode), preview=not commit
        )
        if commit:
            flash(
                "Configuration imported atomically. Add missing secrets through endpoints.",
                "success",
            )
            return redirect(url_for("gui.configuration_page"))
        return render_template(
            "configuration.html",
            plan=plan,
            preview_payload=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
    except (ApiError, ValueError, json.JSONDecodeError) as exc:
        flash(_error_message(exc), "error")
        return render_template("configuration.html", plan=None, preview_payload=None)


@gui.post("/modellen/<uuid:profile_id>/verwijderen")
def model_delete(profile_id: UUID) -> Response:
    try:
        _model_service().delete(profile_id)
        flash("Model profile deleted.", "success")
    except ApiError as exc:
        flash(_error_message(exc), "error")
    return redirect(url_for("gui.endpoints_page"))
