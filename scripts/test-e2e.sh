#!/usr/bin/env bash
# Keep the host untouched; copy diagnostics out before destroying the container.
set -euo pipefail
cd "$(dirname "$0")/.."

# --attach keeps the container and prints how to watch a live tmux session; the
# default path stays headless and removes the container on exit.
attach=0
if [[ "${1:-}" == "--attach" ]]; then
    attach=1
elif [[ $# -gt 0 ]]; then
    echo "usage: $0 [--attach]" >&2
    exit 2
fi

artifacts="$(mktemp -d "$PWD/e2e-artifacts.XXXXXX")"
container=""
cleanup() {
    result=$?
    trap - EXIT
    if [[ -n "$container" ]]; then
        docker cp "$container:/artifacts/." "$artifacts/" || result=1
        if (( attach )); then
            echo "E2E container kept for inspection: $container"
            echo "  docker exec -it $container tmux ls"
            echo "  docker exec -it $container tmux attach -t <session>"
            echo "Remove it with: docker rm -f $container"
        else
            docker rm -f "$container" >/dev/null || result=1
        fi
    fi
    echo "E2E diagnostics: $artifacts"
    exit "$result"
}
trap cleanup EXIT
docker build --file tests/e2e/Dockerfile --tag lion-e2e .
if (( attach )); then
    container="lion-e2e-attach"
    docker rm -f "$container" >/dev/null 2>&1 || true
    docker create --name "$container" --network none --env LION_E2E_CONTAINER=1 lion-e2e >/dev/null
    echo "Watch the run live (a session appears once the first watch scenario starts):"
    echo "  docker exec -it $container tmux ls"
    echo "  docker exec -it $container tmux attach -t <session>"
    docker start --attach "$container"
else
    container="$(docker create --network none --env LION_E2E_CONTAINER=1 lion-e2e)"
    docker start --attach "$container"
fi
