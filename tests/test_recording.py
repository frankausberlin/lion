"""Tests for the recording data space, lifecycle and segment liveness."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import tomli_w

from lion import __version__
from lion.program import recording
from lion.program.checks import CheckStatus, DoctorContext
from lion.program.recording import (
    STATUS_OPEN,
    STATUS_STOPPED,
    RecordingError,
    active_recording,
    get_active_pointer,
    get_recordings_dir,
    list_recordings,
    load_events,
    load_recording,
    read_events,
    resolve_recording,
    start_recording,
    stop_recording,
    unquote_zsh,
)
from lion.program.storage import COMPACT_TIMESTAMP_FORMAT
from lion.state.model import Snapshot

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

    monkeypatch.setattr(recording, "datetime", FrozenDatetime)


def _state(hostname: str = "lion") -> dict[str, dict[str, object]]:
    return {"host": {"status": "ok", "error": "", "hostname": hostname}}


def _write_meta(directory: str, **overrides: object) -> None:
    record_dir = get_recordings_dir() / directory
    record_dir.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {
        "schema_version": 1,
        "ref": directory,
        "status": STATUS_OPEN,
        "started_at": T0.isoformat(),
        "integration_version": "0.1.0",
        "note": "",
    }
    data.update(overrides)
    (record_dir / "meta.toml").write_text(tomli_w.dumps(data))


def _write_start_snapshot(ref: str) -> None:
    record_dir = get_recordings_dir() / ref
    snapshot = Snapshot(created_at=T0.isoformat(), confirmed_at=T0.isoformat(), collectors=_state())
    record_dir.mkdir(parents=True, exist_ok=True)
    (record_dir / "state-start.toml").write_text(tomli_w.dumps(snapshot.to_toml_dict()))


def _append_segment(ref: str, name: str, lines: list[str]) -> Path:
    record_dir = get_recordings_dir() / ref
    (record_dir / "events").mkdir(parents=True, exist_ok=True)
    path = record_dir / "events" / name
    with path.open("a", encoding="utf-8") as stream:
        for line in lines:
            _ = stream.write(line + "\n")
    return path


def _current_boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()


def _current_start_ticks(pid: int) -> int:
    data = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    return int(data.rsplit(")", 1)[-1].split()[19])


def _header(
    boot_id: str | None = None,
    pid: int | None = None,
    ticks: int | None = None,
    epoch: float | None = None,
) -> str:
    boot = boot_id if boot_id is not None else _current_boot_id()
    process = pid if pid is not None else os.getpid()
    start_ticks = ticks if ticks is not None else _current_start_ticks(process)
    moment = epoch if epoch is not None else T0.timestamp()
    return f"segment\t{boot}\t{process}\t{start_ticks}\t{moment}"


def _manifest(root: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            snapshot[path.relative_to(root).as_posix()] = path.read_bytes()
    return snapshot


# --- lifecycle ---------------------------------------------------------------


def test_start_creates_layout_and_pointer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Starting publishes meta, the start state, events/ and the pointer."""
    _freeze(monkeypatch, T0)

    result = start_recording(_state("one"), note="hello")

    ref = T0.strftime(COMPACT_TIMESTAMP_FORMAT)
    assert result.ref == ref
    assert result.active
    assert result.meta.status == STATUS_OPEN
    assert result.meta.note == "hello"
    assert result.meta.integration_version == __version__
    assert result.meta.started_at == T0.isoformat()
    assert result.state_start.created_at == result.state_start.confirmed_at == T0.isoformat()
    assert result.state_end is None
    record_dir = get_recordings_dir() / ref
    assert (record_dir / "meta.toml").is_file()
    assert (record_dir / "state-start.toml").is_file()
    assert (record_dir / "events").is_dir()
    assert get_active_pointer().read_text() == f"{ref}\n"
    assert (record_dir.stat().st_mode & 0o777) == 0o700
    assert ((record_dir / "meta.toml").stat().st_mode & 0o777) == 0o600


def test_stop_publishes_end_state_and_clears_pointer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stopping writes state-end and removes the pointer."""
    _freeze(monkeypatch, T0, T1)
    start_recording(_state("one"), note="start note")

    result = stop_recording(_state("two"))

    assert result.meta.status == STATUS_STOPPED
    assert result.meta.stopped_at == T1.isoformat()
    assert result.meta.note == "start note"
    assert result.state_end is not None
    assert result.state_end.created_at == T1.isoformat()
    assert not get_active_pointer().exists()
    assert active_recording() is None
    assert load_recording(result.ref) == result


def test_stop_note_replaces_start_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stop note replaces the start note; without one the start note stays."""
    _freeze(monkeypatch, T0, T1)
    start_recording(_state(), note="start")
    assert stop_recording(_state(), note="stop").meta.note == "stop"

    _freeze(monkeypatch, T2, T2 + timedelta(minutes=1))
    start_recording(_state(), note="start")
    assert stop_recording(_state()).meta.note == "start"


