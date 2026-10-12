"""Tests for the ``lion records`` command.

The recording space is isolated via ``HOME``/``XDG_DATA_HOME``; recordings are
created through the real recording API and synthetic segment files, so the
tests stay deterministic and never touch the developer's machine.
"""

import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner, Result

from lion.cli import app
from lion.program import annotations, recording

runner = CliRunner()

HOST_STATE: dict[str, dict[str, object]] = {"host": {"status": "ok", "error": "", "hostname": "lion"}}

_SPECIALS = set(" \t#$:;'\"`(){}[]!&|<>*?~\\")

COMPLETE_ADR: list[tuple[str, str]] = [
    ("comment", "#+LION: adr.title Antidote for the shell"),
    ("cmd", "echo anchor-me"),
    ("comment", "#+LION: adr.component shell"),
    ("comment", "#-LION: adr.decision Load plugins through Antidote."),
    ("comment", "#.LION: adr.status accepted"),
]


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Disposable home and data directories."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("ZDOTDIR", raising=False)


def _invoke(*args: str) -> Result:
    return runner.invoke(app, list(args))


def _quote(text: str) -> str:
    """Encode a value like zsh's ``${(q)}`` for a spool line field."""
    parts: list[str] = []
    for char in text:
        if char == "\n":
            parts.append("$'\\n'")
        elif char == "\t":
            parts.append("$'\\t'")
        elif char in _SPECIALS:
            parts.append("\\" + char)
        else:
            parts.append(char)
    return "".join(parts)


def _start(note: str = "") -> recording.Recording:
    return recording.start_recording(dict(HOST_STATE), note)


def _header(epoch: float) -> str:
    boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    stat = Path(f"/proc/{os.getpid()}/stat").read_text(encoding="utf-8")
    ticks = int(stat.rsplit(")", 1)[-1].split()[19])
    return f"segment\t{boot}\t{os.getpid()}\t{ticks}\t{epoch}"


def _record(events: Sequence[tuple[str, str]], *, stop: bool = True) -> str:
    """Create a recording whose single segment carries the given events."""
    record = _start()
    base = datetime.now(UTC).timestamp() - 60
    lines = [_header(base)]
    lines.extend(f"{kind}\t{base + index + 1}\t/tmp\t{_quote(text)}" for index, (kind, text) in enumerate(events))
    directory = recording.get_recordings_dir() / record.ref / "events"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "100-1-1.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if stop:
        _ = recording.stop_recording(dict(HOST_STATE))
    return record.ref


# --- records status ----------------------------------------------------------


def test_records_bare_runs_status() -> None:
    """Bare ``lion records`` is the status; an empty space is exit 0."""
    result = _invoke("records")

    assert result.exit_code == 0
    assert "No active recording." in result.stdout
    assert "Recordings: 0 (0 open)." in result.stdout
    assert "ADRs: 0 total" in result.stdout

    payload = json.loads(_invoke("records", "status", "--json").stdout)
    empty_status = {"proposed": 0, "accepted": 0, "superseded": 0}
    assert payload == {
        "active": None,
        "recordings": 0,
        "open": 0,
        "adrs": {"total": 0, "complete": 0, "incomplete": 0, "edited": 0, "status": empty_status},
    }


def test_records_status_counts_and_active() -> None:
    """The status reports the active record and the ADR distribution."""
    _record([("comment", "#-LION: adr.title Only title")])
    _record(COMPLETE_ADR)
    active = _start("live")
    payload = json.loads(_invoke("records", "status", "--json").stdout)

    assert payload["recordings"] == 3
    assert payload["open"] == 1
    assert payload["active"]["ref"] == active.ref
    assert payload["active"]["note"] == "live"
    assert payload["active"]["segments"] == 0
    assert payload["adrs"] == {
        "total": 2,
        "complete": 1,
        "incomplete": 1,
        "edited": 0,
        "status": {"proposed": 1, "accepted": 1, "superseded": 0},
    }

    text = _invoke("records", "status").stdout
    assert f"Active recording {active.ref}" in text
    assert "ADRs: 2 total, 1 complete, 1 incomplete, 0 edited." in text
    assert "Status: proposed 1, accepted 1, superseded 0." in text


