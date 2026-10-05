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


def test_init() -> None:
    """Run the init command."""
    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "LION initialized at" in result.stdout
    assert "/lion" in result.stdout


def test_scan() -> None:
    """Run the scan command."""
    result = runner.invoke(app, ["scan"])

    assert result.exit_code == 0
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
    assert "Test Linux 1.0" in result.stdout
    assert "6.0.0-test" in result.stdout
    assert "Test CPU" in result.stdout
    assert "16 GiB" in result.stdout