def test_second_start_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """An existing pointer blocks a second start and names the pointer."""
    _freeze(monkeypatch, T0)
    start_recording(_state())

    with pytest.raises(RecordingError, match="pointer already exists") as excinfo:
        start_recording(_state())
    assert str(get_active_pointer()) in str(excinfo.value)
    assert len(list(get_recordings_dir().iterdir())) == 2


def test_stop_without_active_recording_fails() -> None:
    """Stopping without a pointer is a loud error."""
    with pytest.raises(RecordingError, match="No active recording"):
        stop_recording(_state())


def test_stop_of_stopped_recorded_pointer_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pointer that references a stopped record is rejected."""
    _freeze(monkeypatch, T0, T1)
    start_recording(_state())
    result = stop_recording(_state())
    get_active_pointer().write_text(result.ref + "\n")

    with pytest.raises(RecordingError, match="already stopped"):
        stop_recording(_state())
    with pytest.raises(RecordingError, match="stopped recording"):
        active_recording()


def test_same_timestamp_collision_adds_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two recordings from the same instant get a ``~NNNN`` directory suffix."""
    _freeze(monkeypatch, T0, T0, T0)
    first = start_recording(_state())
    stop_recording(_state())
    second = start_recording(_state())

    assert first.ref == T0.strftime(COMPACT_TIMESTAMP_FORMAT)
    assert second.ref == first.ref + "~0001"
    assert [entry.ref for entry in list_recordings()] == [first.ref, second.ref]


def test_failed_start_cleans_up_its_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """A start that cannot publish the pointer removes its fresh record."""
    _freeze(monkeypatch, T0)
    start_recording(_state())

    def fail_link(source: object, target: object) -> None:
        raise FileExistsError(str(target))

    with monkeypatch.context() as patch:
        patch.setattr(recording.os, "link", fail_link)
        with pytest.raises(RecordingError):
            start_recording(_state())
    assert [entry.ref for entry in list_recordings()] == [T0.strftime(COMPACT_TIMESTAMP_FORMAT)]


# --- clock guard -------------------------------------------------------------


def test_start_refuses_when_clock_precedes_recording_space(monkeypatch: pytest.MonkeyPatch) -> None:
    """A start with a clock before the newest known timestamp changes nothing."""
    _freeze(monkeypatch, T1, T1)
    start_recording(_state())
    stop_recording(_state())
    before = _manifest(get_recordings_dir())

    _freeze(monkeypatch, T0)
    with pytest.raises(RecordingError, match="System clock is earlier"):
        start_recording(_state())
    assert _manifest(get_recordings_dir()) == before


def test_start_refuses_when_clock_precedes_segment_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Segment starts participate in the clock guard."""
    _freeze(monkeypatch, T1)
    start_recording(_state())
    ref = next(entry.ref for entry in list_recordings())
    _append_segment(ref, "100-1-1.log", [_header(epoch=T2.timestamp())])
    _freeze(monkeypatch, T2)
    stop_recording(_state())

    _freeze(monkeypatch, T0)
    with pytest.raises(RecordingError, match="System clock is earlier"):
        start_recording(_state())


def test_stop_refuses_when_clock_precedes_last_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stopping is guarded against the last event of its own record."""
    _freeze(monkeypatch, T0)
    start_recording(_state())
    ref = next(entry.ref for entry in list_recordings())
    _append_segment(
        ref,
        "100-1-1.log",
        [_header(epoch=T0.timestamp()), f"cmd\t{T1.timestamp()}\t/tmp\techo\\ hi"],
    )
    _freeze(monkeypatch, T0 + timedelta(minutes=1))
    with pytest.raises(RecordingError, match="System clock is earlier"):
        stop_recording(_state())
    assert active_recording() is not None


# --- reading -----------------------------------------------------------------


