# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.10.9@sha256:10902f58a1606787602f303954cea099626a4adb02acbac4c69920fe9d278f82 AS uv
FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55
COPY --from=uv /uv /usr/local/bin/uv
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates zsh
WORKDIR /app
ENV UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --group e2e --no-install-project --python /usr/bin/python3 --no-managed-python
COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --group e2e --python /usr/bin/python3 --no-managed-python
COPY tests/e2e/ tests/e2e/
ENV PATH="/app/.venv/bin:$PATH"
RUN mkdir /artifacts
CMD ["uv", "run", "--no-sync", "pytest", "tests/e2e", "-m", "e2e", "-v", "--basetemp=/artifacts/work", "--junitxml=/artifacts/junit.xml"]
