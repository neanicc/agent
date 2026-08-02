# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.12.13-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b

FROM ${PYTHON_IMAGE} AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
COPY loopguard/pyproject.toml loopguard/README.md /build/loopguard/
COPY loopguard/src /build/loopguard/src
COPY services/control-api/pyproject.toml services/control-api/README.md /build/control-api/
COPY services/control-api/src /build/control-api/src
RUN python -m pip wheel \
    --wheel-dir /wheels \
    /build/loopguard \
    /build/control-api

FROM ${PYTHON_IMAGE} AS runtime
ARG BUILD_SHA=unknown
LABEL org.opencontainers.image.title="LoopGuard Control API" \
      org.opencontainers.image.revision="${BUILD_SHA}" \
      org.opencontainers.image.source="https://github.com/neanicc/agent"
ENV PATH=/opt/loopguard/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LOOPGUARD_API_BUILD_SHA=${BUILD_SHA}
RUN groupadd --gid 10001 loopguard \
    && useradd \
      --uid 10001 \
      --gid 10001 \
      --no-create-home \
      --home-dir /nonexistent \
      --shell /usr/sbin/nologin \
      loopguard \
    && mkdir -p /opt/loopguard /app \
    && chown -R 10001:10001 /opt/loopguard /app
COPY --from=builder /wheels /wheels
RUN python -m venv /opt/loopguard \
    && /opt/loopguard/bin/pip install \
      --no-cache-dir \
      --no-index \
      --find-links /wheels \
      loopguard-control-api \
    && rm -rf /wheels
COPY --chown=10001:10001 services/control-api/alembic /app/alembic
COPY --chown=10001:10001 services/control-api/alembic.ini /app/alembic.ini
WORKDIR /app
USER 10001:10001
EXPOSE 8000
STOPSIGNAL SIGTERM
CMD ["uvicorn", "loopguard_api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
