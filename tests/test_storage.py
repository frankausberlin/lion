"""Tests for LION scan persistence."""

from pathlib import Path

import pytest

from repolion.scan import SystemInfo
from repolion.storage import load_latest_scan, save_scan


@pytest.fixture(autouse=True)
def isolated_data_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep tests away from the real LION data directory."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))


def test_scan_round_trip() -> None:
    """Save and load a complete scan."""
    expected = SystemInfo(
        distribution="Test Linux",
        distribution_version="1.0",
        kernel="6.0.0-test",
        architecture="x86_64",
        hostname="lion-test",
        cpu_model="Test CPU",
        cpu_logical_cores=8,
        memory_total_bytes=16 * 1024**3,
    )

    path = save_scan(expected)
    loaded = load_latest_scan()

    assert path.exists()
    assert loaded is not None
    assert loaded.system == expected
    assert loaded.timestamp
