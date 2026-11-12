"""Flask application factory."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from flask import Flask, Response, jsonify

from evaluator.aggregates import AggregateService
from evaluator.api import api_v1
from evaluator.api.handlers import register_api_handlers
from evaluator.config import BootstrapSettings, load_bootstrap_settings
from evaluator.config_io import ConfigurationExchangeService
from evaluator.costs import CostService, SQLiteCostRepository
from evaluator.dashboard import DashboardService
from evaluator.database import SQLiteConnectionFactory
from evaluator.discovery import ModelDiscoveryClient, ModelDiscoveryService
from evaluator.endpoints import EndpointService, SQLiteEndpointRepository
from evaluator.evaluation_preview import EvaluationPreviewService
from evaluator.gates import QualityGateService
from evaluator.golden_sets import GoldenSetLimits, GoldenSetService, SQLiteGoldenSetRepository
from evaluator.gui import csrf_token, gui
from evaluator.judges import JudgeProfileService, SQLiteJudgeProfileRepository
from evaluator.logging_config import configure_logging
from evaluator.migrations import MigrationManager
from evaluator.models import ModelProfileService, SQLiteModelProfileRepository
from evaluator.provider import OpenAIProviderClient, ProviderHeaderResolver
from evaluator.rate_limits import CentralEndpointLimiter, ProviderCallExecutor, RetryPolicy
from evaluator.repositories import SQLiteSettingsRepository
from evaluator.run_exports import RunExportService
from evaluator.run_views import (
    RunViewService,
    chart_percent,
    display_metric,
    display_money,
    status_label,
)
from evaluator.runs import RunQueue
from evaluator.secrets import EncryptionKeyring, SecretEncryptionService, SQLiteSecretRepository
from evaluator.settings import (
    SETTINGS_REGISTRY,
    InMemorySettingsRepository,
    SettingsRepository,
    SettingsResolver,
    SettingsService,
)
from evaluator.streaming import OpenAIStreamingClient
from evaluator.web_security import install_web_security
from evaluator.worker_runtime import (
    JudgeCaller,
    ModelProfileTester,
    StreamingTargetCaller,
    WorkerRuntime,
)
from evaluator.workers import WorkerPipeline


def create_app(
    *,
    settings: BootstrapSettings | None = None,
    env_file: str | Path | None = ".env",
    test_config: dict[str, object] | None = None,
    settings_repository: SettingsRepository | None = None,
    settings_environment: Mapping[str, str] | None = None,
    initialize_database: bool | None = None,
) -> Flask:
    """Create the web application without touching runtime data paths."""

    loaded_from_environment = settings is None
    active_settings = settings or load_bootstrap_settings(env_file)
    environment = dict(
        settings_environment
        if settings_environment is not None
        else os.environ
        if loaded_from_environment
        else {}
    )
    if not loaded_from_environment:
        for name in BootstrapSettings.model_fields:
            value = getattr(active_settings, name)
            environment[SETTINGS_REGISTRY[name].env_name] = (
                str(value).lower() if isinstance(value, bool) else str(value)
            )
    should_initialize_database = (
        loaded_from_environment and settings_environment is None
        if initialize_database is None
        else initialize_database
    )
    database_factory: SQLiteConnectionFactory | None = None
    repository = settings_repository
    if repository is None and should_initialize_database:
        database_factory = SQLiteConnectionFactory(active_settings.database_path)
        MigrationManager(database_factory).migrate()
        repository = SQLiteSettingsRepository(database_factory)
    if repository is None:
        repository = InMemorySettingsRepository()

    resolved_settings = SettingsResolver(repository).resolve(environment)
    evaluation_preview_service = EvaluationPreviewService(
        max_candidate_chars=resolved_settings.values.max_model_answer_chars
    )
    endpoint_limiter = CentralEndpointLimiter()
    provider_call_executor = ProviderCallExecutor(endpoint_limiter, RetryPolicy())
    secret_repository: SQLiteSecretRepository | None = None
    endpoint_service: EndpointService | None = None
    model_profile_service: ModelProfileService | None = None
    cost_service: CostService | None = None
    dashboard_service: DashboardService | None = None
    golden_set_service: GoldenSetService | None = None
    model_discovery_service: ModelDiscoveryService | None = None
    judge_profile_service: JudgeProfileService | None = None
    run_queue: RunQueue | None = None
    aggregate_service: AggregateService | None = None
    quality_gate_service: QualityGateService | None = None
    configuration_exchange_service: ConfigurationExchangeService | None = None
    run_export_service: RunExportService | None = None
    run_view_service: RunViewService | None = None
    worker_runtime: WorkerRuntime | None = None
    model_profile_tester: ModelProfileTester | None = None
    if database_factory is not None:
        keyring = EncryptionKeyring.from_base64(resolved_settings.values.encryption_key)
        secret_repository = SQLiteSecretRepository(
            database_factory, SecretEncryptionService(keyring)
        )
        endpoint_repository = SQLiteEndpointRepository(database_factory)
        endpoint_service = EndpointService(endpoint_repository, secret_repository)
        model_profile_repository = SQLiteModelProfileRepository(database_factory)
        model_profile_service = ModelProfileService(model_profile_repository, endpoint_repository)
        provider_headers = ProviderHeaderResolver(
            endpoint_repository, secret_repository, environment
        )
        model_discovery_service = ModelDiscoveryService(
            endpoint_repository,
            model_profile_repository,
            ModelDiscoveryClient(
                provider_headers,
                max_response_bytes=resolved_settings.values.max_provider_response_bytes,
            ),
            provider_call_executor,
        )
        cost_service = CostService(SQLiteCostRepository(database_factory), model_profile_repository)
        dashboard_service = DashboardService(database_factory)
        judge_profile_service = JudgeProfileService(SQLiteJudgeProfileRepository(database_factory))
        run_queue = RunQueue(
            database_factory, max_profiles=resolved_settings.values.max_model_profiles_per_run
        )
        # UTC lease recovery runs before any worker can start.
        run_queue.recover_expired()
        aggregate_service = AggregateService(database_factory)
        configuration_exchange_service = ConfigurationExchangeService(database_factory)
        run_export_service = RunExportService(database_factory)
        run_view_service = RunViewService(database_factory)
        quality_gate_service = QualityGateService(database_factory, aggregate_service)
        golden_set_service = GoldenSetService(
            SQLiteGoldenSetRepository(database_factory),
            GoldenSetLimits(
                max_items=resolved_settings.values.max_golden_set_items,
                max_prompt_chars=resolved_settings.values.max_prompt_chars,
                max_system_prompt_chars=resolved_settings.values.max_system_prompt_chars,
                max_expected_answer_chars=resolved_settings.values.max_expected_answer_chars,
                max_judge_rubric_context_chars=(
                    resolved_settings.values.max_judge_rubric_context_chars
                ),
                max_import_file_bytes=resolved_settings.values.max_import_file_bytes,
            ),
        )
        streaming_client = OpenAIStreamingClient(
            provider_headers,
            max_response_bytes=resolved_settings.values.max_provider_response_bytes,
        )
        target_caller = StreamingTargetCaller(
            endpoint_repository,
            streaming_client,
            provider_call_executor,
        )
        model_profile_tester = ModelProfileTester(
            endpoint_repository,
            streaming_client,
            provider_call_executor,
        )
        judge_caller = JudgeCaller(
            run_queue,
            endpoint_repository,
            OpenAIProviderClient(
                provider_headers,
                max_response_bytes=resolved_settings.values.max_provider_response_bytes,
            ),
            provider_call_executor,
        )
        worker_runtime = WorkerRuntime(
            lambda: WorkerPipeline(
                run_queue,
                target_caller,
                judge=judge_caller,
                gates=quality_gate_service,
            ),
            concurrency=resolved_settings.values.run_concurrency,
        )

    logging_secrets = {
        str(item.value) for item in resolved_settings.metadata.values() if item.definition.secret
    }
    logging_secrets.add(active_settings.secret_key)
    configure_logging(resolved_settings.values.log_level, secrets=logging_secrets)
    settings_service = SettingsService(
        repository,
        environment,
        active=resolved_settings,
        dynamic_appliers={
            "log_level": lambda value: logging.getLogger().setLevel(str(value)),
            "max_model_answer_chars": lambda value: (
                evaluation_preview_service.apply_max_candidate_chars(cast(int, value))
            ),
        },
    )

    app = Flask(__name__)
    app.config.from_mapping(
        ENV=active_settings.flask_env,
        SECRET_KEY=active_settings.secret_key,
        TESTING=False,
    )
    install_web_security(app, active_settings, resolved_settings.values.max_import_file_bytes)
    if test_config:
        app.config.update(test_config)
    app.extensions["bootstrap_settings"] = active_settings
    app.extensions["settings_repository"] = repository
    app.extensions["active_settings"] = resolved_settings
    app.extensions["desired_settings"] = resolved_settings
    # Compatibility alias: `settings` always means configuration active in this process.
    app.extensions["settings"] = resolved_settings
    app.extensions["settings_service"] = settings_service
    app.extensions["evaluation_preview_service"] = evaluation_preview_service
    app.extensions["endpoint_limiter"] = endpoint_limiter
    app.extensions["provider_call_executor"] = provider_call_executor
    app.extensions["target_call_executor"] = provider_call_executor
    app.extensions["judge_call_executor"] = provider_call_executor
    app.extensions["connection_test_call_executor"] = provider_call_executor
    shutdown_callbacks = [endpoint_limiter.close]
    app.extensions["shutdown_services"] = lambda: [callback() for callback in shutdown_callbacks]
    if database_factory is not None:
        app.extensions["database_factory"] = database_factory
    if secret_repository is not None:
        app.extensions["secret_repository"] = secret_repository
    if endpoint_service is not None:
        app.extensions["endpoint_service"] = endpoint_service
    if model_profile_service is not None:
        app.extensions["model_profile_service"] = model_profile_service
    if model_profile_tester is not None:
        app.extensions["model_profile_tester"] = model_profile_tester
    if cost_service is not None:
        app.extensions["cost_service"] = cost_service
    if dashboard_service is not None:
        app.extensions["dashboard_service"] = dashboard_service
    if golden_set_service is not None:
        app.extensions["golden_set_service"] = golden_set_service
    if model_discovery_service is not None:
        app.extensions["model_discovery_service"] = model_discovery_service
    if judge_profile_service is not None:
        app.extensions["judge_profile_service"] = judge_profile_service
    if run_queue is not None:
        app.extensions["run_queue"] = run_queue
    if aggregate_service is not None:
        app.extensions["aggregate_service"] = aggregate_service
    if quality_gate_service is not None:
        app.extensions["quality_gate_service"] = quality_gate_service
    if configuration_exchange_service is not None:
        app.extensions["configuration_exchange_service"] = configuration_exchange_service
    if run_export_service is not None:
        app.extensions["run_export_service"] = run_export_service
    if run_view_service is not None:
        app.extensions["run_view_service"] = run_view_service
    if worker_runtime is not None:
        app.extensions["worker_runtime"] = worker_runtime

        def stop_workers() -> None:
            worker_runtime.stop()

        shutdown_callbacks.insert(0, stop_workers)

    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.globals["model_service"] = model_profile_service
    app.jinja_env.globals["display_metric"] = display_metric
    app.jinja_env.globals["display_money"] = display_money
    app.jinja_env.globals["status_label"] = status_label
    app.jinja_env.globals["chart_percent"] = chart_percent
    app.register_blueprint(gui)
    app.register_blueprint(api_v1)
    register_api_handlers(app, secrets=logging_secrets)

    @app.get("/ready")
    def ready() -> tuple[Response, int] | Response:
        if database_factory is None:
            return jsonify({"status": "not_ready", "reason": "database_unavailable"}), 503
        try:
            connection = database_factory.connect(create_parent=False)
            try:
                row = connection.execute("PRAGMA quick_check").fetchone()
                connection.execute("BEGIN IMMEDIATE")
                connection.rollback()
            finally:
                connection.close()
            if row is None or row[0] != "ok":
                raise RuntimeError("database integrity check failed")
        except Exception:
            return jsonify({"status": "not_ready", "reason": "storage_unavailable"}), 503
        return jsonify({"status": "ready"})

    @app.get("/health")
    def health() -> Response:
        """Return process liveness without requiring database access."""

        return jsonify({"status": "ok", "service": "thistlewarden", "version": "1"})

    return app
