"""Smoke tests for the LION CLI."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from repolion.cli import app
from repolion.scan import SystemInfo
from repolion.storage import save_scan


@pytest.fixture(autouse=True)
def isolated_data_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep tests away from the real LION data directory."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))


runner = CliRunner()


def test_help() -> None:
    """Show CLI help."""
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Linux Operator Nerd" in result.stdout


def test_scan() -> None:
    """Run the scan command."""
    result = runner.invoke(app, ["scan"])

    assert result.exit_code == 0
    assert "Host:" in result.stdout
    assert "OS:" in result.stdout
    assert "Kernel:" in result.stdout
    assert "Arch:" in result.stdout
    assert "CPU:" in result.stdout
    assert "Cores:" in result.stdout
    assert "Memory:" in result.stdout


def test_status() -> None:
    """Run the status command."""
    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "No scan found." in result.stdout


def test_status_with_existing_scan() -> None:
    """Show the latest stored scan."""
    system_info = SystemInfo(
        distribution="Test Linux",
        distribution_version="1.0",
        kernel="6.0.0-test",
        architecture="x86_64",
        hostname="lion-test",
        cpu_model="Test CPU",
        cpu_logical_cores=8,
        memory_total_bytes=16 * 1024**3,
    )
    save_scan(system_info)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "lion-test" in result.stdout
    assert "Test Linux 1.0" in result.stdout
    assert "6.0.0-test" in result.stdout
    assert "Test CPU" in result.stdout
    assert "16 GiB" in result.stdout


def test_json_scan_and_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both commands emit the same stored record as clean machine-readable JSON."""
    import json

    from repolion import cli

    info = SystemInfo("Test Linux", "1.0", "6.0", "x86_64", "lion-test", "Test CPU", 8, 16 * 1024**3)
    monkeypatch.setattr(cli, "scan_system", lambda: info)
    scanned = runner.invoke(app, ["scan", "--json"])
    latest = runner.invoke(app, ["status", "--json"])
    assert scanned.exit_code == latest.exit_code == 0
    assert scanned.stderr == latest.stderr == ""
    data = json.loads(scanned.stdout)
    assert data == json.loads(latest.stdout)
    assert data["system"]["hostname"] == "lion-test"
    assert data["system"]["memory_total_bytes"] == 16 * 1024**3
    assert data["scan"]["timestamp"]


def test_json_without_scan() -> None:
    """Represent an absent saved scan explicitly as JSON null."""
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "null"


@pytest.mark.parametrize("args", [["status"], ["status", "--json"]])
def test_corrupt_scan_error(args: list[str]) -> None:
    """Report corrupt files on stderr with an unsuccessful exit status."""
    from repolion.paths import get_scans_dir

    directory = get_scans_dir()
    directory.mkdir(parents=True)
    (directory / "broken.toml").write_text("broken = [")
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "broken.toml" in result.stderr
    assert "Traceback" not in result.stderr


def test_save_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not print success or JSON when saving fails."""
    from repolion import cli

    def fail_save(system_info: SystemInfo) -> Path:
        raise PermissionError("scan directory is read-only")

    monkeypatch.setattr(cli, "save_scan", fail_save)
    result = runner.invoke(app, ["scan", "--json"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "read-only" in result.stderr
