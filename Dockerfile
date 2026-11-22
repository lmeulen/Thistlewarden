# syntax=docker/dockerfile:1
FROM python:3.15.2-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY pyproject.toml README.md constraints.txt ./
COPY docs/manual ./docs/manual
COPY src ./src
RUN python -m pip wheel --constraint constraints.txt --wheel-dir /wheels .

FROM python:3.15.2-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    CONTAINER_MODE=true \
    HOST=0.0.0.0 \
    PORT=5000 \
    DATABASE_PATH=/var/lib/thistlewarden/evaluator.db \
    EXPORT_PATH=/var/lib/thistlewarden/exports \
    APP_PROCESS_COUNT=1 \
    STORAGE_BACKEND=local \
    WORKERS_ENABLED=true \
    RELOADER_ENABLED=false
RUN groupadd --system --gid 10001 evaluator \
    && useradd --system --uid 10001 --gid evaluator --home /nonexistent --shell /usr/sbin/nologin evaluator \
    && mkdir -p /var/lib/thistlewarden/exports \
    && chown -R evaluator:evaluator /var/lib/thistlewarden
COPY --from=builder /wheels /wheels
RUN python -m pip install /wheels/*.whl && rm -rf /wheels
USER evaluator:evaluator
WORKDIR /var/lib/thistlewarden
VOLUME ["/var/lib/thistlewarden"]
EXPOSE 5000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import json,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:5000/ready',timeout=2); assert r.status==200 and json.load(r)['status']=='ready'"
ENTRYPOINT ["python", "-m", "evaluator.container_entrypoint"]
