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
    bind_operation_actor,
    log_event,
    reset_log_context,
    reset_operation_actor,
)

REQUEST_ID_HEADER = "X-Request-ID"
_LOG = logging.getLogger(__name__)
_ACTION_EVENTS = {
    "discover_models": "model_discovery_completed",
    "discover": "model_discovery_completed",
    "import_discovered_models": "discovered_models_imported",
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


def _safe_route() -> str:
    """Return static route template without user-controlled path values."""
    return request.url_rule.rule if request.url_rule is not None else "<unmatched>"


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
        g.operation_actor_token = bind_operation_actor("api" if _is_api_request() else "local_gui")
        log_event(
            _LOG,
            logging.DEBUG,
            "request_started",
            method=request.method,
            route=_safe_route(),
            interface="api" if _is_api_request() else "gui",
            request_bytes=request.content_length,
        )

    @app.after_request
    def add_request_id(response: Response) -> Response:
        response.headers[REQUEST_ID_HEADER] = _request_id()
        started = getattr(g, "request_started", None)
        duration = None if started is None else round((time.monotonic() - started) * 1000, 3)
        interface = "api" if _is_api_request() else "gui"
        route = _safe_route()
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
        actor_token = getattr(g, "operation_actor_token", None)
        if actor_token is not None:
            reset_operation_actor(actor_token)
            g.operation_actor_token = None

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
            route=_safe_route(),
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
            route=_safe_route(),
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
            route=_safe_route(),
        )
        if not _is_api_request():
            return InternalServerError(original_exception=error)
        safe_error = InternalError()
        return jsonify(error_payload(safe_error, _request_id())), safe_error.status_code