def test_records_status_never_collects(monkeypatch: pytest.MonkeyPatch) -> None:
    """The status reads recordings only; a collector failure cannot happen."""
    broken = Mock(side_effect=AssertionError("collect must not run"))
    monkeypatch.setattr("lion.state.collector.collect_state", broken)

    assert _invoke("records", "status").exit_code == 0
    assert broken.call_count == 0


# --- records list ------------------------------------------------------------


def test_records_list_text_has_adr_column() -> None:
    """The listing extends the watch list with the ADR state."""
    plain = _record([("cmd", "echo hi")])
    accepted = _record(COMPLETE_ADR)
    incomplete = _record([("comment", "#-LION: adr.title only a title")])
    result = _invoke("records", "list")

    assert result.exit_code == 0
    rows = {line.split()[1]: line for line in result.stdout.splitlines()[1:]}
    assert rows[plain].endswith("  -")
    assert rows[accepted].endswith("  accepted")
    assert rows[incomplete].endswith("  incomplete")


def test_records_list_json_adds_adr_object() -> None:
    """Each record keeps the watch JSON shape and gains an ``adr`` object."""
    _record([("cmd", "echo hi")])
    ref = _record(COMPLETE_ADR)
    payload = json.loads(_invoke("records", "list", "--json").stdout)["recordings"]

    assert len(payload) == 2
    watch_keys = {
        "ref",
        "path",
        "status",
        "started_at",
        "stopped_at",
        "note",
        "integration_version",
        "segments",
    }
    assert watch_keys <= set(payload[0])
    assert payload[0]["adr"] is None
    by_ref = {item["ref"]: item for item in payload}
    assert by_ref[ref]["adr"] == {
        "status": "accepted",
        "complete": True,
        "edited": False,
        "title": "Antidote for the shell",
    }


# --- records adrs (list and detail) ------------------------------------------


def test_records_adrs_list() -> None:
    """Without a reference the command lists every ADR."""
    _record([("cmd", "echo hi")])
    ref = _record(COMPLETE_ADR)
    result = _invoke("records", "adrs")

    assert result.exit_code == 0
    assert "No ADRs recorded." not in result.stdout
    row = next(line for line in result.stdout.splitlines() if ref in line)
    assert "accepted" in row
    assert "Antidote for the shell" in row

    payload = json.loads(_invoke("records", "adrs", "--json").stdout)["adrs"]
    assert payload == [
        {
            "ref": ref,
            "status": "accepted",
            "complete": True,
            "edited": False,
            "title": "Antidote for the shell",
            "started_at": recording.load_recording(ref).meta.started_at,
        }
    ]


def test_records_adrs_empty_list() -> None:
    """An empty ADR list is exit 0 in text and JSON."""
    assert "No ADRs recorded." in _invoke("records", "adrs").stdout
    assert json.loads(_invoke("records", "adrs", "--json").stdout) == {"adrs": []}


def test_records_adrs_detail_text() -> None:
    """The detail view shows fields, anchor and the derived metadata."""
    ref = _record([*COMPLETE_ADR, ("comment", "#.LION: adr.uses 2026-01-01T00-00-00.000000Z")])
    result = _invoke("records", "adrs", ref)

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert lines[0] == f"ADR {ref} (accepted, complete)"
    order = [line.split(":")[0].strip() for line in lines[1:] if line.startswith("  ") and ":" in line]
    assert order[:3] == ["title", "decision", "component"]
    assert "  anchor: next command in 100-1-1.log" in result.stdout
    assert "    $ echo anchor-me" in result.stdout
    assert "  host: lion" in result.stdout
    assert "  edited: false" in result.stdout
    assert "  uses: 2026-01-01T00-00-00.000000Z -> (unknown)" in result.stdout


