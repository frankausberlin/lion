"""Deterministic tests for Linux system information readers."""

from pathlib import Path

import pytest

from repolion import scan


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("processor : 0\nmodel name\t: Example CPU\n", "Example CPU"),
        ("model name without separator", "Unknown"),
        ("model name: \n", "Unknown"),
        ("Hardware: ARM board", "Unknown"),
        ("", "Unknown"),
    ],
)
def test_cpu_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str, expected: str) -> None:
    """Read CPU fixture text independently of the host machine."""
    source = tmp_path / "cpuinfo"
    source.write_text(content)

    def fixture_path(name: str) -> Path:
        return source

    monkeypatch.setattr(scan, "Path", fixture_path)
    assert scan.scan_system().cpu_model == expected


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("MemTotal:       16384 kB\nMemFree: 100 kB", 16384 * 1024),
        ("MemTotal:", 0),
        ("MemTotal: nope kB", 0),
        ("MemTotal: -1 kB", 0),
        ("MemTotal: 16 MB", 0),
        ("MemFree: 100 kB", 0),
        ("", 0),
    ],
)
def test_memory_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str, expected: int) -> None:
    """Validate units, missing values and malformed memory readings."""
    source = tmp_path / "meminfo"
    source.write_text(content)

    def fixture_path(name: str) -> Path:
        return source

    monkeypatch.setattr(scan, "Path", fixture_path)
    assert scan.scan_system().memory_total_bytes == expected


def test_missing_proc_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep documented unknown values when proc files are absent."""

    def fixture_path(name: str) -> Path:
        return tmp_path / "missing"

    monkeypatch.setattr(scan, "Path", fixture_path)
    assert scan.scan_system().cpu_model == "Unknown"
    assert scan.scan_system().memory_total_bytes == 0


def test_os_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the standard library result without depending on the local distribution."""
    monkeypatch.setattr(scan.platform, "freedesktop_os_release", lambda: {"NAME": "Fixture Linux"})
    info = scan.scan_system()
    assert info.distribution == "Fixture Linux"
    assert info.distribution_version == "Unknown"


def test_missing_os_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing or unreadable release files yield unknown distribution details."""

    def unavailable() -> dict[str, str]:
        raise OSError("unavailable")

    monkeypatch.setattr(scan.platform, "freedesktop_os_release", unavailable)
    info = scan.scan_system()
    assert info.distribution == info.distribution_version == "Unknown"
