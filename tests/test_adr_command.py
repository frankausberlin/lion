"""Tests for the ``lion adr`` command.

The store is isolated via ``HOME``/``XDG_DATA_HOME`` and documents are created
through the CLI, so the tests stay deterministic and never touch the developer's
machine. Read commands must never write.
"""

import json
import os
import re
import stat
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner, Result

from lion.cli import app
from lion.program import adrs

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Disposable home and data directories."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def _invoke(*args: str) -> Result:
    return runner.invoke(app, list(args))


_ANSI_PATTERN = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _plain(text: str) -> str:
    """Normalize help text: strip ANSI escapes and collapse hard wrapping."""
    return " ".join(_ANSI_PATTERN.sub("", text).split())


def _add(*fields: str, title: str = "", decision: str = "", status: str = "") -> Result:
    args = ["adr", "add"]
    if title:
        args += ["--title", title]
    if decision:
        args += ["--decision", decision]
    if status:
        args += ["--status", status]
    for field in fields:
        args += ["--field", field]
    return _invoke(*args)


# --- status ------------------------------------------------------------------


def test_adr_bare_runs_status() -> None:
    """Bare ``lion adr`` is the status; an empty store is exit 0."""
    result = _invoke("adr")

    assert result.exit_code == 0
    assert "ADRs: 0 total, 0 complete, 0 incomplete, 0 edited." in result.stdout
    assert "Status: proposed 0, accepted 0, superseded 0." in result.stdout

    payload = json.loads(_invoke("adr", "status", "--json").stdout)
    assert payload == {
        "total": 0,
        "complete": 0,
        "incomplete": 0,
        "edited": 0,
        "status": {"proposed": 0, "accepted": 0, "superseded": 0},
    }


def test_status_counts_complete_and_edited() -> None:
    """The counters split complete/incomplete/edited and the status distribution."""
    _ = _add(title="Only title")
    _ = _add(title="Use X", decision="Because Y", status="accepted")
    payload = json.loads(_invoke("adr", "status", "--json").stdout)

    assert payload == {
        "total": 2,
        "complete": 1,
        "incomplete": 1,
        "edited": 0,
        "status": {"proposed": 1, "accepted": 1, "superseded": 0},
    }


def test_status_never_collects(monkeypatch: pytest.MonkeyPatch) -> None:
    """The status reads the store only; a collector failure cannot happen."""
    broken = Mock(side_effect=AssertionError("collect must not run"))
    monkeypatch.setattr("lion.state.collector.collect_state", broken)

    assert _invoke("adr", "status").exit_code == 0
    assert broken.call_count == 0


# --- add ---------------------------------------------------------------------


def test_add_creates_a_mode_600_document() -> None:
    """A new ADR is published with ref and path and mode 600."""
    result = _add(title="Use X", decision="Because Y", status="accepted")

    assert result.exit_code == 0
    assert "ADR created: adr-" in result.stdout
    document = adrs.list_adrs()[0]
    assert f"Path: {document.path}" in result.stdout
    assert stat.S_IMODE(document.path.stat().st_mode) == 0o600
    assert document.complete is True
    assert document.status == "accepted"


def test_add_requires_a_field() -> None:
    """An ADR without any field is rejected loudly."""
    result = _invoke("adr", "add")

    assert result.exit_code == 1
    assert "at least one field" in result.stderr
    assert not adrs.get_data_dir().exists()


def test_add_rejects_a_bad_field() -> None:
    """A ``--field`` without NAME=VALUE is a loud usage error."""
    result = _invoke("adr", "add", "--field", "bogus")

    assert result.exit_code == 1
    assert "Invalid --field" in result.stderr


def test_add_repeated_fields_and_recording() -> None:
    """Repeated ``--field`` options and ``--recording`` are stored."""
    result = _invoke("adr", "add", "--field", "component=shell", "--field", "note=a=b", "--recording", "rec-ref")

    assert result.exit_code == 0
    document = adrs.list_adrs()[0]
    assert document.fields == {"component": "shell", "note": "a=b"}
    assert document.recording == "rec-ref"


def test_add_unknown_status_is_visible_not_fatal() -> None:
    """An unknown ``--status`` is stored and reported as an issue."""
    assert _add(title="T", decision="D", status="bogus").exit_code == 0
    payload = json.loads(_invoke("adr", "show", "latest", "--json").stdout)

    assert payload["status"] == "bogus"
    assert [issue["reason"] for issue in payload["issues"]] == ["unknown status value"]


# --- list --------------------------------------------------------------------


def test_list_text_and_json() -> None:
    """The listing shows text rows and a JSON object of the same entries."""
    _ = _add(title="Use X", decision="Because Y", status="accepted")
    _ = _add("component=shell")

    text = _invoke("adr", "list").stdout
    assert "accepted" in text
    assert "Use X" in text

    payload = json.loads(_invoke("adr", "list", "--json").stdout)["adrs"]
    assert len(payload) == 2
    assert set(payload[0]) == {"ref", "status", "complete", "edited", "title", "recording", "created_at"}
    by_title = {item["title"]: item for item in payload}
    assert by_title["Use X"]["status"] == "accepted"
    assert by_title[""]["status"] == "proposed"
    assert by_title[""]["complete"] is False