def test_list_and_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    """Recordings list oldest first and resolve by ref, prefix, alias and active."""
    _freeze(monkeypatch, T0, T1, T2, T2)
    first = start_recording(_state())
    stop_recording(_state())
    second = start_recording(_state())

    assert [entry.ref for entry in list_recordings()] == [first.ref, second.ref]
    assert resolve_recording(first.ref).ref == first.ref
    assert resolve_recording(first.ref[:19]).ref == first.ref
    assert resolve_recording("latest").ref == second.ref
    assert resolve_recording("active").ref == second.ref
    stop_recording(_state())
    with pytest.raises(RecordingError, match="No active recording"):
        resolve_recording("active")


def test_resolve_unknown_and_ambiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown references list the valid ones; ambiguous prefixes name candidates."""
    _freeze(monkeypatch, T0, T0, T0, T0)
    first = start_recording(_state())
    stop_recording(_state())
    second = start_recording(_state())
    stop_recording(_state())

    with pytest.raises(RecordingError, match="Unknown reference") as unknown:
        resolve_recording("1999-01-01")
    assert first.ref in str(unknown.value) and second.ref in str(unknown.value)
    with pytest.raises(RecordingError, match="ambiguous"):
        resolve_recording(T0.strftime("%Y-%m-%d"))
    with pytest.raises(RecordingError, match="Empty reference"):
        resolve_recording("  ")


def test_load_recording_validation_failures() -> None:
    """Damaged parts of a record fail loudly with their path."""
    ref = "2026-10-05T20-00-00.000000Z"
    _write_meta(ref)
    with pytest.raises(RecordingError, match=r"state-start\.toml is missing"):
        load_recording(ref)
    _write_start_snapshot(ref)
    assert load_recording(ref).meta.status == STATUS_OPEN

    _write_meta(ref, ref="other", status=STATUS_OPEN)
    with pytest.raises(RecordingError, match="does not match the directory"):
        load_recording(ref)

    _write_meta(ref, status=STATUS_STOPPED, stopped_at=T1.isoformat())
    with pytest.raises(RecordingError, match=r"without state-end\.toml"):
        load_recording(ref)

    _write_meta(ref, status="broken")
    with pytest.raises(RecordingError, match="unknown status"):
        load_recording(ref)

    _write_meta(ref, schema_version=99)
    with pytest.raises(RecordingError, match="unsupported schema_version"):
        load_recording(ref)


def test_unexpected_entries_fail_loudly() -> None:
    """Stray files in the recording space are never skipped silently."""
    get_recordings_dir().mkdir(parents=True)
    (get_recordings_dir() / "stray.txt").write_text("x")
    with pytest.raises(RecordingError, match="Unexpected entry"):
        list_recordings()


def test_read_functions_never_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loading, listing, resolving and reading leave the space byte-identical."""
    _freeze(monkeypatch, T0)
    start_recording(_state())
    ref = next(entry.ref for entry in list_recordings())
    _append_segment(
        ref,
        "100-1-1.log",
        [_header(epoch=T0.timestamp()), f"cmd\t{T0.timestamp()}\t/tmp\techo\\ hi"],
    )
    before = _manifest(get_recordings_dir())

    entries = list_recordings()
    load_recording(ref)
    resolve_recording("latest")
    active_recording()
    load_events(entries[0])
    read_events(get_recordings_dir() / ref / "events" / "100-1-1.log")
    recording.inspect_space()

    assert _manifest(get_recordings_dir()) == before


# --- events ------------------------------------------------------------------


def test_read_events_parses_every_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    """All five event kinds decode into their typed fields."""
    _freeze(monkeypatch, T0)
    start_recording(_state())
    ref = next(entry.ref for entry in list_recordings())
    path = _append_segment(
        ref,
        "100-1-1.log",
        [
            _header(epoch=T0.timestamp()),
            f"cmd\t{T0.timestamp()}\t/home/frank\tls\\ -la",
            f"result\t{T0.timestamp()}\t1\t100.5\t250.25",
            f"comment\t{T0.timestamp()}\t/tmp\t\\#\\ note",
            f"exit\t{T0.timestamp()}\t130",
        ],
    )

    events = read_events(path)

    kinds = [event.kind for event in events]
    assert kinds == ["segment", "cmd", "result", "comment", "exit"]
    header, cmd, result, comment, exit_event = events
    assert header.boot_id and header.pid == os.getpid() and header.start_ticks
    assert cmd.cwd == "/home/frank" and cmd.text == "ls -la"
    assert result.status == 1 and result.t0 == 100.5 and result.t1 == 250.25
    assert result.executed_seconds == pytest.approx(149.75)
    assert comment.text == "# note"
    assert exit_event.status == 130


