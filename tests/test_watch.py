"""Tests for the ``lion shell`` and ``lion watch`` commands and the banner."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner, Result

from lion.cli import app
from lion.command import status as status_command
from lion.command import watch as watch_command
from lion.program import recording, shell, shlib
from lion.program.storage import save_state

runner = CliRunner()

HOST_STATE: dict[str, dict[str, object]] = {"host": {"status": "ok", "error": "", "hostname": "lion"}}
OTHER_STATE: dict[str, dict[str, object]] = {"host": {"status": "ok", "error": "", "hostname": "other"}}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Disposable home and data directories with mocked syntax checks."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("ZDOTDIR", raising=False)
    monkeypatch.setattr(shell, "validate_zsh", Mock())
    monkeypatch.setattr(shlib, "_validate", Mock())


@pytest.fixture
def collect(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Replace the collector collection in the watch command with a stub."""
    fake = Mock(return_value=dict(HOST_STATE))
    monkeypatch.setattr(watch_command, "collect_state", fake)
    return fake


def _invoke(*args: str) -> Result:
    return runner.invoke(app, list(args))


def _insert_integration(home: Path) -> None:
    report = shell.insert(home)
    assert report.messages


def _append_segment(ref: str, lines: list[str]) -> None:
    record_dir = recording.get_recordings_dir() / ref
    events = record_dir / "events"
    events.mkdir(parents=True, exist_ok=True)
    path = events / "100-1-1.log"
    with path.open("a", encoding="utf-8") as stream:
        for line in lines:
            _ = stream.write(line + "\n")


def _header(epoch: str) -> str:
    boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    return f"segment\t{boot}\t{os.getpid()}\t{_start_ticks()}\t{epoch}"


def _start_ticks() -> int:
    stat = Path(f"/proc/{os.getpid()}/stat").read_text(encoding="utf-8")
    return int(stat.rsplit(")", 1)[-1].split()[19])


# --- lion shell --------------------------------------------------------------


def test_shell_bare_and_status_json(tmp_path: Path) -> None:
    """Bare ``lion shell`` is the status and ``--json`` carries the contract."""
    bare = _invoke("shell")
    assert bare.exit_code == 0
    assert "mode: zshrc" in bare.stdout

    payload = json.loads(_invoke("shell", "status", "--json").stdout)
    assert set(payload) == {"mode", "path", "inserted", "modified", "warnings"}
    assert payload["inserted"] is False


def test_shell_insert_remove_lifecycle(tmp_path: Path) -> None:
    """Insert and remove run through the CLI with exit code 0."""
    insert = _invoke("shell", "insert")
    assert insert.exit_code == 0
    assert "inserted" in insert.stdout
    assert json.loads(_invoke("shell", "status", "--json").stdout)["inserted"] is True

    remove = _invoke("shell", "remove")
    assert remove.exit_code == 0
    assert "removed" in remove.stdout
    assert json.loads(_invoke("shell", "status", "--json").stdout)["inserted"] is False


def test_shell_json_is_status_only(tmp_path: Path) -> None:
    """``--json`` on insert is a usage error (typer rejects the option)."""
    result = _invoke("shell", "insert", "--json")
    assert result.exit_code == 2


def test_shell_remove_warning_goes_to_stderr(collect: Mock) -> None:
    """The active-recording warning is on stderr, messages stay on stdout."""
    home = Path(os.environ["HOME"])
    _insert_integration(home)
    assert _invoke("watch", "start").exit_code == 0

    result = _invoke("shell", "remove")

    assert result.exit_code == 0
    assert "removed" in result.stdout
    assert "recording is active" in result.stderr


# --- lion watch start/stop ---------------------------------------------------


def test_watch_start_requires_integration() -> None:
    """Starting without the integration fails with the insert hint."""
    result = _invoke("watch", "start")

    assert result.exit_code == 1
    assert "lion shell insert" in result.stderr
    assert not recording.get_recordings_dir().exists()