def test_list_empty() -> None:
    """An empty store is exit 0 in text and JSON."""
    assert "No ADRs stored." in _invoke("adr", "list").stdout
    assert json.loads(_invoke("adr", "list", "--json").stdout) == {"adrs": []}


# --- show --------------------------------------------------------------------


def test_show_text_and_json() -> None:
    """The detail view shows fields in catalog order and the derived metadata."""
    _ = _add("component=shell", title="Use X", decision="Because Y")

    text = _invoke("adr", "show", "latest").stdout
    lines = text.splitlines()
    assert lines[0].startswith("ADR adr-")
    assert "(proposed, complete)" in lines[0]
    order = [line.split(":")[0].strip() for line in lines[1:] if line.startswith("  ") and ":" in line]
    assert order[:3] == ["title", "decision", "component"]
    assert "  edited: false" in text

    payload = json.loads(_invoke("adr", "show", "--json").stdout)
    assert set(payload) == {
        "ref",
        "fields",
        "status",
        "complete",
        "edited",
        "recording",
        "created_at",
        "edited_at",
        "uses",
        "supersedes",
        "issues",
    }
    assert list(payload["fields"]) == ["title", "decision", "component"]
    assert payload["issues"] == []


def test_show_resolves_uses_and_supersedes() -> None:
    """``uses``/``supersedes`` resolve exact/prefix and mark unknown tokens."""
    first = _add(title="First")
    assert first.exit_code == 0
    first_ref = adrs.list_adrs()[0].ref
    prefix = first_ref[:30]
    _ = _add(f"uses={prefix}, does-not-exist", f"supersedes={first_ref}", title="Second")

    text = _invoke("adr", "show", "latest").stdout
    assert f"{prefix} -> {first_ref}" in text
    assert "does-not-exist -> (unknown)" in text
    assert f"supersedes: {first_ref} -> {first_ref}" in text

    payload = json.loads(_invoke("adr", "show", "latest", "--json").stdout)
    assert payload["uses"] == [
        {"ref": prefix, "resolved": first_ref},
        {"ref": "does-not-exist", "resolved": None},
    ]


def test_reference_resolution_and_failures() -> None:
    """Prefixes resolve; unknown and ambiguous references fail loudly."""
    result = _add(title="T")
    assert result.exit_code == 0
    ref = adrs.list_adrs()[0].ref

    assert _invoke("adr", "show", ref[:30], "--json").exit_code == 0
    assert _invoke("adr", "show", "latest", "--json").exit_code == 0

    unknown = _invoke("adr", "show", "does-not-exist")
    assert unknown.exit_code == 1
    assert "Unknown reference" in unknown.stderr

    _ = _add(title="U")
    ambiguous = _invoke("adr", "show", "adr-20")
    assert ambiguous.exit_code == 1
    assert "ambiguous" in ambiguous.stderr


def test_show_without_store_fails() -> None:
    """``show`` on an empty store fails loudly."""
    result = _invoke("adr", "show")

    assert result.exit_code == 1
    assert "No ADRs stored" in result.stderr


# --- edit --------------------------------------------------------------------


def _edit_script(tmp_path: Path, body: str) -> str:
    """Write an executable $EDITOR stub that runs ``body`` after reading argv[1]."""
    script = tmp_path / "editor_stub.py"
    script.write_text(
        "import pathlib, sys\npath = pathlib.Path(sys.argv[1])\ntext = path.read_text()\n" + body,
        encoding="utf-8",
    )
    script.chmod(0o755)
    return f"{sys.executable} {script}"


def test_edit_overwrites_fields_and_cleans_the_draft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The editor flow merges fields, stamps edited_at and removes the draft."""
    _ = _add(title="Use X")
    ref = adrs.list_adrs()[0].ref
    monkeypatch.setenv(
        "EDITOR",
        _edit_script(
            tmp_path,
            'assert \'title = "Use X"\' in text, text\npath.write_text(text + "decision = \\"Because Y\\"\\n")\n',
        ),
    )

    result = _invoke("adr", "edit", ref)

    assert result.exit_code == 0
    assert "ADR written" in result.stdout
    document = adrs.load_adr(adrs.get_adrs_dir() / f"{ref}.toml")
    assert document.fields == {"title": "Use X", "decision": "Because Y"}
    assert document.edited is True
    assert stat.S_IMODE(document.path.stat().st_mode) == 0o600
    assert not list(document.path.parent.glob(".adr-edit-*"))


def test_edit_defaults_to_latest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without REF edit targets the latest ADR."""
    _ = _add(title="Only")
    monkeypatch.setenv("EDITOR", _edit_script(tmp_path, 'path.write_text(text + "context = \\"c\\"\\n")\n'))

    result = _invoke("adr", "edit")

    assert result.exit_code == 0
    assert adrs.list_adrs()[0].fields["context"] == "c"


