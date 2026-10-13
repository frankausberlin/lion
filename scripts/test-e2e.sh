#!/usr/bin/env bash
# Run the E2E suite in disposable containers; the host stays untouched.
#
# The image is built once, then the suite is split over LION_E2E_SHARDS
# containers (default 4) that run in parallel, one pytest file list per shard
# (computed by scripts/e2e_shards.py). Artifacts of every shard land in
# e2e-artifacts.XXXXXX/shard-<n>/ (junit.xml, work/, run.log). --attach keeps
# today's single container and prints how to watch a live tmux session.
set -euo pipefail
cd "$(dirname "$0")/.."

usage() {
    echo "usage: $0 [--attach] [--shards N]" >&2
    exit 2
}

attach=0
shards="${LION_E2E_SHARDS:-4}"
shards_given=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --attach)
            attach=1
            shift
            ;;
        --shards)
            [[ $# -ge 2 ]] || usage
            shards="$2"
            shards_given=1
            shift 2
            ;;
        --shards=*)
            shards="${1#--shards=}"
            shards_given=1
            shift
            ;;
        *)
            usage
            ;;
    esac
done

if ! [[ "$shards" =~ ^[0-9]+$ ]] || (( shards < 1 )); then
    echo "error: --shards needs a positive integer, got '$shards'" >&2
    exit 2
fi
if (( attach )); then
    if (( shards_given )) && (( shards > 1 )); then
        echo "error: --attach runs a single container; it cannot be combined with --shards > 1" >&2
        exit 2
    fi
    shards=1
fi

artifacts="$(mktemp -d "$PWD/e2e-artifacts.XXXXXX")"
containers=()
targets=()
names=()
numbers=()
estimates=()
handled=0

cleanup() {
    result=$?
    trap - EXIT
    if (( ! handled )); then
        for i in "${!containers[@]}"; do
            docker cp "${containers[$i]}:/artifacts/." "${targets[$i]}/" 2>/dev/null || true
            if (( attach )); then
                echo "E2E container kept for inspection: ${containers[$i]}"
                echo "  docker exec -it ${containers[$i]} tmux ls"
                echo "  docker exec -it ${containers[$i]} tmux attach -t <session>"
                echo "Remove it with: docker rm -f ${containers[$i]}"
            else
                docker rm -f "${containers[$i]}" >/dev/null 2>&1 || true
            fi
        done
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
    containers=("$container")
    targets=("$artifacts")
    echo "Watch the run live (a session appears once the first watch scenario starts):"
    echo "  docker exec -it $container tmux ls"
    echo "  docker exec -it $container tmux attach -t <session>"
    docker start --attach "$container"
    exit 0
fi

if ! assignment="$(python3 scripts/e2e_shards.py --shards "$shards")"; then
    echo "error: could not compute the shard assignment (python3 scripts/e2e_shards.py)" >&2
    exit 1
fi

effective_shards="$(printf '%s\n' "$assignment" | grep -c .)"
echo "E2E: $effective_shards container(s) in parallel; per-shard artifacts under $artifacts"
while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    IFS=$'\t' read -r -a fields <<<"$line"
    number="${fields[0]}"
    estimate="${fields[1]}"
    files=("${fields[@]:2}")
    name="shard-$number"
    target="$artifacts/$name"
    mkdir -p "$target"
    echo "  $name: ${estimate}s estimated, ${#files[@]} file(s): ${files[*]}"
    container="$(docker create --network none --env LION_E2E_CONTAINER=1 lion-e2e \
        uv run --no-sync pytest "${files[@]}" -m e2e -v \
        --basetemp=/artifacts/work --junitxml=/artifacts/junit.xml)"
    numbers+=("$number")
    estimates+=("$estimate")
    names+=("$name")
    targets+=("$target")
    containers+=("$container")
done <<<"$assignment"

declare -A pid_shard=()
for i in "${!containers[@]}"; do
    (
        started=$(date +%s)
        if docker start --attach "${containers[$i]}" >"${targets[$i]}/run.log" 2>&1; then
            code=0
        else
            code=$?
        fi
        finished=$(date +%s)
        printf '%s\n' "$code" >"${targets[$i]}/exit"
        printf '%s\n' "$((finished - started))" >"${targets[$i]}/seconds"
    ) &
    pid_shard[$!]="$i"
done

failed=0
report_shard() {
    local i="$1" code seconds
    code="$(cat "${targets[$i]}/exit" 2>/dev/null || echo '?')"
    seconds="$(cat "${targets[$i]}/seconds" 2>/dev/null || echo '?')"
    echo "  ${names[$i]} finished: exit $code in ${seconds}s (estimated ${estimates[$i]}s)"
    if [[ "$code" != "0" ]]; then
        failed=1
    fi
}

# Bash >= 5.1 reports which job finished; older shells wait in start order.
if (( BASH_VERSINFO[0] > 5 || (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] >= 1) )); then
    while (( ${#pid_shard[@]} > 0 )); do
        done_pid=""
        wait -n -p done_pid || true
        report_shard "${pid_shard[$done_pid]}"
        unset 'pid_shard[$done_pid]'
    done
else
    for pid in "${!pid_shard[@]}"; do
        wait "$pid" || true
        report_shard "${pid_shard[$pid]}"
    done
fi

for i in "${!containers[@]}"; do
    if ! docker cp "${containers[$i]}:/artifacts/." "${targets[$i]}/"; then
        echo "warning: could not copy artifacts from ${names[$i]}" >&2
        failed=1
    fi
    docker rm -f "${containers[$i]}" >/dev/null || failed=1
done
handled=1

echo "E2E shard summary:"
for i in "${!numbers[@]}"; do
    code="$(cat "${targets[$i]}/exit" 2>/dev/null || echo '?')"
    seconds="$(cat "${targets[$i]}/seconds" 2>/dev/null || echo '?')"
    echo "  ${names[$i]}: exit $code, ${seconds}s (estimated ${estimates[$i]}s)"
done

if (( failed )); then
    for i in "${!numbers[@]}"; do
        code="$(cat "${targets[$i]}/exit" 2>/dev/null || echo '?')"
        if [[ "$code" != "0" ]]; then
            echo "--- ${names[$i]} log tail (${targets[$i]}/run.log) ---" >&2
            tail -n 40 "${targets[$i]}/run.log" >&2 || true
        fi
    done
    echo "E2E failed: at least one shard exited non-zero." >&2
    exit 1
fi
echo "E2E passed: all ${#containers[@]} shard(s) green."