def test_watch_start_second_time_fails(collect: Mock) -> None:
    """A second start must not disturb the active recording."""
    home = Path(os.environ["HOME"])
    _insert_integration(home)
    assert _invoke("watch", "start").exit_code == 0
    before = sorted(entry.ref for entry in recording.list_recordings())

    result = _invoke("watch", "start")

    assert result.exit_code == 1
    assert "pointer already exists" in result.stderr
    assert sorted(entry.ref for entry in recording.list_recordings()) == before


def test_watch_stop_without_recording_fails(collect: Mock) -> None:
    """Stopping without an active recording is an error."""
    result = _invoke("watch", "stop")

    assert result.exit_code == 1
    assert "No active recording" in result.stderr


def test_watch_collects_once_per_mutation(collect: Mock) -> None:
    """Start and stop each collect the machine state exactly once."""
    home = Path(os.environ["HOME"])
    _insert_integration(home)

    assert _invoke("watch", "start").exit_code == 0
    assert collect.call_count == 1
    collect.reset_mock()
    assert _invoke("watch", "stop").exit_code == 0
    assert collect.call_count == 1


def test_watch_start_stop_notes(collect: Mock) -> None:
    """The start note is stored and a stop note replaces it."""
    home = Path(os.environ["HOME"])
    _insert_integration(home)
    assert _invoke("watch", "start", "--note", "start note").exit_code == 0
    ref = next(entry.ref for entry in recording.list_recordings())
    assert _invoke("watch", "stop", "--note", "stop note").exit_code == 0

    assert recording.load_recording(ref).meta.note == "stop note"


# --- lion watch status -------------------------------------------------------


def test_watch_status_inactive(collect: Mock) -> None:
    """With no active recording status is exit 0 and reports inactive."""
    text = _invoke("watch", "status")
    assert text.exit_code == 0
    assert "No active recording" in text.stdout
    payload = json.loads(_invoke("watch", "status", "--json").stdout)
    assert payload == {"active": False}
    assert collect.call_count == 0


def test_watch_status_active_json_and_changes(collect: Mock) -> None:
    """An active recording reports segments, counters and the start diff."""
    home = Path(os.environ["HOME"])
    _insert_integration(home)
    assert _invoke("watch", "start").exit_code == 0
    ref = next(entry.ref for entry in recording.list_recordings())
    epoch = str(datetime.now(UTC).timestamp())
    _append_segment(
        ref,
        [
            _header(epoch),
            f"cmd\t{epoch}\t/tmp\techo\\ hi",
            f"result\t{epoch}\t0\t1.0\t2.5",
            f"comment\t{epoch}\t/tmp\t\\#\\ note",
        ],
    )
    collect.return_value = dict(OTHER_STATE)
    collect.reset_mock()

    result = _invoke("watch", "status", "--json")

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["active"] is True
    assert payload["recording"]["ref"] == ref
    assert payload["recording"]["segments"][0]["alive"] is True
    assert payload["recording"]["segments"][0]["commands"] == 1
    assert payload["structure_changed"] is False
    assert payload["changes"]["host"]["changed"]["hostname"] == {"old": "lion", "new": "other"}
    assert collect.call_count == 1

    text = _invoke("watch", "status")
    assert text.exit_code == 0
    assert f"Active recording {ref}" in text.stdout
    assert "hostname: lion -> other" in text.stdout


def test_watch_status_unchanged(collect: Mock) -> None:
    """An unchanged machine reports no changes in text and JSON."""
    home = Path(os.environ["HOME"])
    _insert_integration(home)
    assert _invoke("watch", "start").exit_code == 0

    text = _invoke("watch", "status")

    assert "No changes since the recording started." in text.stdout
    payload = json.loads(_invoke("watch", "status", "--json").stdout)
    assert payload["changes"] == {}
    assert payload["structure_changed"] is False


# --- lion watch list ---------------------------------------------------------


def test_watch_list_empty() -> None:
    """An empty recording space is an empty list with exit 0."""
    text = _invoke("watch", "list")
    assert text.exit_code == 0
    assert "No recordings stored" in text.stdout
    assert json.loads(_invoke("watch", "list", "--json").stdout) == {"recordings": []}


