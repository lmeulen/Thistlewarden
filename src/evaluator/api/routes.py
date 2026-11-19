"""Version 1 API routes."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Literal, cast
from urllib.parse import quote
from uuid import UUID

from flask import Blueprint, Response, current_app, jsonify, request, send_file
from werkzeug.wsgi import ClosingIterator

from evaluator.aggregates import AggregateService, aggregate_to_dict
from evaluator.api.errors import ValidationApiError
from evaluator.api.openapi import build_openapi_document
from evaluator.config_io import ConfigurationExchangeService
from evaluator.costs import CostService, calculation_to_dict
from evaluator.discovery import ModelDiscoveryService
from evaluator.endpoints import EndpointService, endpoint_to_dict
from evaluator.gates import QualityGateService
from evaluator.golden_set_service import (
    GoldenSetService,
    golden_item_to_dict,
    golden_set_to_dict,
)
from evaluator.judging.definition_service import (
    JudgeDefinitionService,
    judge_definition_to_dict,
    judge_definition_version_to_dict,
)
from evaluator.judging.profiles import JudgeProfileService, judge_profile_to_dict
from evaluator.models import ModelProfileService, profile_to_dict
from evaluator.run_comparisons import ComparisonFilter, RunComparisonService
from evaluator.run_exports import ExportFormat, RunExportService, safe_filename
from evaluator.runs import RunQueue
from evaluator.settings import SettingsService

api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")


@api_v1.get("/health")
def health() -> Response:
    """Return API process liveness without querying SQLite."""

    return jsonify({"status": "ok", "service": "thistlewarden", "version": "1"})


@api_v1.get("/configuration/export")
def export_configuration() -> Response:
    return jsonify(_configuration_exchange_service().export().model_dump(mode="json"))


@api_v1.post("/configuration/import")
def import_configuration() -> Response:
    mode = request.args.get("mode", "merge")
    if mode not in {"merge", "replace"}:
        raise ValidationApiError("Import mode must be merge or replace.")
    preview_text = request.args.get("preview", "true").lower()
    if preview_text not in {"true", "false"}:
        raise ValidationApiError("preview must be true or false.")
    preview = preview_text == "true"
    plan = _configuration_exchange_service().import_bundle(
        _json_object(), cast(Literal["merge", "replace"], mode), preview=preview
    )
    return jsonify(plan.model_dump(mode="json"))


@api_v1.get("/settings")
def get_settings() -> Response:
    """Return resolved settings while suppressing all secret values."""

    service = current_app.extensions["settings_service"]
    if not isinstance(service, SettingsService):
        raise RuntimeError("Settings service is unavailable")
    active = service.active
    desired = service.desired
    items = []
    for name, desired_item in desired.metadata.items():
        active_item = active.get(name)
        active_value = active_item.public_value()
        desired_value = desired_item.public_value()
        if isinstance(active_value, Path):
            active_value = str(active_value)
        if isinstance(desired_value, Path):
            desired_value = str(desired_value)
        items.append(
            {
                "name": name,
                "value": active_value,
                "active_value": active_value,
                "desired_value": desired_value,
                "source": desired_item.source.value,
                "read_only": desired_item.read_only,
                "secret": desired_item.definition.secret,
                "lifecycle": desired_item.definition.lifecycle.value,
                "pending_restart": active_item.value != desired_item.value,
            }
        )
    return jsonify({"settings": items})


def _endpoint_service() -> EndpointService:
    service = current_app.extensions.get("endpoint_service")
    if not isinstance(service, EndpointService):
        raise RuntimeError("Endpoint service is unavailable")
    return service


def _golden_set_service() -> GoldenSetService:
    service = current_app.extensions.get("golden_set_service")
    if not isinstance(service, GoldenSetService):
        raise RuntimeError("Golden set service is unavailable")
    return service


def _model_discovery_service() -> ModelDiscoveryService:
    service = current_app.extensions.get("model_discovery_service")
    if not isinstance(service, ModelDiscoveryService):
        raise RuntimeError("Model discovery service is unavailable")
    return service


def _cost_service() -> CostService:
    service = current_app.extensions.get("cost_service")
    if not isinstance(service, CostService):
        raise RuntimeError("Cost service is unavailable")
    return service


def _judge_definition_service() -> JudgeDefinitionService:
    service = current_app.extensions.get("judge_definition_service")
    if not isinstance(service, JudgeDefinitionService):
        raise RuntimeError("Judge definition service is unavailable")
    return service


def _judge_profile_service() -> JudgeProfileService:
    service = current_app.extensions.get("judge_profile_service")
    if not isinstance(service, JudgeProfileService):
        raise RuntimeError("Judge profile service is unavailable")
    return service


def _model_profile_service() -> ModelProfileService:
    service = current_app.extensions.get("model_profile_service")
    if not isinstance(service, ModelProfileService):
        raise RuntimeError("Model profile service is unavailable")
    return service


def _run_comparison_service() -> RunComparisonService:
    service = current_app.extensions.get("run_comparison_service")
    if not isinstance(service, RunComparisonService):
        raise RuntimeError("Run comparison service is unavailable")
    return service


def _run_export_service() -> RunExportService:
    service = current_app.extensions.get("run_export_service")
    if not isinstance(service, RunExportService):
        raise RuntimeError("Run export service is unavailable")
    return service


def _configuration_exchange_service() -> ConfigurationExchangeService:
    service = current_app.extensions.get("configuration_exchange_service")
    if not isinstance(service, ConfigurationExchangeService):
        raise RuntimeError("Configuration exchange service is unavailable")
    return service


def _quality_gate_service() -> QualityGateService:
    service = current_app.extensions.get("quality_gate_service")
    if not isinstance(service, QualityGateService):
        raise RuntimeError("Quality gate service is unavailable")
    return service


def _aggregate_service() -> AggregateService:
    service = current_app.extensions.get("aggregate_service")
    if not isinstance(service, AggregateService):
        raise RuntimeError("Aggregate service is unavailable")
    return service


def _run_queue() -> RunQueue:
    queue = current_app.extensions.get("run_queue")
    if not isinstance(queue, RunQueue):
        raise RuntimeError("Run queue is unavailable")
    return queue


def _json_object() -> dict[str, object]:
    payload = request.get_json(silent=False)
    if not isinstance(payload, dict):
        raise ValidationApiError("JSON object is required.")
    return payload


@api_v1.get("/endpoints")
def list_endpoints() -> Response:
    """List provider endpoints with masked secret values."""

    return jsonify(
        {"endpoints": [endpoint_to_dict(item) for item in _endpoint_service().list_all()]}
    )


@api_v1.post("/endpoints")
def create_endpoint() -> tuple[Response, int]:
    """Create a validated provider endpoint and encrypted secrets."""

    record = _endpoint_service().create(_json_object())
    return jsonify({"endpoint": endpoint_to_dict(record)}), 201


@api_v1.get("/endpoints/<uuid:endpoint_id>")
def get_endpoint(endpoint_id: UUID) -> Response:
    """Get one provider endpoint with masked secret values."""

    return jsonify({"endpoint": endpoint_to_dict(_endpoint_service().get(endpoint_id))})


@api_v1.patch("/endpoints/<uuid:endpoint_id>")
def update_endpoint(endpoint_id: UUID) -> Response:
    """Patch endpoint configuration while preserving omitted secrets."""

    record = _endpoint_service().update(endpoint_id, _json_object())
    return jsonify({"endpoint": endpoint_to_dict(record)})


@api_v1.delete("/endpoints/<uuid:endpoint_id>")
def delete_endpoint(endpoint_id: UUID) -> tuple[str, int]:
    """Delete endpoint unless persistent historical references block it."""

    _endpoint_service().delete(endpoint_id)
    return "", 204


@api_v1.post("/endpoints/<uuid:endpoint_id>/model-discovery")
def discover_models(endpoint_id: UUID) -> Response:
    candidates = _model_discovery_service().discover(endpoint_id)
    return jsonify(
        {
            "models": [
                {"model_id": item.model_id, "suggested_display_name": item.suggested_display_name}
                for item in candidates
            ]
        }
    )


@api_v1.post("/endpoints/<uuid:endpoint_id>/model-discovery/import")
def import_discovered_models(endpoint_id: UUID) -> tuple[Response, int]:
    records = _model_discovery_service().import_selected(endpoint_id, _json_object())
    return jsonify({"models": [profile_to_dict(record) for record in records]}), 201


@api_v1.get("/endpoints/<uuid:endpoint_id>/models")
def list_model_profiles(endpoint_id: UUID) -> Response:
    """List model test profiles for one endpoint."""

    records = _model_profile_service().list_for_endpoint(endpoint_id)
    return jsonify({"models": [profile_to_dict(record) for record in records]})


@api_v1.post("/endpoints/<uuid:endpoint_id>/models")
def create_model_profile(endpoint_id: UUID) -> tuple[Response, int]:
    """Create model test profile under endpoint."""

    record = _model_profile_service().create(endpoint_id, _json_object())
    return jsonify({"model": profile_to_dict(record)}), 201


@api_v1.get("/models/<uuid:profile_id>")
def get_model_profile(profile_id: UUID) -> Response:
    """Get model test profile."""

    return jsonify({"model": profile_to_dict(_model_profile_service().get(profile_id))})


@api_v1.patch("/models/<uuid:profile_id>")
def update_model_profile(profile_id: UUID) -> Response:
    """Patch model inference and pricing configuration."""

    record = _model_profile_service().update(profile_id, _json_object())
    return jsonify({"model": profile_to_dict(record)})


@api_v1.delete("/models/<uuid:profile_id>")
def delete_model_profile(profile_id: UUID) -> tuple[str, int]:
    """Delete model profile unless historical references block it."""

    _model_profile_service().delete(profile_id)
    return "", 204


@api_v1.get("/judges")
def list_judges() -> Response:
    judges = [judge_definition_to_dict(x) for x in _judge_definition_service().list_all()]
    return jsonify({"judges": judges})


@api_v1.post("/judges")
def create_judge() -> Response | tuple[Response, int]:
    record = _judge_definition_service().create(_json_object())
    return jsonify({"judge": judge_definition_to_dict(record)}), 201


@api_v1.get("/judges/<definition_id>")
def get_judge(definition_id: str) -> Response:
    judge = _judge_definition_service().get(definition_id)
    return jsonify({"judge": judge_definition_to_dict(judge)})


@api_v1.patch("/judges/<definition_id>/draft")
def update_judge_draft(definition_id: str) -> Response:
    record = _judge_definition_service().update_draft(definition_id, _json_object())
    return jsonify({"judge": judge_definition_to_dict(record)})


@api_v1.post("/judges/<definition_id>/publish")
def publish_judge(definition_id: str) -> Response | tuple[Response, int]:
    version = request.args.get("version", "1.0")
    result = _judge_definition_service().publish(definition_id, version)
    return jsonify({"judge_version": judge_definition_version_to_dict(result)}), 201


@api_v1.get("/judges/<definition_id>/versions/<version>")
def get_judge_version(definition_id: str, version: str) -> Response:
    result = _judge_definition_service().get_version(definition_id, version)
    return jsonify({"judge_version": judge_definition_version_to_dict(result)})


@api_v1.get("/judge-profiles")
def list_judge_profiles() -> Response:
    return jsonify(
        {"judge_profiles": [judge_profile_to_dict(x) for x in _judge_profile_service().list_all()]}
    )


@api_v1.post("/judge-profiles")
def create_judge_profile() -> tuple[Response, int]:
    return jsonify(
        {"judge_profile": judge_profile_to_dict(_judge_profile_service().create(_json_object()))}
    ), 201


@api_v1.get("/judge-profiles/<uuid:profile_id>")
def get_judge_profile(profile_id: UUID) -> Response:
    return jsonify(
        {"judge_profile": judge_profile_to_dict(_judge_profile_service().get(profile_id))}
    )


@api_v1.patch("/judge-profiles/<uuid:profile_id>")
def update_judge_profile(profile_id: UUID) -> Response:
    return jsonify(
        {
            "judge_profile": judge_profile_to_dict(
                _judge_profile_service().update(profile_id, _json_object())
            )
        }
    )


@api_v1.delete("/judge-profiles/<uuid:profile_id>")
def delete_judge_profile(profile_id: UUID) -> tuple[str, int]:
    _judge_profile_service().delete(profile_id)
    return "", 204


@api_v1.post("/models/<uuid:profile_id>/cost-calculations")
def create_cost_calculation(profile_id: UUID) -> tuple[Response, int]:
    """Calculate and persist exact EUR cost from provider usage."""

    result = _cost_service().calculate_for_profile(profile_id, _json_object())
    return jsonify({"cost_calculation": calculation_to_dict(result)}), 201


@api_v1.get("/cost-calculations/<uuid:calculation_id>")
def get_cost_calculation(calculation_id: UUID) -> Response:
    """Read immutable auditable cost calculation."""

    result = _cost_service().get(calculation_id)
    return jsonify({"cost_calculation": calculation_to_dict(result)})


def _positive_query(name: str, default: int) -> int:
    try:
        value = int(request.args.get(name, default))
    except ValueError as exc:
        raise ValidationApiError(f"Query parameter {name} must be an integer.") from exc
    return value


def _optional_bool_query(name: str) -> bool | None:
    value = request.args.get(name)
    if value is None:
        return None
    if value.lower() not in {"true", "false"}:
        raise ValidationApiError(f"Query parameter {name} must be true or false.")
    return value.lower() == "true"


@api_v1.get("/golden-sets")
def list_golden_sets() -> Response:
    page, per_page = _positive_query("page", 1), _positive_query("per_page", 20)
    records, total = _golden_set_service().list_sets(page, per_page)
    return jsonify(
        {
            "golden_sets": [golden_set_to_dict(item) for item in records],
            "pagination": {"page": page, "per_page": per_page, "total": total},
        }
    )


@api_v1.post("/golden-sets/import")
def import_golden_set() -> tuple[Response, int] | Response:
    format_ = request.args.get("format", "json").lower()
    if format_ not in {"json", "csv"}:
        raise ValidationApiError("Import format must be json or csv.")
    preview_text = request.args.get("preview", "false").lower()
    if preview_text not in {"true", "false"}:
        raise ValidationApiError("Query parameter preview must be true or false.")
    upload = request.files.get("file")
    if upload is None:
        raise ValidationApiError("Multipart file field is required.")
    settings = current_app.extensions["settings"]
    limit = settings.values.max_import_file_bytes
    content = upload.stream.read(limit + 1)
    data, items, record = _golden_set_service().import_set(
        content,
        cast(Literal["json", "csv"], format_),
        csv_set_name=request.form.get("name"),
        preview=preview_text == "true",
    )
    if record is None:
        return jsonify(
            {
                "valid": True,
                "preview": {"name": data.name, "version": data.version, "item_count": len(items)},
            }
        )
    return jsonify({"golden_set": golden_set_to_dict(record), "item_count": len(items)}), 201


@api_v1.post("/golden-sets")
def create_golden_set() -> tuple[Response, int]:
    record = _golden_set_service().create_set(_json_object())
    return jsonify({"golden_set": golden_set_to_dict(record)}), 201


@api_v1.get("/golden-sets/<uuid:set_id>")
def get_golden_set(set_id: UUID) -> Response:
    return jsonify({"golden_set": golden_set_to_dict(_golden_set_service().get_set(set_id))})


@api_v1.get("/golden-sets/<uuid:set_id>/export")
def export_golden_set(set_id: UUID) -> Response:
    format_ = request.args.get("format", "json").lower()
    if format_ not in {"json", "csv"}:
        raise ValidationApiError("Export format must be json or csv.")
    record = _golden_set_service().get_set(set_id)
    content = _golden_set_service().export_set(set_id, cast(Literal["json", "csv"], format_))
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", record.data.name).strip("-.") or "golden-set"
    filename = f"{safe_name}-{set_id}.{format_}"
    response = Response(
        content,
        mimetype="application/json" if format_ == "json" else "text/csv",
    )
    response.headers["Content-Disposition"] = (
        f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
    )
    return response


@api_v1.patch("/golden-sets/<uuid:set_id>")
def update_golden_set(set_id: UUID) -> Response:
    record = _golden_set_service().update_set(set_id, _json_object())
    return jsonify({"golden_set": golden_set_to_dict(record)})


@api_v1.delete("/golden-sets/<uuid:set_id>")
def delete_golden_set(set_id: UUID) -> tuple[str, int]:
    _golden_set_service().delete_set(set_id)
    return "", 204


@api_v1.get("/golden-sets/<uuid:set_id>/items")
def list_golden_set_items(set_id: UUID) -> Response:
    page, per_page = _positive_query("page", 1), _positive_query("per_page", 20)
    records, total = _golden_set_service().list_items(
        set_id,
        page=page,
        per_page=per_page,
        category=request.args.get("category"),
        tag=request.args.get("tag"),
        enabled=_optional_bool_query("enabled"),
    )
    return jsonify(
        {
            "items": [golden_item_to_dict(item) for item in records],
            "pagination": {"page": page, "per_page": per_page, "total": total},
        }
    )


@api_v1.get("/golden-sets/<uuid:set_id>/quality-gates")
def get_quality_gate_defaults(set_id: UUID) -> Response:
    return jsonify(
        {
            "quality_gates": _quality_gate_service()
            .defaults(set_id)
            .model_dump(mode="json", exclude_none=True)
        }
    )


@api_v1.put("/golden-sets/<uuid:set_id>/quality-gates")
def set_quality_gate_defaults(set_id: UUID) -> Response:
    gates = _quality_gate_service().set_defaults(set_id, _json_object())
    return jsonify({"quality_gates": gates.model_dump(mode="json", exclude_none=True)})


@api_v1.post("/golden-sets/<uuid:set_id>/items")
def create_golden_set_item(set_id: UUID) -> tuple[Response, int]:
    record = _golden_set_service().create_item(set_id, _json_object())
    return jsonify({"item": golden_item_to_dict(record)}), 201


@api_v1.get("/golden-set-items/<uuid:item_id>")
def get_golden_set_item(item_id: UUID) -> Response:
    return jsonify({"item": golden_item_to_dict(_golden_set_service().get_item(item_id))})


@api_v1.patch("/golden-set-items/<uuid:item_id>")
def update_golden_set_item(item_id: UUID) -> Response:
    record = _golden_set_service().update_item(item_id, _json_object())
    return jsonify({"item": golden_item_to_dict(record)})


@api_v1.delete("/golden-set-items/<uuid:item_id>")
def delete_golden_set_item(item_id: UUID) -> tuple[str, int]:
    _golden_set_service().delete_item(item_id)
    return "", 204


@api_v1.post("/runs")
def create_run() -> tuple[Response, int]:
    run_id = _run_queue().create_run(_json_object())
    return jsonify({"run": _run_queue().get_run(run_id)}), 201


@api_v1.get("/runs/<uuid:run_id>")
def get_run(run_id: UUID) -> Response:
    return jsonify({"run": _run_queue().get_run(run_id)})


@api_v1.get("/runs/<uuid:run_id>/aggregates")
def get_run_aggregates(run_id: UUID) -> Response:
    return jsonify({"models": aggregate_to_dict(_aggregate_service().for_run(run_id))})


@api_v1.get("/runs/<uuid:run_id>/comparison-candidates")
def get_comparison_candidates(run_id: UUID) -> Response:
    return jsonify({"runs": _run_comparison_service().candidates(run_id)})


@api_v1.get("/runs/<uuid:run_id>/compare/<uuid:baseline_id>")
def compare_runs(run_id: UUID, baseline_id: UUID) -> Response:
    filter_value = request.args.get("filter", "all")
    return jsonify(
        _run_comparison_service().compare(
            run_id, baseline_id, filter_=cast(ComparisonFilter, filter_value)
        )
    )


@api_v1.get("/runs/<uuid:run_id>/compare/<uuid:baseline_id>/export")
def export_run_comparison(run_id: UUID, baseline_id: UUID) -> Response:
    service = _run_comparison_service()
    report = service.compare(run_id, baseline_id)
    name = safe_filename(f"{report['candidate']['name']}-vs-{report['baseline']['name']}")
    return Response(
        service.export_json(run_id, baseline_id),
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}.comparison.json"'},
    )


@api_v1.get("/runs/<uuid:run_id>/export")
def export_run(run_id: UUID) -> Response:
    format_ = request.args.get("format", "json")
    if format_ not in {"json", "csv", "html"}:
        raise ValidationApiError("Export format must be json, csv, or html.")
    artifact = _run_export_service().create_export(run_id, cast(ExportFormat, format_))
    try:
        response = send_file(
            artifact.path,
            mimetype=artifact.mimetype,
            as_attachment=True,
            download_name=artifact.download_name,
        )
    except BaseException:
        artifact.cleanup()
        raise
    response.response = ClosingIterator(
        cast(Iterable[bytes], response.response), [artifact.cleanup]
    )
    return response


@api_v1.delete("/runs/<uuid:run_id>")
def delete_run(run_id: UUID) -> Response:
    _run_export_service().delete(run_id)
    return Response(status=204)


@api_v1.post("/runs/retention-cleanup")
def cleanup_runs() -> Response:
    payload = _json_object()
    days = payload.get("retention_days")
    if not isinstance(days, int) or isinstance(days, bool):
        raise ValidationApiError("retention_days must be an integer.")
    return jsonify({"deleted": _run_export_service().cleanup(days)})


@api_v1.get("/runs/<uuid:run_id>/quality-gates")
def get_run_quality_gates(run_id: UUID) -> Response:
    return jsonify(_quality_gate_service().results(run_id))


@api_v1.post("/runs/<uuid:run_id>/cancel")
def cancel_run(run_id: UUID) -> Response:
    _run_queue().cancel(run_id)
    return jsonify({"run": _run_queue().get_run(run_id)})


@api_v1.post("/runs/<uuid:run_id>/rerun-failures")
def rerun_failures(run_id: UUID) -> tuple[Response, int]:
    payload = _json_object()
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValidationApiError("Name is required.")
    new_id = _run_queue().rerun_failures(run_id, name)
    return jsonify({"run": _run_queue().get_run(new_id)}), 201


@api_v1.get("/openapi.json")
def openapi_document() -> Response:
    """Return current OpenAPI 3.1 document."""

    return jsonify(build_openapi_document())
