"""Tests for the ``lion history`` and ``lion diff`` read-only commands."""

import json
from collections.abc import Mapping
from pathlib import Path

import pytest
import tomli_w
from typer.testing import CliRunner, Result

from lion.cli import app
from lion.program.storage import get_history_dir
from lion.state.model import Snapshot

runner = CliRunner()

A_REF = "2026-10-05T18-00-00.000000Z"
B_REF = "2026-10-05T20-00-00.000000Z"
C_REF = "2026-10-05T22-00-00.000000Z"
A_ISO = "2026-10-05T18:00:00+00:00"
B_ISO = "2026-10-05T20:00:00+00:00"
C_ISO = "2026-10-05T22:00:00+00:00"
RAM_OLD = 99005419520
RAM_NEW = 99005415424


@pytest.fixture(autouse=True)
def isolated_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep tests away from the real LION data directory."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))


def _write(ref: str, iso: str, collectors: Mapping[str, Mapping[str, object]]) -> None:
    get_history_dir().mkdir(parents=True, exist_ok=True)
    snapshot = Snapshot(erstscan=iso, zuletzt_bestaetigt=iso, collectors={k: dict(v) for k, v in collectors.items()})
    (get_history_dir() / f"{ref}.toml").write_text(tomli_w.dumps(snapshot.to_toml_dict()))


def _host(hostname: str) -> dict[str, dict[str, object]]:
    return {"host": {"status": "ok", "error": "", "hostname": hostname}}


def _hardware(memory: int) -> dict[str, dict[str, object]]:
    return {
        "host": {"status": "ok", "error": "", "hostname": "lion"},
        "hardware": {"status": "ok", "error": "", "memory_total_bytes": memory},
    }


def _invoke(*args: str) -> Result:
    return runner.invoke(app, list(args))


def test_history_lists_oldest_first() -> None:
    """Entries are numbered from oldest to newest with the newest marked."""
    _write(A_REF, A_ISO, _host("one"))
    _write(B_REF, B_ISO, _host("two"))

    result = _invoke("history")

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert A_REF in lines[1]
    assert B_REF in lines[2]
    assert lines[1].startswith("  1")
    assert lines[2].startswith("  2")
    assert "aktuell" in lines[2]
    assert "aktuell" not in lines[1]


def test_history_json() -> None:
    """``history --json`` exposes index, ref, timestamps and the path."""
    _write(A_REF, A_ISO, _host("one"))
    _write(B_REF, B_ISO, _host("two"))

    result = _invoke("history", "--json")

    assert result.exit_code == 0
    assert result.stderr == ""
    entries = json.loads(result.stdout)["eintraege"]
    assert [entry["index"] for entry in entries] == [1, 2]
    assert [entry["ref"] for entry in entries] == [A_REF, B_REF]
    assert entries[1]["aktuell"] is True
    assert entries[0]["pfad"].endswith(f"{A_REF}.toml")


def test_history_empty() -> None:
    """An empty history is reported without creating files."""
    text = _invoke("history")
    assert text.exit_code == 0
    assert "Kein Zustand gespeichert" in text.stdout
    machine = _invoke("history", "--json")
    assert json.loads(machine.stdout) == {"eintraege": []}
    assert not get_history_dir().exists()


def test_history_corrupt_entry_fails() -> None:
    """A damaged entry stops the listing and names the offending file."""
    get_history_dir().mkdir(parents=True)
    (get_history_dir() / "broken.toml").write_text("broken = [")
    result = _invoke("history")
    assert result.exit_code == 1
    assert "broken.toml" in result.stderr


def test_history_limit_keeps_global_indices() -> None:
    """``--limit`` shows the newest N, oldest first, without renumbering."""
    _write(A_REF, A_ISO, _host("one"))
    _write(B_REF, B_ISO, _host("two"))
    _write(C_REF, C_ISO, _host("three"))

    result = _invoke("history", "--limit", "2")

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert len(lines) == 3
    assert lines[1].startswith("  2")
    assert lines[2].startswith("  3")
    assert B_REF in lines[1] and C_REF in lines[2]
    assert "aktuell" in lines[2] and "aktuell" not in lines[1]


def test_history_limit_json_matches_text_selection() -> None:
    """``--limit`` applies the same selection and indices to the JSON output."""
    _write(A_REF, A_ISO, _host("one"))
    _write(B_REF, B_ISO, _host("two"))
    _write(C_REF, C_ISO, _host("three"))

    entries = json.loads(_invoke("history", "--limit", "2", "--json").stdout)["eintraege"]

    assert [entry["index"] for entry in entries] == [2, 3]
    assert [entry["ref"] for entry in entries] == [B_REF, C_REF]
    assert entries[-1]["aktuell"] is True


def test_history_limit_larger_than_history_shows_all() -> None:
    """A limit above the entry count leaves the listing unchanged."""
    _write(A_REF, A_ISO, _host("one"))
    _write(B_REF, B_ISO, _host("two"))

    entries = json.loads(_invoke("history", "--limit", "5", "--json").stdout)["eintraege"]
    assert [entry["index"] for entry in entries] == [1, 2]


