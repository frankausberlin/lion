# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.12
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-bookworm-slim

WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --group e2e --no-install-project

COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --group e2e --no-editable

COPY tests/ tests/
ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --uid 10001 --no-create-home --shell /bin/sh e2e \
    && mkdir /artifacts \
    && chown e2e:e2e /artifacts
USER e2e

CMD ["pytest", "tests/e2e", "-v", "-p", "no:cacheprovider", \
     "--basetemp=/artifacts/work", "--junitxml=/artifacts/junit.xml"]