def test_watch_list_shows_events_and_size(collect: Mock) -> None:
    """The listing reports status, event count and size per recording."""
    home = Path(os.environ["HOME"])
    _insert_integration(home)
    assert _invoke("watch", "start").exit_code == 0
    ref = next(entry.ref for entry in recording.list_recordings())
    epoch = str(datetime.now(UTC).timestamp())
    _append_segment(ref, [_header(epoch), f"cmd\t{epoch}\t/tmp\techo\\ hi", f"exit\t{epoch}\t0"])
    assert _invoke("watch", "stop").exit_code == 0

    text = _invoke("watch", "list")

    assert text.exit_code == 0
    row = text.stdout.splitlines()[1].split()
    assert ref in text.stdout and "stopped" in text.stdout
    assert row[4] == "2"
    payload = json.loads(_invoke("watch", "list", "--json").stdout)["recordings"]
    assert payload[0]["ref"] == ref
    assert payload[0]["segments"][0]["exits"] == 1


# --- lion watch show ---------------------------------------------------------


def test_watch_show_empty() -> None:
    """Showing without any recording is exit 0 with a null recording."""
    text = _invoke("watch", "show")
    assert text.exit_code == 0
    assert "No recording stored" in text.stdout
    assert json.loads(_invoke("watch", "show", "--json").stdout) == {"recording": None, "events": []}


def test_watch_show_events_json(collect: Mock) -> None:
    """Show decodes the ordered events with status and duration."""
    home = Path(os.environ["HOME"])
    _insert_integration(home)
    assert _invoke("watch", "start").exit_code == 0
    ref = next(entry.ref for entry in recording.list_recordings())
    epoch = str(datetime.now(UTC).timestamp())
    _append_segment(
        ref,
        [
            _header(epoch),
            f"cmd\t{epoch}\t/tmp\tmulti$'\\n'line",
            f"result\t{epoch}\t130\t10.0\t10.0025",
            f"exit\t{epoch}\t130",
        ],
    )

    result = _invoke("watch", "show", ref, "--json")

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    kinds = [event["kind"] for event in payload["events"]]
    assert kinds == ["segment", "cmd", "result", "exit"]
    cmd = payload["events"][1]
    assert cmd["text"] == "multi\nline"
    assert cmd["cwd"] == "/tmp"
    assert payload["events"][2]["status"] == 130
    assert payload["events"][2]["duration_ms"] == 2.5
    assert payload["recording"]["ref"] == ref


def test_watch_show_defaults_to_active(collect: Mock) -> None:
    """Without a reference show prefers the active recording."""
    home = Path(os.environ["HOME"])
    _insert_integration(home)
    assert _invoke("watch", "start").exit_code == 0
    ref = next(entry.ref for entry in recording.list_recordings())

    result = _invoke("watch", "show")

    assert result.exit_code == 0
    assert f"Recording {ref} (open" in result.stdout


def test_watch_show_unknown_reference(collect: Mock) -> None:
    """An unknown reference fails with exit 1."""
    home = Path(os.environ["HOME"])
    _insert_integration(home)
    assert _invoke("watch", "start").exit_code == 0

    result = _invoke("watch", "show", "does-not-exist")

    assert result.exit_code == 1
    assert "Unknown reference" in result.stderr


def test_watch_damaged_record_fails_with_path() -> None:
    """A damaged recording fails list, show and status with its path."""
    home = Path(os.environ["HOME"])
    _insert_integration(home)
    record_dir = recording.get_recordings_dir() / "2026-10-05T20-00-00.000000Z"
    record_dir.mkdir(parents=True)
    (record_dir / "meta.toml").write_text("broken = [")
    (recording.get_active_pointer()).write_text("2026-10-05T20-00-00.000000Z\n")

    for args in (("watch", "list"), ("watch", "show", "latest"), ("watch", "status")):
        result = _invoke(*args)
        assert result.exit_code == 1, args
        assert "meta.toml" in result.stderr, args


