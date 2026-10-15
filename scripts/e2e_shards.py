r"""Balanced shard assignment for the Docker E2E suite.

``scripts/test-e2e.sh`` builds the ``lion-e2e`` image once and runs one
container per shard in parallel. This helper turns the E2E test files into
balanced shards with a greedy LPT (longest processing time first) packing:

- Input is the shard count ``N`` and the sorted list of ``tests/e2e/test_*.py``
  files below the repository root;
- every file carries a measured duration from :data:`WEIGHTS` (fallback
  :data:`FALLBACK_WEIGHT` seconds for files without an entry, so new files are
  always scheduled);
- ``N`` is capped at the number of files: a shard would otherwise receive no
  files and the ``tests/e2e`` directory argument would collect the whole suite
  again.

Usage::

    python3 scripts/e2e_shards.py [--shards N] [--root DIR]

Output is one line per shard, fields separated by tabs::

    <shard>\t<estimate_seconds>\t<file>\t<file>...

``shard`` is the 1-based shard number, ``estimate_seconds`` the summed weight
with one decimal, and each remaining field is a repository-relative test path.
The line set covers every input file exactly once.

Exit codes: ``0`` on success, ``2`` for usage errors (non-positive shard
count, missing directory).
"""

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

# Measured 2026-10-13 in one serial container (junit of e2e-artifacts.z6WLLe,
# 82 tests, ~94.6 s); test_watch_dog.py measured 2026-10-15 (sharded run,
# e2e-artifacts.RHhZvA). Refresh the numbers from the newest junit.xml when a
# file changed significantly or a new file appears; an unknown file
# automatically uses FALLBACK_WEIGHT.
WEIGHTS: Mapping[str, float] = {
    "test_watch_lifecycle.py": 18.9,
    "test_collector_dash.py": 11.3,
    "test_doctor_environment.py": 8.3,
    "test_watch_p10k.py": 7.3,
    "test_doctor_shlib.py": 7.0,
    "test_classify_recording.py": 6.4,
    "test_records_adr.py": 4.9,
    "test_watch_exit_paths.py": 4.6,
    "test_collectors_v2.py": 3.8,
    "test_watch_races.py": 3.7,
    "test_history_contract.py": 3.4,
    "test_package_lifecycle.py": 2.7,
    "test_doctor_permissions.py": 2.7,
    "test_mcp_serve.py": 2.4,
    "test_watch_user.py": 2.0,
    "test_watch_masking.py": 1.3,
    "test_watch_dog.py": 4.5,
    "test_wiki_on_scan.py": 1.1,
    "test_runner.py": 1.0,
    "test_shlib_lifecycle.py": 0.6,
}
FALLBACK_WEIGHT = 5.0
E2E_DIR = Path("tests/e2e")


def weight_of(path: str, weights: Mapping[str, float] = WEIGHTS) -> float:
    """Return the measured weight of ``path`` or the fallback weight."""
    return weights.get(Path(path).name, FALLBACK_WEIGHT)


def list_e2e_files(root: Path) -> list[str]:
    """Return the sorted repository-relative E2E test files below ``root``."""
    directory = root / E2E_DIR
    return sorted(str(E2E_DIR / path.name) for path in directory.glob("test_*.py"))


def shard_files(
    files: Sequence[str],
    shards: int,
    weights: Mapping[str, float] = WEIGHTS,
) -> list[list[str]]:
    """Pack ``files`` into at most ``shards`` balanced, non-empty shards.

    Files are placed longest-first onto the currently lightest shard (ties go
    to the lower shard index), which keeps the result deterministic. The shard
    count is capped at the file count so no shard stays empty; an empty file
    list yields no shards at all.

    Raises:
        ValueError: If ``shards`` is not positive.
    """
    if shards < 1:
        raise ValueError(f"shard count must be positive, got {shards}")
    if not files:
        return []
    count = min(shards, len(files))
    ordered = sorted(files, key=lambda path: (-weight_of(path, weights), path))
    buckets: list[list[str]] = [[] for _ in range(count)]
    loads = [0.0] * count
    for path in ordered:
        index = min(range(count), key=lambda position: (loads[position], position))
        buckets[index].append(path)
        loads[index] += weight_of(path, weights)
    return buckets


def _render(buckets: Sequence[Sequence[str]], weights: Mapping[str, float]) -> str:
    """Render the assignment as tab-separated lines."""
    lines: list[str] = []
    for index, bucket in enumerate(buckets, start=1):
        estimate = sum(weight_of(path, weights) for path in bucket)
        lines.append("\t".join([str(index), f"{estimate:.1f}", *bucket]))
    return "\n".join(lines)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split tests/e2e into balanced parallel shards.")
    _ = parser.add_argument("--shards", type=int, default=4, help="number of shards (default: 4)")
    _ = parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (default: the checkout containing this script)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Print the shard assignment; exit ``2`` on usage errors."""
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.shards < 1:
        print(f"error: --shards must be positive, got {args.shards}", file=sys.stderr)
        return 2
    files = list_e2e_files(args.root)
    if not files:
        print(f"error: no test files found under {args.root / E2E_DIR}", file=sys.stderr)
        return 2
    buckets = shard_files(files, args.shards)
    print(_render(buckets, WEIGHTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
