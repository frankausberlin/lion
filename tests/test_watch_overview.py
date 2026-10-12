"""Tests for the terse ``lion watch R``/``R-`` reads of the active recording."""

import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner, Result

from lion.cli import app
from lion.command import watch as watch_command
from lion.program import recording, shell, shlib

runner = CliRunner()

HOST_STATE: dict[str, dict[str, object]] = {"host": {"status": "ok", "error": "", "hostname": "lion"}}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Disposable home, data and config directories with mocked syntax checks."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("ZDOTDIR", raising=False)
    monkeypatch.setattr(shell, "validate_zsh", Mock())
    monkeypatch.setattr(shlib, "_validate", Mock())


@pytest.fixture
def collect(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Replace the collector collection with a stub to prove R never collects."""
    fake = Mock(return_value=dict(HOST_STATE))
    monkeypatch.setattr(watch_command, "collect_state", fake)
    return fake


def _invoke(*args: str) -> Result:
    return runner.invoke(app, list(args))


def _quote_zsh(text: str) -> str:
    parts: list[str] = []
    for char in text:
        if char == "\n":
            parts.append("$'\\n'")
        elif char in "\\'\" $`()|&;<>":
            parts.append("\\" + char)
        else:
            parts.append(char)
    return "".join(parts)


def _segment_header(epoch: str) -> str:
    boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    stat = Path(f"/proc/{os.getpid()}/stat").read_text(encoding="utf-8")
    ticks = int(stat.rsplit(")", 1)[-1].split()[19])
    return f"segment\t{boot}\t{os.getpid()}\t{ticks}\t{epoch}"


def _append_events(ref: str, events: list[tuple[str, str]]) -> None:
    """Append ``(kind, text)`` events to the recording's single segment."""
    epoch = datetime.now(UTC).timestamp()
    lines = [_segment_header(str(epoch))]
    for kind, text in events:
        lines.append(f"{kind}\t{epoch}\t/tmp\t{_quote_zsh(text)}")
    events_dir = recording.get_recordings_dir() / ref / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    (events_dir / "100-1-1.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _started(events: list[tuple[str, str]]) -> str:
    record = recording.start_recording(dict(HOST_STATE))
    _append_events(record.ref, events)
    return record.ref


def test_watch_r_without_active_recording(collect: Mock) -> None:
    """Both terse reads report the missing active recording and exit 0."""
    for action in ("R", "R-"):
        result = _invoke("watch", action)
        assert result.exit_code == 0
        assert "No active recording." in result.stdout


def test_watch_r_overview_shows_adr_open_fields_and_blocks(collect: Mock) -> None:
    """R lists the set ADR fields, the open catalog and the numbered blocks."""
    ref = _started(
        [
            ("comment", "#+LION: adr.title complex-stuff-decision"),
            ("comment", "#+LION: adr.component network"),
            ("cmd", "sudo nala install foo"),
            ("cmd", "lion watch R"),
            ("cmd", 'echo "sudo nala install"'),
            ("cmd", "lion watch R-"),
        ]
    )

    result = _invoke("watch", "R")

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert lines[0] == f"Recording {ref}"
    assert lines[1] == "  adr: title=complex-stuff-decision, component=network"
    assert lines[2].startswith("  open: ")
    assert "title" not in lines[2]
    assert "component" not in lines[2]
    assert "decision" in lines[2]
    assert "status" in lines[2]
    assert "R1: sudo nala install foo  packages:manager=nala,action=install" in lines
    assert 'R2: echo "sudo nala install"  -' in lines
    assert not any(line.startswith("R3") for line in lines)


def test_watch_r_previous_shows_exactly_the_last_block(collect: Mock) -> None:
    """R- prints one line for the block before the silent reads."""
    _started(
        [
            ("cmd", "sudo nala install foo"),
            ("cmd", "echo done"),
            ("cmd", "lion watch R-"),
        ]
    )

    result = _invoke("watch", "R-")

    assert result.exit_code == 0
    assert result.stdout == "R2: echo done  -\n"


def test_watch_r_without_blocks_reports_nothing_to_show(collect: Mock) -> None:
    """An empty recording shows the full open catalog; R- reports no block."""
    _started([])

    overview = _invoke("watch", "R")
    assert overview.exit_code == 0
    assert "  adr: -" in overview.stdout
    assert (
        "  open: title, decision, component, scope, context, consequences, alternatives, "
        "revisit_when, uses, supersedes, status" in overview.stdout
    )

    previous = _invoke("watch", "R-")
    assert previous.exit_code == 0
    assert previous.stdout == "No previous block.\n"


def test_watch_r_preview_collapses_and_truncates_to_40_characters(collect: Mock) -> None:
    """The preview is single-line, whitespace-collapsed and cut at 40 chars."""
    _started([("cmd", "y" * 60), ("cmd", "first line\nsecond line")])

    result = _invoke("watch", "R")

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert f"R1: {'y' * 40}  -" in lines
    assert "R2: first line second line  -" in lines


def test_watch_r_filters_silent_reads_from_the_numbering(collect: Mock) -> None:
    """R, R- and empty commands are not blocks; comments are not classified."""
    _started(
        [
            ("cmd", "lion watch R"),
            ("cmd", "apt --version"),
            ("comment", "# just a note"),
            ("cmd", ""),
            ("cmd", "uv run lion watch R-"),
        ]
    )

    result = _invoke("watch", "R")

    assert result.exit_code == 0
    assert "R1: apt --version  checks:subject=apt,intent=version" in result.stdout
    assert not any(line.startswith("R2") for line in result.stdout.splitlines())


def test_watch_r_and_previous_never_collect(collect: Mock) -> None:
    """Both reads are pure readers: the collector is never called."""
    _started([("cmd", "apt --version")])

    assert _invoke("watch", "R").exit_code == 0
    assert _invoke("watch", "R-").exit_code == 0
    collect.assert_not_called()


def test_watch_r_sanitizes_control_characters(collect: Mock) -> None:
    """Recorded ESC/BEL bytes in text and ADR fields never reach the terminal."""
    _started(
        [
            ("comment", "#+LION: adr.title \x1b]0;evil\x07title"),
            ("cmd", "apt --version \x1b[2J"),
        ]
    )

    result = _invoke("watch", "R")

    assert result.exit_code == 0
    assert "\x1b" not in result.stdout
    assert "\x07" not in result.stdout
    assert "title" in result.stdout


def test_watch_r_loads_the_events_once(collect: Mock, monkeypatch: pytest.MonkeyPatch) -> None:
    """R reads the recording once and feeds the same events to ADR and blocks."""
    ref = _started([("cmd", "apt --version")])
    real = recording.load_events
    calls: list[str] = []

    def counting(record: recording.Recording) -> list[recording.SpoolEvent]:
        calls.append(record.ref)
        return real(record)

    monkeypatch.setattr(recording, "load_events", counting)

    assert _invoke("watch", "R").exit_code == 0
    assert calls == [ref]


def test_watch_r_is_text_only(collect: Mock) -> None:
    """``--json`` on R is a usage error; the JSON contracts stay untouched."""
    _started([("cmd", "apt --version")])

    result = _invoke("watch", "R", "--json")
    assert result.exit_code == 2


def test_watch_r_ignores_state_and_shows_stored_text(collect: Mock) -> None:
    """R never collects, so it keeps working while the machine would differ."""
    _started([("cmd", "sudo nala install foo")])

    result = _invoke("watch", "R")

    assert result.exit_code == 0
    assert "R1: sudo nala install foo" in result.stdout
