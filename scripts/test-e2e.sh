#!/usr/bin/env bash
# Run the opt-in Docker E2E suite. An empty suite exits 0 before any Docker call.
set -euo pipefail

cd "$(dirname "$0")/.."

tests_dir="tests/e2e"
hits=""
if [[ -d "$tests_dir" ]]; then
    if ! hits="$(find "$tests_dir" -type f -name 'test_*.py')"; then
        echo "Error: searching '$tests_dir' for test files failed." >&2
        exit 1
    fi
fi
if [[ -z "$hits" ]]; then
    cat >&2 <<'MSG'
No E2E tests configured yet.
Add test_*.py files under tests/e2e/ and run `just test-e2e` again.
See tests/e2e/README.md for the expected structure.
MSG
    exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "Error: 'docker' is not installed or not on PATH; E2E tests need Docker." >&2
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "Error: the Docker daemon is not reachable; start Docker and retry." >&2
    exit 1
fi

dockerfile="tests/e2e/Dockerfile"
if [[ ! -f "$dockerfile" ]]; then
    echo "Error: missing '$dockerfile'." >&2
    exit 1
fi

python_version="3.12"
if [[ -f .python-version ]]; then
    python_version="$(tr -d '[:space:]' < .python-version)"
fi
if [[ "$python_version" =~ ^([0-9]+\.[0-9]+)(\.[0-9]+)?$ ]]; then
    python_version="${BASH_REMATCH[1]}"
else
    python_version="3.12"
fi

tag="$(basename "$PWD" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9._-' '-')"
tag="${tag#-}"
tag="${tag%-}"
[[ -n "$tag" ]] || tag="lupysta"
tag="${tag}-e2e"

artifacts="$(mktemp -d "$PWD/e2e-artifacts.XXXXXX")"
container=""
cleanup() {
    if [[ -n "$container" ]]; then
        docker rm -f "$container" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

echo "Building E2E image '$tag' (Python $python_version)..."
docker build --file "$dockerfile" \
    --build-arg "PYTHON_VERSION=$python_version" --tag "$tag" .

container="$(docker create --network none \
    --env LUPYSTA_E2E=1 \
    --env LUPYSTA_E2E_CONTAINER=1 \
    "$tag")"

status=0
docker start --attach "$container" >"$artifacts/run.log" 2>&1 || status=$?

copy_status=0
if ! copy_error="$(docker cp "$container:/artifacts/." "$artifacts/" 2>&1)"; then
    copy_status=1
    printf 'Error: copying E2E artifacts from the container failed:\n%s\n' "$copy_error" >&2
fi
docker rm -f "$container" >/dev/null 2>&1 || true
container=""

if [[ $status -ne 0 ]]; then
    echo "E2E tests failed (exit $status). Last log lines:" >&2
    tail -n 40 "$artifacts/run.log" >&2 || true
    echo "Artifacts: $artifacts" >&2
    exit 1
fi

if [[ $copy_status -ne 0 ]]; then
    echo "E2E tests passed, but copying the artifacts failed. Artifacts: $artifacts" >&2
    exit 1
fi

echo "E2E tests passed. Artifacts: $artifacts"