def test_segment_statistics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Segment infos count events, sum executed time and expose the exit hint."""
    _freeze(monkeypatch, T0)
    start_recording(_state())
    ref = next(entry.ref for entry in list_recordings())
    _append_segment(
        ref,
        "100-1-1.log",
        [
            _header(epoch=T0.timestamp()),
            f"cmd\t{T0.timestamp()}\t/tmp\techo\\ a",
            f"result\t{T0.timestamp()}\t0\t10.0\t11.5",
            f"cmd\t{T0.timestamp()}\t/tmp\techo\\ b",
            f"result\t{T0.timestamp()}\t0\t20.0\t20.25",
            f"exit\t{T0.timestamp()}\t0",
        ],
    )

    segment = load_recording(ref).segments[0]

    assert segment.commands == 2
    assert segment.results == 2
    assert segment.exits == 1
    assert segment.last_exit_status == 0
    assert segment.executed_seconds == pytest.approx(1.75)
    assert segment.alive is True
    assert segment.end_reason == ""


def test_load_events_orders_by_segment_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Events are ordered by segment start, then time and line."""
    _freeze(monkeypatch, T0)
    start_recording(_state())
    ref = next(entry.ref for entry in list_recordings())
    _append_segment(ref, "200-1-1.log", [_header(epoch=T1.timestamp()), f"cmd\t{T1.timestamp()}\t/tmp\tsecond"])
    _append_segment(ref, "100-1-1.log", [_header(epoch=T0.timestamp()), f"cmd\t{T0.timestamp()}\t/tmp\tfirst"])

    events = load_events(load_recording(ref))

    assert [event.text for event in events if event.kind == "cmd"] == ["first", "second"]
    assert [event.kind for event in events] == ["segment", "cmd", "segment", "cmd"]


def test_damaged_segment_line_fails_with_path_and_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed event line names the file and the line number."""
    _freeze(monkeypatch, T0)
    start_recording(_state())
    ref = next(entry.ref for entry in list_recordings())
    path = _append_segment(ref, "100-1-1.log", [_header(), "cmd\tonly-two-fields", "garbage"])

    with pytest.raises(RecordingError, match=f"'{path}' line 2") as excinfo:
        read_events(path)
    assert "cmd event must carry" in str(excinfo.value)

    with pytest.raises(RecordingError, match="unknown event kind"):
        read_events(_append_segment(ref, "103-1-1.log", ["not-a-header"]))

    _append_segment(ref, "101-1-1.log", [f"cmd\t{T0.timestamp()}\t/tmp\techo\\ hi"])
    with pytest.raises(RecordingError, match="first line must be the segment header"):
        read_events(path.parent / "101-1-1.log")

    empty = path.parent / "102-1-1.log"
    empty.write_text("")
    with pytest.raises(RecordingError, match="empty segment file"):
        read_events(empty)


# --- liveness ----------------------------------------------------------------


def test_liveness_alive_gone_reused_reboot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Liveness needs boot_id, PID and start ticks to match together."""
    _freeze(monkeypatch, T0)
    start_recording(_state())
    ref = next(entry.ref for entry in list_recordings())
    ticks = _current_start_ticks(os.getpid())
    _append_segment(ref, "100-1-1.log", [_header(pid=os.getpid(), ticks=ticks)])
    _append_segment(ref, "101-1-1.log", [_header(boot_id="00000000-0000-0000-0000-000000000000")])
    _append_segment(ref, "102-1-1.log", [_header(pid=999999999, ticks=1)])
    _append_segment(ref, "103-1-1.log", [_header(pid=os.getpid(), ticks=ticks + 1)])

    infos = {info.name: info for info in load_recording(ref).segments}

    assert infos["100-1-1.log"].alive is True
    assert infos["100-1-1.log"].end_reason == ""
    assert infos["101-1-1.log"].alive is False
    assert infos["101-1-1.log"].end_reason == "reboot"
    assert infos["102-1-1.log"].alive is False
    assert infos["102-1-1.log"].end_reason == "gone"
    assert infos["103-1-1.log"].alive is False
    assert infos["103-1-1.log"].end_reason == "reused"


def test_superseded_takeover_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """An older segment with the same process stamp is superseded, not alive."""
    _freeze(monkeypatch, T0)
    start_recording(_state())
    ref = next(entry.ref for entry in list_recordings())
    ticks = _current_start_ticks(os.getpid())
    _append_segment(ref, "100-1-1.log", [_header(pid=os.getpid(), ticks=ticks, epoch=T0.timestamp())])
    _append_segment(ref, "101-1-1.log", [_header(pid=os.getpid(), ticks=ticks, epoch=T1.timestamp())])

    infos = {info.name: info for info in load_recording(ref).segments}

    assert infos["100-1-1.log"].alive is False
    assert infos["100-1-1.log"].end_reason == "superseded"
    assert infos["101-1-1.log"].alive is True
    assert infos["101-1-1.log"].end_reason == ""


