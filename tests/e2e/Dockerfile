# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.10.9@sha256:10902f58a1606787602f303954cea099626a4adb02acbac4c69920fe9d278f82 AS uv

# Powerlevel10k for the p10k watch scenario. It is cloned at build time (the
# runtime container has no network) and kept in a separate stage so the runtime
# image needs no git. Pinned to the annotated tag v1.20.0 (peeled commit
# 35833ea15f14b71dbcebc7e54c104d8d56ca5268); re-verify with
#   git ls-remote --tags https://github.com/romkatv/powerlevel10k.git
# before changing the pin.
FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55 AS p10k
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && git clone --depth=1 --branch v1.20.0 https://github.com/romkatv/powerlevel10k.git /opt/powerlevel10k \
    && rm -rf /opt/powerlevel10k/.git

FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55
COPY --from=uv /uv /usr/local/bin/uv
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates zsh
COPY --from=p10k /opt/powerlevel10k /opt/powerlevel10k
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
RUN useradd --uid 10001 --no-create-home --shell /bin/sh lion-e2e \
    && mkdir /artifacts
CMD ["uv", "run", "--no-sync", "pytest", "tests/e2e", "-m", "e2e", "-v", "--basetemp=/artifacts/work", "--junitxml=/artifacts/junit.xml"]
