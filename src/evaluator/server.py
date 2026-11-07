"""Production single-process HTTP and worker lifecycle."""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol, cast

from waitress.server import create_server

from evaluator import create_app
from evaluator.config import BootstrapSettings, load_bootstrap_settings
from evaluator.logging_config import log_event
from evaluator.runtime import InstanceLock

_LOG = logging.getLogger(__name__)


class Server(Protocol):
    def run(self) -> None: ...

    def close(self) -> None: ...


def run(settings: BootstrapSettings | None = None) -> None:
    bootstrap = settings or load_bootstrap_settings()
    with InstanceLock.for_database(bootstrap.database_path) as lock:
        app = create_app(
            settings=bootstrap, settings_environment=os.environ, initialize_database=True
        )
        app.extensions["instance_lock"] = lock
        log_event(
            _LOG,
            logging.INFO,
            "application_initialized",
            host=bootstrap.host,
            port=bootstrap.port,
            workers_enabled=bootstrap.workers_enabled,
            database=bootstrap.database_path,
        )
        runtime = app.extensions.get("worker_runtime") if bootstrap.workers_enabled else None
        if runtime is not None:
            runtime.start()
            log_event(_LOG, logging.INFO, "workers_started", count=runtime.running_threads)
        server = cast(
            Server,
            cast(
                Any,
                create_server(
                    app,
                    host=bootstrap.host,
                    port=bootstrap.port,
                    threads=4,
                    clear_untrusted_proxy_headers=True,
                ),
            ),
        )
        log_event(
            _LOG,
            logging.INFO,
            "http_server_listening",
            host=bootstrap.host,
            port=bootstrap.port,
        )
        try:
            server.run()
        finally:
            log_event(_LOG, logging.INFO, "shutdown_started")
            server.close()
            app.extensions["shutdown_services"]()
            log_event(_LOG, logging.INFO, "shutdown_completed")


def main() -> None:
    run()


if __name__ == "__main__":
    main()