def test_records_adrs_detail_json_contract() -> None:
    """The detail JSON carries fields, anchor, auto data, refs and issues."""
    ref = _record(COMPLETE_ADR)
    payload = json.loads(_invoke("records", "adrs", ref, "--json").stdout)["adr"]

    assert set(payload) == {
        "ref",
        "fields",
        "status",
        "complete",
        "edited",
        "anchor",
        "auto",
        "uses",
        "supersedes",
        "issues",
    }
    assert payload["ref"] == ref
    assert list(payload["fields"]) == ["title", "decision", "component", "status"]
    assert payload["anchor"]["kind"] == "next"
    assert payload["anchor"]["text"] == "echo anchor-me"
    assert set(payload["auto"]) == {"first_at", "last_at", "user", "host", "cwd", "segments"}
    assert payload["auto"]["host"] == "lion"
    assert payload["auto"]["segments"] == ["100-1-1.log"]
    assert payload["issues"] == []


def test_records_adrs_resolves_uses_against_recordings() -> None:
    """``uses`` resolves exact/prefix references and marks unknown ones."""
    first = _record([("comment", "#-LION: adr.title First")])
    prefix = first[:-1]
    second = _record(
        [
            ("comment", "#-LION: adr.title Second"),
            ("comment", f"#.LION: adr.uses {prefix}, does-not-exist"),
            ("comment", f"#.LION: adr.supersedes {first}"),
        ]
    )
    result = _invoke("records", "adrs", second)

    assert f"{prefix} -> {first}" in result.stdout
    assert "does-not-exist -> (unknown)" in result.stdout
    assert f"supersedes: {first} -> {first}" in result.stdout

    payload = json.loads(_invoke("records", "adrs", second, "--json").stdout)["adr"]
    assert payload["uses"] == [
        {"ref": prefix, "resolved": first},
        {"ref": "does-not-exist", "resolved": None},
    ]


def test_records_adrs_without_adr() -> None:
    """A record without fields is a message, not an error; issues stay visible."""
    plain = _record([("cmd", "echo hi")])
    result = _invoke("records", "adrs", plain)

    assert result.exit_code == 0
    assert f"No ADR in recording {plain}." in result.stdout
    assert json.loads(_invoke("records", "adrs", plain, "--json").stdout) == {"adr": None, "issues": []}

    unresolved = _record([("comment", "#-LION: junk")])
    result = _invoke("records", "adrs", unresolved)
    assert result.exit_code == 0
    assert f"No ADR in recording {unresolved}." in result.stdout
    assert "Unresolved:" in result.stdout
    assert "unknown annotation form" in result.stdout
    payload = json.loads(_invoke("records", "adrs", unresolved, "--json").stdout)
    assert payload["adr"] is None
    assert payload["issues"][0]["reason"] == "unknown annotation form"


def test_records_reference_resolution_and_failures() -> None:
    """Prefixes resolve; unknown references and damaged records fail loudly."""
    ref = _record(COMPLETE_ADR)
    assert _invoke("records", "adrs", ref[:16], "--json").exit_code == 0
    assert _invoke("records", "adrs", "latest", "--json").exit_code == 0

    unknown = _invoke("records", "adrs", "does-not-exist")
    assert unknown.exit_code == 1
    assert "Unknown reference" in unknown.stderr

    record_dir = recording.get_recordings_dir() / "2026-10-05T20-00-00.000000Z"
    record_dir.mkdir(parents=True)
    (record_dir / "meta.toml").write_text("broken = [")
    for args in (("records", "status"), ("records", "list"), ("records", "adrs")):
        result = _invoke(*args)
        assert result.exit_code == 1, args
        assert "meta.toml" in result.stderr, args


def test_records_read_commands_never_write() -> None:
    """status, list and adrs leave the data directory byte-identical."""
    _record(COMPLETE_ADR)
    _record([("comment", "#-LION: adr.title Two")])
    data = Path(os.environ["XDG_DATA_HOME"])
    before = {
        path.relative_to(data).as_posix(): path.read_bytes() for path in sorted(data.rglob("*")) if path.is_file()
    }

    assert _invoke("records", "status").exit_code == 0
    assert _invoke("records", "status", "--json").exit_code == 0
    assert _invoke("records", "list").exit_code == 0
    assert _invoke("records", "list", "--json").exit_code == 0
    assert _invoke("records", "adrs").exit_code == 0
    assert _invoke("records", "adrs", "latest").exit_code == 0
    assert _invoke("records", "adrs", "latest", "--json").exit_code == 0

    after = {path.relative_to(data).as_posix(): path.read_bytes() for path in sorted(data.rglob("*")) if path.is_file()}
    assert after == before


