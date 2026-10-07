"""Smoke tests for the LION CLI."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from lion.cli import app
from lion.command import scan as scan_command
from lion.command import status as status_command
from lion.program.storage import get_data_dir, get_history_dir

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep tests away from the real LION data directory."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))


@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, object]]:
    """Replace collection with a deterministic state."""
    value: dict[str, dict[str, object]] = {
        "host": {"status": "ok", "error": "", "hostname": "lion-test"},
        "hardware": {"status": "ok", "error": "", "cpu_model": "Test CPU", "gpu": []},
    }

    def fake_collect(collectors: object) -> dict[str, dict[str, object]]:
        return value

    monkeypatch.setattr(scan_command, "collect_state", fake_collect)
    monkeypatch.setattr(status_command, "collect_state", fake_collect)
    return value


def _invoke(*args: str) -> Result:
    return runner.invoke(app, list(args))


def test_help() -> None:
    """Show CLI help."""
    result = _invoke("--help")
    assert result.exit_code == 0
    assert "Linux Operator Nerd" in result.stdout


def test_no_command_runs_status() -> None:
    """Running ``lion`` without a command is the same as ``lion status``."""
    result = _invoke()
    assert result.exit_code == 0
    assert "Kein Zustand gespeichert" in result.stdout


def test_version_option(monkeypatch: pytest.MonkeyPatch) -> None:
    """``lion --version`` prints the package version without collecting or writing."""
    from lion import __version__

    def boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("collectors must not run for --version")

    monkeypatch.setattr(scan_command, "collect_state", boom)
    monkeypatch.setattr(status_command, "collect_state", boom)
    result = _invoke("--version")
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__
    assert not get_data_dir().exists()


def test_scan_created_then_confirmed(state: dict[str, dict[str, object]]) -> None:
    """The first scan creates an entry; an unchanged scan confirms it."""
    first = _invoke("scan")
    second = _invoke("scan")
    assert first.exit_code == second.exit_code == 0
    assert "Zustand angelegt" in first.stdout
    assert "Zeitstempel aktualisiert" in second.stdout
    assert len(list(get_history_dir().glob("*.toml"))) == 1


def test_scan_json(state: dict[str, dict[str, object]]) -> None:
    """``scan --json`` reports the event, path, and stored state."""
    result = _invoke("scan", "--json")
    assert result.exit_code == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["ereignis"] == "created"
    assert payload["pfad"].endswith(".toml")
    assert payload["zustand"]["collectors"]["host"]["hostname"] == "lion-test"


def test_status_without_history() -> None:
    """Without history, status explains what to do; JSON returns null."""
    text = _invoke("status")
    assert text.exit_code == 0
    assert "Kein Zustand gespeichert" in text.stdout
    machine = _invoke("status", "--json")
    assert machine.exit_code == 0
    assert machine.stdout.strip() == "null"


def test_status_warns_about_legacy_scans() -> None:
    """A leftover legacy scans/ directory is mentioned without being read."""
    (get_data_dir() / "scans").mkdir(parents=True)
    result = _invoke("status")
    assert result.exit_code == 0
    assert "Kein Zustand gespeichert" in result.stdout
    assert "scans/" in result.stderr


def test_status_unchanged(state: dict[str, dict[str, object]]) -> None:
    """An unchanged state reports that nothing changed."""
    _invoke("scan")
    result = _invoke("status")
    assert result.exit_code == 0
    assert "hat sich nichts geändert" in result.stdout


def test_status_changed(state: dict[str, dict[str, object]]) -> None:
    """A changed state renders a grouped diff."""
    _invoke("scan")
    state["host"]["hostname"] = "server"
    result = _invoke("status")
    assert result.exit_code == 0
    assert "host:" in result.stdout
    assert "~ hostname: lion-test -> server" in result.stdout


def test_status_json_changed(state: dict[str, dict[str, object]]) -> None:
    """``status --json`` exposes the structured difference."""
    _invoke("scan")
    state["host"]["hostname"] = "server"
    result = _invoke("status", "--json")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["geaendert"] is True
    assert payload["seit"]
    assert payload["unterschiede"]["host"]["changed"]["hostname"] == {"old": "lion-test", "new": "server"}


@pytest.mark.parametrize("args", [["status"], ["status", "--json"]])
def test_corrupt_history_error(args: list[str]) -> None:
    """Corrupt entries fail on stderr with exit code 1 and no JSON on stdout."""
    directory = get_history_dir()
    directory.mkdir(parents=True)
    (directory / "broken.toml").write_text("broken = [")
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "broken.toml" in result.stderr
    assert "Traceback" not in result.stderr


def test_save_error(monkeypatch: pytest.MonkeyPatch, state: dict[str, dict[str, object]]) -> None:
    """Do not print success or JSON when saving fails."""

    def fail_save(collectors: dict[str, dict[str, object]]) -> object:
        raise PermissionError("history directory is read-only")

    monkeypatch.setattr(scan_command, "save_state", fail_save)
    result = _invoke("scan", "--json")
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "read-only" in result.stderr
