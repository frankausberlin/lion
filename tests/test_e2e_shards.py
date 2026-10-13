"""Unit tests for the E2E shard helper (:mod:`scripts.e2e_shards`).

The helper only splits file names, so the tests are pure and Docker-free: the
real weight table must cover every current E2E file exactly once, the packing
must stay balanced and deterministic, and unknown files must never be dropped.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.e2e_shards import (
    E2E_DIR,
    FALLBACK_WEIGHT,
    WEIGHTS,
    list_e2e_files,
    main,
    shard_files,
    weight_of,
)

ROOT = Path(__file__).resolve().parents[1]


def _files() -> list[str]:
    return list_e2e_files(ROOT)


def _load(bucket: list[str]) -> float:
    return sum(weight_of(path) for path in bucket)


def test_assignment_covers_every_file_exactly_once() -> None:
    """The four-shard split schedules every current E2E file exactly once."""
    files = _files()

    buckets = shard_files(files, 4)

    scheduled = [path for bucket in buckets for path in bucket]
    assert sorted(scheduled) == sorted(files)
    assert len(scheduled) == len(set(scheduled))


def test_real_table_balances_under_the_expected_maximum() -> None:
    """With the measured weights the slowest shard stays around 25 seconds."""
    buckets = shard_files(_files(), 4)

    loads = [_load(bucket) for bucket in buckets]
    assert max(loads) <= 25.0
    assert min(loads) > 0.0
    assert sum(loads) == pytest.approx(sum(WEIGHTS.values()), abs=0.05)


def test_unknown_files_use_the_fallback_weight() -> None:
    """A file without a table entry is scheduled with the fallback weight."""
    weights = {"test_a.py": 10.0, "test_b.py": 10.0, "test_c.py": 1.0}
    files = ["tests/e2e/test_a.py", "tests/e2e/test_b.py", "tests/e2e/test_c.py", "tests/e2e/test_new.py"]

    assert weight_of("tests/e2e/test_new.py", weights) == FALLBACK_WEIGHT
    buckets = shard_files(files, 2, weights)

    assert sorted(buckets[0]) == ["tests/e2e/test_a.py", "tests/e2e/test_new.py"]
    assert sorted(buckets[1]) == ["tests/e2e/test_b.py", "tests/e2e/test_c.py"]


def test_packing_is_deterministic() -> None:
    """Identical input yields the identical assignment."""
    files = _files()

    assert shard_files(files, 4) == shard_files(files, 4)
    assert shard_files(files, 3) == shard_files(files, 3)


def test_shard_count_is_capped_at_the_file_count() -> None:
    """More shards than files collapse to one non-empty shard per file."""
    files = ["tests/e2e/test_a.py", "tests/e2e/test_b.py"]

    buckets = shard_files(files, 5)

    assert len(buckets) == 2
    assert all(bucket for bucket in buckets)
    assert sorted(path for bucket in buckets for path in bucket) == sorted(files)


def test_single_shard_is_the_whole_list() -> None:
    """One shard equals today's serial file list."""
    files = _files()

    buckets = shard_files(files, 1)

    assert len(buckets) == 1
    assert sorted(buckets[0]) == sorted(files)


def test_non_positive_shard_count_is_rejected() -> None:
    """Zero and negative shard counts raise instead of silently mis-packing."""
    with pytest.raises(ValueError, match="positive"):
        _ = shard_files(_files(), 0)
    with pytest.raises(ValueError, match="positive"):
        _ = shard_files(_files(), -1)


def test_empty_file_list_yields_no_shards() -> None:
    """Without files there is nothing to run and no empty shard."""
    assert shard_files([], 4) == []


def test_cli_output_shape_and_union(tmp_path: Path) -> None:
    """The CLI prints one parseable line per shard covering the whole list."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "e2e_shards.py"), "--shards", "4"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    lines = result.stdout.strip().splitlines()
    assert len(lines) == 4
    scheduled: list[str] = []
    for number, line in enumerate(lines, start=1):
        fields = line.split("\t")
        assert fields[0] == str(number)
        estimate = float(fields[1])
        assert estimate > 0
        scheduled.extend(fields[2:])
    assert sorted(scheduled) == sorted(_files())


def test_cli_single_shard_lists_the_directory_files() -> None:
    """``--shards 1`` prints every E2E file in one shard."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "e2e_shards.py"), "--shards", "1"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    lines = result.stdout.strip().splitlines()
    assert len(lines) == 1
    fields = lines[0].split("\t")
    assert fields[0] == "1"
    assert sorted(fields[2:]) == sorted(_files())
    assert all(path.startswith(str(E2E_DIR)) for path in fields[2:])


def test_cli_rejects_non_positive_shards(capsys: pytest.CaptureFixture[str]) -> None:
    """A non-positive ``--shards`` is a usage error with exit code 2."""
    assert main(["--shards", "0"]) == 2
    assert "positive" in capsys.readouterr().err