def test_records_help_documents_grammar() -> None:
    """``lion records --help`` explains the markers and the default action."""
    result = _invoke("records", "--help")

    assert result.exit_code == 0
    assert "#-LION:" in result.stdout
    assert "#.LION:" in result.stdout
    assert "#+LION:" in result.stdout


# --- overlay and edit --------------------------------------------------------


def _overlay_path(ref: str) -> Path:
    return recording.load_recording(ref).path / annotations.OVERLAY_FILENAME


def _edit_script(tmp_path: Path, body: str) -> str:
    """Write an executable $EDITOR stub that runs ``body`` after reading argv[1]."""
    script = tmp_path / "editor_stub.py"
    script.write_text(
        "import pathlib, sys\npath = pathlib.Path(sys.argv[1])\ntext = path.read_text()\n" + body,
        encoding="utf-8",
    )
    script.chmod(0o755)
    return f"{sys.executable} {script}"


def test_overlay_replaces_event_fields(tmp_path: Path) -> None:
    """A valid overlay beats observation and flips the edited flag."""
    ref = _record(COMPLETE_ADR)
    _ = annotations.write_overlay(
        recording.load_recording(ref).path,
        {"title": "Edited title", "decision": "Edited decision", "status": "superseded", "extra": "kept"},
    )

    payload = json.loads(_invoke("records", "adrs", ref, "--json").stdout)["adr"]

    assert payload["edited"] is True
    assert payload["status"] == "superseded"
    assert list(payload["fields"]) == ["title", "decision", "status", "extra"]
    assert payload["fields"]["title"] == "Edited title"
    assert payload["issues"] == []

    text = _invoke("records", "adrs", ref).stdout
    assert "  edited: true" in text
    by_ref = {item["ref"]: item for item in json.loads(_invoke("records", "list", "--json").stdout)["recordings"]}
    assert by_ref[ref]["adr"]["edited"] is True
    assert by_ref[ref]["adr"]["title"] == "Edited title"


def test_overlay_unknown_status_is_reported() -> None:
    """An unknown overlay status stays verbatim and becomes a visible finding."""
    ref = _record([("comment", "#-LION: adr.title T")])
    _ = annotations.write_overlay(recording.load_recording(ref).path, {"title": "T", "status": "bogus"})

    payload = json.loads(_invoke("records", "adrs", ref, "--json").stdout)["adr"]

    assert payload["status"] == "bogus"
    assert [issue["reason"] for issue in payload["issues"]] == ["unknown status value"]
    assert payload["issues"][0]["segment"] == annotations.OVERLAY_FILENAME


def test_overlay_empty_fields_means_no_adr() -> None:
    """An empty [fields] table deliberately removes the ADR."""
    ref = _record(COMPLETE_ADR)
    _ = annotations.write_overlay(recording.load_recording(ref).path, {})

    result = _invoke("records", "adrs", ref)
    assert result.exit_code == 0
    assert f"No ADR in recording {ref}." in result.stdout
    assert json.loads(_invoke("records", "adrs", ref, "--json").stdout)["adr"] is None
    by_ref = {item["ref"]: item for item in json.loads(_invoke("records", "list", "--json").stdout)["recordings"]}
    assert by_ref[ref]["adr"] is None


def test_invalid_overlay_fails_loudly() -> None:
    """Every broken overlay fails with exit 1 naming the file, never skipped."""
    ref = _record(COMPLETE_ADR)
    path = _overlay_path(ref)
    cases = [
        "broken = [",
        'schema_version = 2\n[fields]\ntitle = "T"\n',
        "schema_version = 1\n",
        'schema_version = 1\n[fields]\ntitle = ""\n',
    ]
    for content in cases:
        path.write_text(content, encoding="utf-8")
        for args in (("records", "list"), ("records", "adrs", ref)):
            result = _invoke(*args)
            assert result.exit_code == 1, (content, args)
            assert annotations.OVERLAY_FILENAME in result.stderr, (content, args)
    path.unlink()


