"""Request correlation, timing, and uniform Flask error handling."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from uuid import uuid4

from flask import Flask, Response, g, jsonify, request
from werkzeug.exceptions import HTTPException, InternalServerError

from evaluator.api.errors import ApiError, HttpApiError, InternalError, error_payload
from evaluator.logging_config import (
    bind_log_context,
    log_event,
    reset_log_context,
)

REQUEST_ID_HEADER = "X-Request-ID"
_LOG = logging.getLogger(__name__)
_ACTION_EVENTS = {
    "update_setting": "setting_updated",
    "create_endpoint": "endpoint_created",
    "update_endpoint": "endpoint_updated",
    "delete_endpoint": "endpoint_deleted",
    "discover_models": "model_discovery_completed",
    "discover": "model_discovery_completed",
    "import_discovered_models": "discovered_models_imported",
    "create_model_profile": "model_profile_created",
    "model_new": "model_profile_created",
    "update_model_profile": "model_profile_updated",
    "model_edit": "model_profile_updated",
    "delete_model_profile": "model_profile_deleted",
    "model_delete": "model_profile_deleted",
    "create_judge_profile": "judge_profile_created",
    "judge_new": "judge_profile_created",
    "update_judge_profile": "judge_profile_updated",
    "judge_edit": "judge_profile_updated",
    "delete_judge_profile": "judge_profile_deleted",
    "create_golden_set": "golden_set_created",
    "golden_set_new": "golden_set_created",
    "update_golden_set": "golden_set_updated",
    "golden_set_edit": "golden_set_updated",
    "delete_golden_set": "golden_set_deleted",
    "import_golden_set": "golden_set_imported",
    "golden_set_import": "golden_set_imported",
    "export_golden_set": "golden_set_exported",
    "golden_set_download": "golden_set_exported",
    "create_golden_set_item": "golden_item_created",
    "golden_item_new": "golden_item_created",
    "update_golden_set_item": "golden_item_updated",
    "golden_item_edit": "golden_item_updated",
    "delete_golden_set_item": "golden_item_deleted",
    "golden_item_delete": "golden_item_deleted",
    "golden_set_judge_update": "golden_set_judge_changed",
    "set_quality_gate_defaults": "quality_gates_updated",
    "quality_gates_update": "quality_gates_updated",
    "export_configuration": "configuration_exported",
    "configuration_download": "configuration_exported",
    "import_configuration": "configuration_imported",
    "configuration_import": "configuration_imported",
    "create_run": "run_created",
    "run_new": "run_created",
    "cancel_run": "run_cancel_requested",
    "run_cancel": "run_cancel_requested",
    "rerun_failures": "run_failures_rerun",
    "run_rerun_failures": "run_failures_rerun",
    "delete_run": "run_deleted",
    "run_delete": "run_deleted",
    "export_run": "run_exported",
    "run_export": "run_exported",
    "cleanup_runs": "retention_cleanup_completed",
}


def _endpoint_name() -> str:
    return (request.endpoint or "unknown").rsplit(".", 1)[-1]


def _request_id() -> str:
    value = getattr(g, "request_id", None)
    if isinstance(value, str):
        return value
    value = str(uuid4())
    g.request_id = value
    return value


def _is_api_request() -> bool:
    return request.path == "/api/v1" or request.path.startswith("/api/v1/")


def register_api_handlers(app: Flask, *, secrets: Iterable[str] = ()) -> None:
    """Install app-wide request IDs, safe timing logs, and API error contracts."""

    secret_values = tuple(secrets)

    @app.before_request
    def assign_request_id() -> None:
        g.request_id = str(uuid4())
        g.request_started = time.monotonic()
        context = {"request_id": g.request_id}
        if request.view_args and "run_id" in request.view_args:
            context["run_id"] = str(request.view_args["run_id"])
        g.log_context_tokens = bind_log_context(**context)
        log_event(
            _LOG,
            logging.DEBUG,
            "request_started",
            method=request.method,
            path=request.path,
            interface="api" if _is_api_request() else "gui",
            request_bytes=request.content_length,
        )

    @app.after_request
    def add_request_id(response: Response) -> Response:
        response.headers[REQUEST_ID_HEADER] = _request_id()
        started = getattr(g, "request_started", None)
        duration = None if started is None else round((time.monotonic() - started) * 1000, 3)
        interface = "api" if _is_api_request() else "gui"
        route = request.url_rule.rule if request.url_rule is not None else request.path
        log_event(
            _LOG,
            logging.DEBUG,
            "request_completed",
            method=request.method,
            route=route,
            interface=interface,
            status_code=response.status_code,
            duration_ms=duration,
        )
        action_event = _ACTION_EVENTS.get(_endpoint_name())
        if response.status_code < 400 and action_event is not None:
            log_event(
                _LOG,
                logging.INFO,
                action_event,
                action=_endpoint_name(),
                actor="api" if interface == "api" else "local_gui",
                method=request.method,
                route=route,
                status_code=response.status_code,
            )
        return response

    @app.teardown_request
    def clear_request_context(error: BaseException | None) -> None:
        tokens = getattr(g, "log_context_tokens", None)
        if tokens:
            reset_log_context(tokens)
            g.log_context_tokens = None

    @app.errorhandler(ApiError)
    def handle_api_error(error: ApiError) -> tuple[Response, int] | InternalServerError:
        if not _is_api_request():
            return InternalServerError(original_exception=error)
        log_event(
            _LOG,
            logging.WARNING if error.status_code < 500 else logging.ERROR,
            "request_rejected",
            error_code=error.code,
            status_code=error.status_code,
            method=request.method,
            path=request.path,
        )
        return jsonify(
            error_payload(error, _request_id(), secrets=secret_values)
        ), error.status_code

    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException) -> tuple[Response, int] | HTTPException:
        if not _is_api_request():
            return error
        code = "NOT_FOUND" if error.code == 404 else "HTTP_ERROR"
        message = "API route not found." if error.code == 404 else "HTTP request is invalid."
        api_error = HttpApiError(code, error.code or 500, message)
        log_event(
            _LOG,
            logging.WARNING,
            "http_request_rejected",
            error_code=code,
            status_code=api_error.status_code,
            method=request.method,
            path=request.path,
        )
        return jsonify(error_payload(api_error, _request_id())), api_error.status_code

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception) -> tuple[Response, int] | InternalServerError:
        log_event(
            _LOG,
            logging.ERROR,
            "unhandled_request_exception",
            exception_type=type(error).__name__,
            method=request.method,
            path=request.path,
        )
        if not _is_api_request():
            return InternalServerError(original_exception=error)
        safe_error = InternalError()
        return jsonify(error_payload(safe_error, _request_id())), safe_error.status_code
