"""Tests for the LION state history persistence."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import tomli_w

from repolion import storage
from repolion.paths import get_history_dir
from repolion.state.model import Snapshot
from repolion.storage import HistoryError, load_latest, save_state

T0 = datetime(2026, 10, 5, 20, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=30)
T2 = T0 + timedelta(hours=1)


@pytest.fixture(autouse=True)
def isolated_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep tests away from the real LION data directory."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))


def _freeze(monkeypatch: pytest.MonkeyPatch, *instants: datetime) -> None:
    remaining = list(instants)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return remaining.pop(0)

    monkeypatch.setattr(storage, "datetime", FrozenDatetime)


def _host(hostname: str = "lion", status: str = "ok") -> dict[str, dict[str, object]]:
    return {"host": {"status": status, "error": "", "hostname": hostname}}


def _entry_files() -> list[Path]:
    return sorted(get_history_dir().glob("*.toml"))


def test_created_on_empty_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first state creates a new entry with both timestamps equal."""
    _freeze(monkeypatch, T0)

    outcome = save_state(_host())

    assert outcome.event == "created"
    assert outcome.path.exists()
    assert outcome.snapshot.erstscan == outcome.snapshot.zuletzt_bestaetigt == T0.isoformat()
    assert load_latest() == outcome.snapshot
    assert len(_entry_files()) == 1