def test_edit_writes_overlay_and_cleans_the_draft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The editor flow validates, stores atomically and removes the draft."""
    ref = _record(COMPLETE_ADR)
    document = 'schema_version = 1\\n\\n[fields]\\ntitle = "Edited title"\\ndecision = "Edited decision"\\n'
    monkeypatch.setenv(
        "EDITOR",
        _edit_script(
            tmp_path,
            "assert 'schema_version = 1' in text, text\n"
            "assert 'Antidote for the shell' in text, text\n"
            f"path.write_text('{document}')\n",
        ),
    )

    result = _invoke("records", "edit", ref)

    assert result.exit_code == 0
    assert "ADR overlay written" in result.stdout
    path = _overlay_path(ref)
    assert path.is_file()
    assert (path.stat().st_mode & 0o777) == 0o600
    record_dir = recording.load_recording(ref).path
    assert not list(record_dir.glob(".adr-edit-*"))
    payload = json.loads(_invoke("records", "adrs", ref, "--json").stdout)["adr"]
    assert payload["edited"] is True
    assert payload["fields"]["title"] == "Edited title"


def test_edit_defaults_to_active_recording(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without REF edit targets the active recording and refuses to touch it."""
    monkeypatch.setenv("EDITOR", _edit_script(tmp_path, "path.write_text('')\n"))
    _record(COMPLETE_ADR)
    _start("open")

    result = _invoke("records", "edit")

    assert result.exit_code == 1
    assert "still open" in result.stderr
    assert not list(recording.get_recordings_dir().rglob(annotations.OVERLAY_FILENAME))


def test_edit_requires_editor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing $EDITOR is a loud error and changes nothing."""
    monkeypatch.delenv("EDITOR", raising=False)
    ref = _record(COMPLETE_ADR)

    result = _invoke("records", "edit", ref)

    assert result.exit_code == 1
    assert "EDITOR" in result.stderr
    assert not _overlay_path(ref).exists()
    assert not list(recording.load_recording(ref).path.glob(".adr-edit-*"))


def test_edit_editor_failure_keeps_the_draft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing editor aborts loudly and leaves the draft for reconciliation."""
    monkeypatch.setenv("EDITOR", _edit_script(tmp_path, "sys.exit(3)\n"))
    ref = _record(COMPLETE_ADR)

    result = _invoke("records", "edit", ref)

    assert result.exit_code == 1
    assert "exited with status 3" in result.stderr
    drafts = list(recording.load_recording(ref).path.glob(".adr-edit-*"))
    assert len(drafts) == 1
    assert str(drafts[0]) in result.stderr
    assert not _overlay_path(ref).exists()


def test_edit_unrunnable_editor_names_the_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    """An $EDITOR that cannot be executed keeps the draft and says so."""
    monkeypatch.setenv("EDITOR", "/nonexistent/editor-binary")
    ref = _record(COMPLETE_ADR)

    result = _invoke("records", "edit", ref)

    assert result.exit_code == 1
    assert "Cannot run $EDITOR" in result.stderr
    drafts = list(recording.load_recording(ref).path.glob(".adr-edit-*"))
    assert len(drafts) == 1
    assert str(drafts[0]) in result.stderr
    assert not _overlay_path(ref).exists()


def test_edit_invalid_content_keeps_the_draft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid edited content fails with the draft path and writes no overlay."""
    monkeypatch.setenv("EDITOR", _edit_script(tmp_path, "path.write_text('broken = [')\n"))
    ref = _record(COMPLETE_ADR)

    result = _invoke("records", "edit", ref)

    assert result.exit_code == 1
    assert "Invalid ADR overlay" in result.stderr
    drafts = list(recording.load_recording(ref).path.glob(".adr-edit-*"))
    assert len(drafts) == 1
    assert str(drafts[0]) in result.stderr
    assert not _overlay_path(ref).exists()


def test_edit_rejects_json_option() -> None:
    """``edit`` has no JSON mode (typer rejects the option)."""
    result = _invoke("records", "edit", "--json")

    assert result.exit_code == 2
