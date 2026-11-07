"""Container startup coordinator: validate storage, then start one runtime."""

from __future__ import annotations

from evaluator.config import load_bootstrap_settings
from evaluator.server import run


def main() -> None:
    settings = load_bootstrap_settings()
    if not settings.container_mode:
        raise RuntimeError("Container entrypoint requires CONTAINER_MODE=true")
    for path in (settings.database_path.parent, settings.export_path):
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-probe"
        probe.write_bytes(b"ok")
        probe.unlink()
    run(settings)


if __name__ == "__main__":
    main()
