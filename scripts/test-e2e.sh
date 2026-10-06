#!/usr/bin/env bash
# Keep the host untouched; copy diagnostics out before destroying the container.
set -euo pipefail
cd "$(dirname "$0")/.."
artifacts="$(mktemp -d "$PWD/e2e-artifacts.XXXXXX")"
container=""
cleanup() {
    result=$?
    trap - EXIT
    if [[ -n "$container" ]]; then
        docker cp "$container:/artifacts/." "$artifacts/" || result=1
        docker rm -f "$container" >/dev/null || result=1
    fi
    echo "E2E diagnostics: $artifacts"
    exit "$result"
}
trap cleanup EXIT
docker build --file tests/e2e/Dockerfile --tag lion-e2e .
container="$(docker create --network none --env LION_E2E_CONTAINER=1 lion-e2e)"
docker start --attach "$container"