def test_watch_read_commands_never_write(collect: Mock) -> None:
    """status, list and show leave the data directory byte-identical."""
    home = Path(os.environ["HOME"])
    _insert_integration(home)
    assert _invoke("watch", "start").exit_code == 0
    ref = next(entry.ref for entry in recording.list_recordings())
    epoch = str(datetime.now(UTC).timestamp())
    _append_segment(ref, [_header(epoch), f"cmd\t{epoch}\t/tmp\techo\\ hi"])
    data = Path(os.environ["XDG_DATA_HOME"])
    before = {
        path.relative_to(data).as_posix(): path.read_bytes() for path in sorted(data.rglob("*")) if path.is_file()
    }

    assert _invoke("watch", "status").exit_code == 0
    assert _invoke("watch", "list").exit_code == 0
    assert _invoke("watch", "show").exit_code == 0
    assert _invoke("watch", "status", "--json").exit_code == 0

    after = {path.relative_to(data).as_posix(): path.read_bytes() for path in sorted(data.rglob("*")) if path.is_file()}
    assert after == before


# --- banner ------------------------------------------------------------------


def test_status_banner_only_in_text_mode(collect: Mock, monkeypatch: pytest.MonkeyPatch) -> None:
    """An active recording adds only a text banner; the JSON payload is stable."""
    monkeypatch.setattr(status_command, "collect_state", Mock(return_value=dict(HOST_STATE)))
    home = Path(os.environ["HOME"])
    _insert_integration(home)
    save_state(dict(HOST_STATE))
    assert _invoke("watch", "start").exit_code == 0

    text = _invoke("status")
    assert text.exit_code == 0
    assert "Watch is recording since" in text.stdout
    assert "run 'lion watch status'" in text.stdout

    machine = _invoke("status", "--json")
    assert machine.exit_code == 0
    payload = json.loads(machine.stdout)
    assert set(payload) == {"changed", "structure_changed", "since", "differences"}
    assert "Watch is recording" not in machine.stdout


def test_status_without_recording_has_no_banner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Text mode stays unchanged while no recording is active."""
    monkeypatch.setattr(status_command, "collect_state", Mock(return_value=dict(HOST_STATE)))
    save_state(dict(HOST_STATE))

    text = _invoke("status")

    assert text.exit_code == 0
    assert "Watch is recording" not in text.stdout


def test_status_banner_is_read_only(collect: Mock, monkeypatch: pytest.MonkeyPatch) -> None:
    """The banner does not change the recording space or the history."""
    monkeypatch.setattr(status_command, "collect_state", Mock(return_value=dict(HOST_STATE)))
    home = Path(os.environ["HOME"])
    _insert_integration(home)
    save_state(dict(HOST_STATE))
    assert _invoke("watch", "start").exit_code == 0
    data = Path(os.environ["XDG_DATA_HOME"])
    before = {
        path.relative_to(data).as_posix(): path.read_bytes() for path in sorted(data.rglob("*")) if path.is_file()
    }

    assert _invoke("status").exit_code == 0

    after = {path.relative_to(data).as_posix(): path.read_bytes() for path in sorted(data.rglob("*")) if path.is_file()}
    assert after == before


def test_status_warns_on_unreadable_recording_space(monkeypatch: pytest.MonkeyPatch) -> None:
    """A damaged recording space warns on stderr without failing status."""
    monkeypatch.setattr(status_command, "collect_state", Mock(return_value=dict(HOST_STATE)))
    save_state(dict(HOST_STATE))
    record_dir = recording.get_recordings_dir() / "2026-10-05T20-00-00.000000Z"
    record_dir.mkdir(parents=True)
    (record_dir / "meta.toml").write_text("broken = [")
    recording.get_active_pointer().write_text("2026-10-05T20-00-00.000000Z\n")

    result = _invoke("status")

    assert result.exit_code == 0
    assert "recording space unreadable" in result.stderr
    assert "Watch is recording" not in result.stdout