@pytest.mark.parametrize("value", ["0", "-1"])
def test_history_limit_rejects_non_positive(value: str) -> None:
    """Zero and negative limits are rejected as invalid CLI input."""
    result = _invoke("history", "--limit", value)
    assert result.exit_code == 2


def test_history_limit_still_validates_all_entries() -> None:
    """A damaged older entry is not hidden by a limit on the newest entries."""
    _write(A_REF, A_ISO, _host("one"))
    (get_history_dir() / "broken.toml").write_text("broken = [")
    _write(B_REF, B_ISO, _host("two"))

    result = _invoke("history", "--limit", "1")
    assert result.exit_code == 1
    assert "broken.toml" in result.stderr


def test_diff_by_index() -> None:
    """Two stored states compare through the shared diff renderer."""
    _write(A_REF, A_ISO, _host("one"))
    _write(B_REF, B_ISO, _host("two"))

    result = _invoke("diff", "1", "2")

    assert result.exit_code == 0
    assert f"Vergleich {A_REF} → {B_REF}" in result.stdout
    assert "~ hostname: one -> two" in result.stdout


def test_diff_defaults_to_latest() -> None:
    """A single reference compares against the latest stored state."""
    _write(A_REF, A_ISO, _host("one"))
    _write(B_REF, B_ISO, _host("two"))

    assert "~ hostname: one -> two" in _invoke("diff", A_REF).stdout


def test_diff_aliases() -> None:
    """``previous`` and ``latest`` resolve to the last two entries."""
    _write(A_REF, A_ISO, _host("one"))
    _write(B_REF, B_ISO, _host("two"))

    assert f"Vergleich {A_REF} → {B_REF}" in _invoke("diff", "previous", "latest").stdout
    assert f"Vergleich {A_REF} → {B_REF}" in _invoke("diff", "vorherig", "aktuell").stdout


def test_diff_accepts_prefix_and_iso() -> None:
    """Unique prefixes and ISO timestamps resolve to the same states."""
    _write(A_REF, A_ISO, _host("one"))
    _write(B_REF, B_ISO, _host("two"))

    assert "~ hostname: one -> two" in _invoke("diff", "2026-10-05T18", "latest").stdout
    assert "~ hostname: one -> two" in _invoke("diff", "2026-10-05T18:00:00Z", B_ISO).stdout


def test_diff_json() -> None:
    """``diff --json`` exposes both references and the structured difference."""
    _write(A_REF, A_ISO, _host("one"))
    _write(B_REF, B_ISO, _host("two"))

    result = _invoke("diff", "1", "2", "--json")

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["geaendert"] is True
    assert payload["von"]["ref"] == A_REF
    assert payload["bis"]["ref"] == B_REF
    assert payload["unterschiede"]["host"]["changed"]["hostname"] == {"old": "one", "new": "two"}


def test_diff_no_changes() -> None:
    """Identical collector data reports no differences and stays successful."""
    _write(A_REF, A_ISO, _host("one"))
    _write(B_REF, B_ISO, _host("one"))

    text = _invoke("diff", "1", "2")
    assert text.exit_code == 0
    assert "Keine Unterschiede." in text.stdout
    assert json.loads(_invoke("diff", "1", "2", "--json").stdout)["geaendert"] is False


def test_diff_reuses_ram_tolerance() -> None:
    """A ``MemTotal`` wobble alone is not a difference, matching status/scan."""
    _write(A_REF, A_ISO, _hardware(RAM_OLD))
    _write(B_REF, B_ISO, _hardware(RAM_NEW))

    assert "Keine Unterschiede." in _invoke("diff", "1", "2").stdout


def test_diff_ambiguous_prefix() -> None:
    """A prefix matching several entries is rejected with candidates."""
    _write(A_REF, A_ISO, _host("one"))
    _write(B_REF, B_ISO, _host("two"))

    result = _invoke("diff", "2026-10-05T", "latest")
    assert result.exit_code == 1
    assert "mehrdeutig" in result.stderr


def test_diff_unknown_reference() -> None:
    """An unknown reference lists the valid compact references."""
    _write(A_REF, A_ISO, _host("one"))
    _write(B_REF, B_ISO, _host("two"))

    result = _invoke("diff", "does-not-exist", "latest")
    assert result.exit_code == 1
    assert "Unbekannte Referenz" in result.stderr


def test_diff_index_out_of_range() -> None:
    """A numeric index outside the listing is rejected."""
    _write(A_REF, A_ISO, _host("one"))
    _write(B_REF, B_ISO, _host("two"))

    result = _invoke("diff", "9", "latest")
    assert result.exit_code == 1
    assert "außerhalb" in result.stderr


def test_diff_requires_two_entries() -> None:
    """Comparing needs at least two stored states."""
    _write(A_REF, A_ISO, _host("one"))

    result = _invoke("diff", A_REF)
    assert result.exit_code == 1
    assert "Weniger als zwei" in result.stderr