def test_superseded_stays_after_process_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """A takeover chain keeps ``superseded`` stable after the process ends."""
    _freeze(monkeypatch, T0)
    start_recording(_state())
    ref = next(entry.ref for entry in list_recordings())
    _append_segment(ref, "100-1-1.log", [_header(pid=999999999, ticks=1, epoch=T0.timestamp())])
    _append_segment(ref, "101-1-1.log", [_header(pid=999999999, ticks=1, epoch=T1.timestamp())])

    infos = {info.name: info for info in load_recording(ref).segments}

    assert infos["100-1-1.log"].alive is False
    assert infos["100-1-1.log"].end_reason == "superseded"
    assert infos["101-1-1.log"].alive is False
    assert infos["101-1-1.log"].end_reason == "gone"


def test_missing_exit_is_not_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A segment without exit stays open while alive and ends by liveness otherwise."""
    _freeze(monkeypatch, T0)
    start_recording(_state())
    ref = next(entry.ref for entry in list_recordings())
    _append_segment(ref, "100-1-1.log", [_header(), f"cmd\t{T0.timestamp()}\t/tmp\techo\\ hi"])

    segment = load_recording(ref).segments[0]
    assert segment.exits == 0
    assert segment.alive is True
    assert load_recording(ref).active is True


# --- unquote -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("quoted", "decoded"),
    [
        ("a\\ b", "a b"),
        ("a\\|b", "a|b"),
        ("a\\;b", "a;b"),
        ("a$'\\n'b", "a\nb"),
        ("x$'\\t'y", "x\ty"),
        ("$'\\033'[1m", "\x1b[1m"),
        ("low$'\\037'$'\\377'", "low\x1f\u00ff"),
        ("nl\\$\\'quote", "nl$'quote"),
        ("a\\#b\\*c", "a#b*c"),
        ("''", ""),
        ("plain", "plain"),
        ("\\", "\\"),
        ("abc\\", "abc\\"),
        ("abc$'\\n", "abc$'\\n"),
        ("cap…<+5906B>\\ end", "cap…<+5906B> end"),
    ],
)
def test_unquote_zsh(quoted: str, decoded: str) -> None:
    """The decoder reverses the zsh ``${(q)}`` forms and tolerates truncation."""
    assert unquote_zsh(quoted) == decoded


def test_unquote_zsh_dollar_escapes() -> None:
    """Single-letter and hexadecimal escapes inside ``$'...'`` decode fully."""
    assert unquote_zsh("$'\\a\\b\\f\\v\\e'") == "\a\b\f\v\x1b"
    assert unquote_zsh("$'\\\\'") == "\\"
    assert unquote_zsh("$'\\x41\\u0042'") == "AB"


# --- doctor ------------------------------------------------------------------


def test_watch_checks_skip_without_records() -> None:
    """No recordings is a neutral skip."""
    findings = recording.watch_checks(DoctorContext(state={}, home=Path("/nonexistent")))
    assert [finding.status for finding in findings] == [CheckStatus.SKIP]
    assert not get_recordings_dir().exists()


def test_watch_checks_active_and_hanging_pointer(monkeypatch: pytest.MonkeyPatch) -> None:
    """An active recording is ok; a pointer to a missing record warns."""
    _freeze(monkeypatch, T0)
    start_recording(_state())

    findings = recording.watch_checks(DoctorContext(state={}, home=Path("/nonexistent")))
    assert CheckStatus.OK in {finding.status for finding in findings}
    assert any(finding.name == "watch.active" for finding in findings)

    get_active_pointer().write_text("1999-01-01T00-00-00.000000Z\n")
    findings = recording.watch_checks(DoctorContext(state={}, home=Path("/nonexistent")))
    assert any(finding.name == "watch.pointer" and finding.status == CheckStatus.WARN for finding in findings)


def test_watch_checks_damaged_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """A damaged recording is an error naming its path."""
    ref = "2026-10-05T20-00-00.000000Z"
    _write_meta(ref)
    findings = recording.watch_checks(DoctorContext(state={}, home=Path("/nonexistent")))
    assert any(finding.name == "watch.record" and finding.status == CheckStatus.ERROR for finding in findings)
    assert any(str(get_recordings_dir() / ref) in finding.message for finding in findings)
