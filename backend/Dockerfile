# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.13-slim-bookworm

FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/opt/app/bin:${PATH}

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --home-dir /nonexistent --no-create-home app \
    && mkdir -p /app /var/lib/cooperative-clearing/blobs \
    && chown -R app:app /app /var/lib/cooperative-clearing

WORKDIR /app

COPY requirements.lock ./requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock

COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
RUN python -m pip install --no-cache-dir --no-deps .

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "cooperative_clearing.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips=*"]

FROM runtime AS test

USER root
COPY requirements-dev.lock ./requirements-dev.lock
RUN python -m pip install --no-cache-dir --require-hashes -r requirements-dev.lock
COPY openapi.json ./openapi.json
COPY tests ./tests
USER 10001:10001

CMD ["pytest", "--cov", "--cov-report=term-missing"]
