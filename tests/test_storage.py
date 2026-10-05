"""Tests for LION scan persistence."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from repolion import storage
from repolion.paths import get_scans_dir
from repolion.scan import SystemInfo
from repolion.storage import load_latest_scan, save_scan


@pytest.fixture(autouse=True)
def isolated_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep tests away from the real LION data directory."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))


@pytest.fixture
def system_info() -> SystemInfo:
    """Return a deterministic system sample."""
    return SystemInfo("Test Linux", "1.0", "6.0", "x86_64", "lion-test", "Test CPU", 8, 16 * 1024**3)


def test_scan_round_trip(system_info: SystemInfo) -> None:
    """Save and load a complete scan."""
    path = save_scan(system_info)
    loaded = load_latest_scan()
    assert path.exists()
    assert loaded is not None
    assert loaded.system == system_info
    assert datetime.fromisoformat(loaded.timestamp).utcoffset() == UTC.utcoffset(None)


def test_identical_timestamps_do_not_overwrite(monkeypatch: pytest.MonkeyPatch, system_info: SystemInfo) -> None:
    """Preserve both scans even when the clock returns the exact same instant."""

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return datetime(2026, 10, 5, 12, tzinfo=UTC)

    monkeypatch.setattr(storage, "datetime", FrozenDatetime)
    first = save_scan(system_info)
    original = first.read_bytes()
    second = save_scan(system_info)
    assert first != second
    assert first.read_bytes() == original
    assert len(list(get_scans_dir().glob("*.toml"))) == 2


def test_latest_uses_record_timestamp(system_info: SystemInfo) -> None:
    """Order legacy local timestamps correctly across the autumn DST change."""
    path = save_scan(system_info)
    content = path.read_text()
    record = load_latest_scan()
    assert record is not None
    path.unlink()
    scans = get_scans_dir()
    (scans / "2026-10-25T02-50-00+0200.toml").write_text(content.replace(record.timestamp, "2026-10-25T02:50:00+02:00"))
    (scans / "2026-10-25T02-10-00+0100.toml").write_text(content.replace(record.timestamp, "2026-10-25T02:10:00+01:00"))
    latest = load_latest_scan()
    assert latest is not None
    assert latest.timestamp == "2026-10-25T02:10:00+01:00"


@pytest.mark.parametrize("content", ["broken = [", "[scan]\ntimestamp = 'oops'", "[system]\nhostname = 'test'"])
def test_invalid_scan_reports_path(content: str) -> None:
    """Explain malformed or incomplete files instead of leaking parser exceptions."""
    scans = get_scans_dir()
    scans.mkdir(parents=True)
    path = scans / "broken.toml"
    path.write_text(content)
    with pytest.raises(ValueError, match=r"broken\.toml"):
        load_latest_scan()


def test_empty_directory() -> None:
    """An empty scan directory has no latest record."""
    get_scans_dir().mkdir(parents=True)
    assert load_latest_scan() is None


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("cpu_logical_cores = 8", "cpu_logical_cores = true"),
        ("memory_total_bytes = 17179869184", "memory_total_bytes = -1"),
        ('hostname = "lion-test"', "hostname = 42"),
    ],
)
def test_invalid_field_types(system_info: SystemInfo, old: str, new: str) -> None:
    """Reject valid TOML with invalid system field values."""
    path = save_scan(system_info)
    path.write_text(path.read_text().replace(old, new))
    with pytest.raises(ValueError, match="Cannot load scan"):
        load_latest_scan()


def test_naive_timestamp(system_info: SystemInfo) -> None:
    """Do not guess the timezone of an ambiguous stored timestamp."""
    path = save_scan(system_info)
    record = load_latest_scan()
    assert record is not None
    path.write_text(path.read_text().replace(record.timestamp, "2026-10-05T12:00:00"))
    with pytest.raises(ValueError, match="timezone"):
        load_latest_scan()


def test_failed_publication_cleans_temporary_file(monkeypatch: pytest.MonkeyPatch, system_info: SystemInfo) -> None:
    """A failed publish leaves no partial scan or temporary file."""

    def fail_link(source: object, destination: object) -> None:
        assert not list(get_scans_dir().glob("*.toml"))
        raise PermissionError("read-only")

    monkeypatch.setattr(storage.os, "link", fail_link)
    with pytest.raises(PermissionError):
        save_scan(system_info)
    assert not list(get_scans_dir().iterdir())


def test_destination_collision_retries(monkeypatch: pytest.MonkeyPatch, system_info: SystemInfo) -> None:
    """Even an identical random suffix cannot overwrite an existing file."""
    from uuid import UUID

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return datetime(2026, 10, 5, 12, tzinfo=UTC)

    identifiers = iter([UUID(int=1), UUID(int=1), UUID(int=2)])
    monkeypatch.setattr(storage, "datetime", FrozenDatetime)
    monkeypatch.setattr(storage, "uuid4", lambda: next(identifiers))
    first = save_scan(system_info)
    second = save_scan(system_info)
    assert first != second
    assert first.exists() and second.exists()