def test_confirmed_updates_head_in_place(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unchanged state updates ``zuletzt_bestaetigt`` without a new file."""
    _freeze(monkeypatch, T0, T1)

    first = save_state(_host())
    second = save_state(_host())

    assert second.event == "confirmed"
    assert second.path == first.path
    assert second.snapshot.erstscan == T0.isoformat()
    assert second.snapshot.zuletzt_bestaetigt == T1.isoformat()
    assert load_latest() == second.snapshot
    assert len(_entry_files()) == 1
    assert not list(get_history_dir().glob("*.tmp"))


def test_appended_on_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """A different state appends a new entry and keeps the old one."""
    _freeze(monkeypatch, T0, T1)

    first = save_state(_host())
    second = save_state(_host(hostname="server"))

    assert second.event == "appended"
    assert second.path != first.path
    assert first.path.exists()
    assert len(_entry_files()) == 2
    assert load_latest() == second.snapshot


def test_collector_status_change_appends(monkeypatch: pytest.MonkeyPatch) -> None:
    """A collector status change counts as a state change."""
    _freeze(monkeypatch, T0, T1)

    save_state(_host(status="ok"))
    second = save_state(_host(status="error"))

    assert second.event == "appended"
    assert len(_entry_files()) == 2


def test_empty_or_missing_directory() -> None:
    """Both a missing and an empty history have no latest entry."""
    assert load_latest() is None
    get_history_dir().mkdir(parents=True)
    assert load_latest() is None


def _write_entry(name: str, erstscan: str, zuletzt: str) -> Path:
    path = get_history_dir() / name
    snapshot = Snapshot(erstscan=erstscan, zuletzt_bestaetigt=zuletzt, collectors=_host())
    path.write_text(tomli_w.dumps(snapshot.to_toml_dict()))
    return path


def test_latest_by_zuletzt_bestaetigt() -> None:
    """Select the entry with the newest confirmation timestamp."""
    get_history_dir().mkdir(parents=True)
    _write_entry("a.toml", T0.isoformat(), T0.isoformat())
    _write_entry("b.toml", T0.isoformat(), T2.isoformat())
    latest = load_latest()
    assert latest is not None
    assert latest.zuletzt_bestaetigt == T2.isoformat()


def test_latest_tie_break_by_filename() -> None:
    """Equal confirmation timestamps are resolved by filename."""
    get_history_dir().mkdir(parents=True)
    _write_entry("a.toml", T0.isoformat(), T1.isoformat())
    _write_entry("b.toml", T2.isoformat(), T1.isoformat())
    latest = load_latest()
    assert latest is not None
    assert latest.erstscan == T2.isoformat()


@pytest.mark.parametrize(
    "content",
    [
        "broken = [",
        "[collectors]\nhost = 1",
        "schema_version = 2\n[collectors]",
        'schema_version = 1\n"erstscan" = "2026-10-05T20:00:00"\n"zuletzt_bestaetigt" = "x"\n[collectors]',
    ],
)
def test_invalid_entry_reports_path(content: str) -> None:
    """Malformed entries fail loudly and name the offending file."""
    get_history_dir().mkdir(parents=True)
    path = get_history_dir() / "broken.toml"
    path.write_text(content)
    with pytest.raises(HistoryError, match=r"broken\.toml"):
        load_latest()


def test_failed_publish_cleans_temporary_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed publish leaves neither a partial entry nor a temporary file."""
    _freeze(monkeypatch, T0)

    def fail_link(source: object, destination: object) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr(storage.os, "link", fail_link)
    with pytest.raises(PermissionError):
        save_state(_host())
    assert not get_history_dir().exists() or not list(get_history_dir().iterdir())


def test_same_timestamp_collision_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two appends at the same instant get distinct filenames and keep the head newest."""
    _freeze(monkeypatch, T0, T0)

    first = save_state(_host())
    second = save_state(_host(hostname="server"))

    assert first.path != second.path
    assert len(_entry_files()) == 2
    assert second.path.name.endswith("~0001.toml")
    latest = load_latest()
    assert latest is not None
    assert latest.collectors["host"]["hostname"] == "server"


def test_collision_suffix_keeps_newest_head(monkeypatch: pytest.MonkeyPatch) -> None:
    """A confirmed head is still found after several same-instant collisions."""
    _freeze(monkeypatch, T0, T0, T0)

    save_state(_host(hostname="one"))
    save_state(_host(hostname="two"))
    third = save_state(_host(hostname="three"))

    assert third.event == "appended"
    assert len(_entry_files()) == 3
    latest = load_latest()
    assert latest is not None
    assert latest.collectors["host"]["hostname"] == "three"


def test_unreadable_non_head_entry_still_fails_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """A syntactically broken entry is reported even when it is not the head."""
    _freeze(monkeypatch, T0, T1)
    save_state(_host())
    (get_history_dir() / "zzz-broken.toml").write_text("broken = [")
    with pytest.raises(HistoryError, match=r"zzz-broken\.toml"):
        save_state(_host(hostname="server"))


@pytest.mark.parametrize(
    ("older", "newer"),
    [
        ("2026-10-05T22:00:00+02:00", "2026-10-05T21:00:00+00:00"),
        ("2026-10-05T20:00:00Z", "2026-10-05T20:00:00.500000+00:00"),
        ("2026-10-05T22:00:00+02:00", "2026-10-05T20:00:00+00:00"),
    ],
)
def test_scan_and_status_choose_same_head(monkeypatch: pytest.MonkeyPatch, older: str, newer: str) -> None:
    """Confirmation selects by instant, with filename ties, regardless of encoding."""
    get_history_dir().mkdir(parents=True)
    old_path = _write_entry("a.toml", older, older)
    new_path = _write_entry("b.toml", newer, newer)
    before = old_path.read_bytes()
    expected = load_latest()
    assert expected is not None
    assert expected.erstscan == newer
    _freeze(monkeypatch, T2 + timedelta(hours=1))
    outcome = save_state(_host())
    assert outcome.event == "confirmed"
    assert outcome.path == new_path
    assert outcome.snapshot.erstscan == expected.erstscan
    assert old_path.read_bytes() == before


@pytest.mark.parametrize("stamp", ["not-a-timestamp", "2026-10-05T20:00:00", 5])
def test_scan_rejects_invalid_selection_timestamp(monkeypatch: pytest.MonkeyPatch, stamp: object) -> None:
    """Invalid head-selection timestamps fail with the offending path."""
    _freeze(monkeypatch, T0, T1)
    save_state(_host())
    path = get_history_dir() / "invalid-time.toml"
    path.write_text(tomli_w.dumps({"zuletzt_bestaetigt": stamp}))
    with pytest.raises(HistoryError, match=r"invalid-time\.toml"):
        save_state(_host())