def test_edit_requires_editor(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing $EDITOR is a loud error and changes nothing."""
    monkeypatch.delenv("EDITOR", raising=False)
    _ = _add(title="Use X")
    ref = adrs.list_adrs()[0].ref

    result = _invoke("adr", "edit", ref)

    assert result.exit_code == 1
    assert "EDITOR" in result.stderr
    assert adrs.load_adr(adrs.get_adrs_dir() / f"{ref}.toml").edited is False


def test_edit_editor_failure_keeps_the_draft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing editor aborts loudly and leaves the draft for reconciliation."""
    _ = _add(title="Use X")
    monkeypatch.setenv("EDITOR", _edit_script(tmp_path, "sys.exit(3)\n"))

    result = _invoke("adr", "edit", "latest")

    assert result.exit_code == 1
    assert "exited with status 3" in result.stderr
    drafts = list(adrs.get_adrs_dir().glob(".adr-edit-*"))
    assert len(drafts) == 1
    assert str(drafts[0]) in result.stderr


def test_edit_invalid_content_keeps_the_draft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid edited content fails with the draft path and writes nothing."""
    _ = _add(title="Use X")
    ref = adrs.list_adrs()[0].ref
    monkeypatch.setenv("EDITOR", _edit_script(tmp_path, "path.write_text('broken = [')\n"))

    result = _invoke("adr", "edit", ref)

    assert result.exit_code == 1
    assert "Invalid ADR document" in result.stderr
    drafts = list(adrs.get_adrs_dir().glob(".adr-edit-*"))
    assert len(drafts) == 1
    assert str(drafts[0]) in result.stderr
    assert adrs.load_adr(adrs.get_adrs_dir() / f"{ref}.toml").fields == {"title": "Use X"}


def test_edit_rejects_empty_field_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Clearing every field is rejected loudly and keeps the draft."""
    _ = _add(title="Use X")
    document = adrs.list_adrs()[0]
    body = (
        "path.write_text("
        f'\'schema_version = 1\\nid = "{document.ref}"\\ncreated_at = "{document.created_at}"\\n[fields]\\n\''
        ")\n"
    )
    monkeypatch.setenv("EDITOR", _edit_script(tmp_path, body))

    result = _invoke("adr", "edit", document.ref)

    assert result.exit_code == 1
    assert "at least one field" in result.stderr
    drafts = list(adrs.get_adrs_dir().glob(".adr-edit-*"))
    assert len(drafts) == 1
    assert adrs.load_adr(adrs.get_adrs_dir() / f"{document.ref}.toml").edited is False


def test_edit_rejects_changed_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Changing the id in the editor is rejected and keeps the draft."""
    _ = _add(title="Use X")
    document = adrs.list_adrs()[0]
    content = f'schema_version = 1\nid = "adr-changed"\ncreated_at = "{document.created_at}"\n[fields]\ntitle = "x"\n'
    body = f"path.write_text({content!r})\n"
    monkeypatch.setenv("EDITOR", _edit_script(tmp_path, body))

    result = _invoke("adr", "edit", document.ref)

    assert result.exit_code == 1
    assert "id must stay" in result.stderr
    assert len(list(adrs.get_adrs_dir().glob(".adr-edit-*"))) == 1


# --- read-only proof ---------------------------------------------------------


def test_read_commands_never_write() -> None:
    """status, list and show leave the data directory byte-identical."""
    _ = _add(title="Use X", decision="Because Y", status="accepted")
    _ = _add("component=shell")
    data = Path(os.environ["XDG_DATA_HOME"])
    before = {
        path.relative_to(data).as_posix(): path.read_bytes() for path in sorted(data.rglob("*")) if path.is_file()
    }

    assert _invoke("adr", "status").exit_code == 0
    assert _invoke("adr", "status", "--json").exit_code == 0
    assert _invoke("adr", "list").exit_code == 0
    assert _invoke("adr", "list", "--json").exit_code == 0
    assert _invoke("adr", "show", "latest").exit_code == 0
    assert _invoke("adr", "show", "latest", "--json").exit_code == 0

    after = {path.relative_to(data).as_posix(): path.read_bytes() for path in sorted(data.rglob("*")) if path.is_file()}
    assert after == before


def test_add_never_overwrites(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two ADRs created in the same instant both survive."""
    from datetime import UTC, datetime

    frozen = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return frozen

    monkeypatch.setattr(adrs, "datetime", FrozenDatetime)

    assert _add(title="First").exit_code == 0
    assert _add(title="Second").exit_code == 0

    documents = adrs.list_adrs()
    assert len(documents) == 2
    assert documents[0].fields["title"] == "First"
    assert documents[1].fields["title"] == "Second"
    assert documents[1].ref.endswith("~0001")


# --- help --------------------------------------------------------------------


def test_help_documents_commands() -> None:
    """``lion adr --help`` explains the standalone decisions and the default action."""
    result = _invoke("adr", "--help")

    assert result.exit_code == 0
    help_text = _plain(result.stdout)
    assert "standalone" in help_text
    assert "status" in help_text
    assert "No delete" in help_text or "never removes" in help_text
