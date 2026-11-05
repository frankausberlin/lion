"""CLI tests for ``lion doctor``."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import lion.doctor.cli as doctor_cli
from lion.cli import app
from lion.doctor.model import FixOutcome, FixPlan
from lion.shlib.layout import ShlibLayout

runner = CliRunner()


def _fail_execute(layout: ShlibLayout, plan: FixPlan) -> FixOutcome:
    """Return a failing fix outcome."""
    _ = layout, plan
    return FixOutcome(errors=("write failed",))


def test_doctor_help_lists_options() -> None:
    """The help documents the doctor options."""
    result = runner.invoke(app, ["doctor", "--help"])

    assert result.exit_code == 0
    for option in ("--home", "--fix", "--dry-run", "--no-color"):
        assert option in result.output


def test_doctor_not_installed_exits_one(home: Path) -> None:
    """A missing installation is reported without creating anything."""
    result = runner.invoke(app, ["doctor", "--home", str(home)])

    assert result.exit_code == 1
    assert "shlib.not_installed" in result.output
    assert not (home / ".shlib").exists()


def test_doctor_clean_report(installed: ShlibLayout, home: Path) -> None:
    """A clean installation prints sections and a zero summary."""
    result = runner.invoke(app, ["doctor", "--home", str(home), "--no-color"])

    assert result.exit_code == 0
    assert "shlib:" in result.output
    assert "dash:" in result.output
    assert "security:" in result.output
    assert "ok" in result.output
    assert "doctor: 0 fixable, 0 manual" in result.output
    assert "\x1b[" not in result.output


def test_doctor_default_is_read_only(installed: ShlibLayout, home: Path) -> None:
    """The default run does not create missing directories."""
    installed.exports.rmdir()
    installed.dash.rmdir()

    result = runner.invoke(app, ["doctor", "--home", str(home)])

    assert result.exit_code == 1
    assert not installed.exports.exists()
    assert not installed.dash.exists()


def test_doctor_dry_run_prints_plan_without_writing(installed: ShlibLayout, home: Path) -> None:
    """Dry-run lists fixable actions and leaves the filesystem unchanged."""
    installed.redaction_key.unlink()

    result = runner.invoke(app, ["doctor", "--home", str(home), "--dry-run"])

    assert result.exit_code == 0
    assert "Planned fixes:" in result.output
    assert "redaction-key" in result.output
    assert "Dry run: no changes made." in result.output
    assert not installed.redaction_key.exists()


def test_doctor_dry_run_on_error_state_exits_zero(installed: ShlibLayout, home: Path) -> None:
    """Dry-run still exits 0 when the diagnosed state contains errors."""
    installed.redaction_key.unlink()
    installed.redaction_key.mkdir()

    result = runner.invoke(app, ["doctor", "--home", str(home), "--dry-run"])

    assert result.exit_code == 0
    assert "Dry run: no changes made." in result.output
    assert installed.redaction_key.is_dir()


def test_doctor_fix_creates_key_and_is_idempotent(installed: ShlibLayout, home: Path) -> None:
    """The first fix repairs the key and a second run plans nothing."""
    installed.redaction_key.unlink()

    first = runner.invoke(app, ["doctor", "--home", str(home), "--fix"])
    second = runner.invoke(app, ["doctor", "--home", str(home), "--fix"])

    assert first.exit_code == 0
    assert "Planned fixes:" in first.output
    assert "doctor: 0 fixable, 0 manual" in first.output
    assert installed.redaction_key.exists()
    assert second.exit_code == 0
    assert "No fixable findings." in second.output


def test_doctor_fix_removes_orphan_lock(installed: ShlibLayout, home: Path) -> None:
    """Orphaned locks are removed without touching the dash register."""
    orphan = installed.locks / "ghost.lock"
    orphan.write_bytes(b"x")

    result = runner.invoke(app, ["doctor", "--home", str(home), "--fix"])

    assert result.exit_code == 0
    assert not orphan.exists()
    assert installed.dash.is_dir()


def test_doctor_fix_missing_dash_defers_locks(installed: ShlibLayout, home: Path) -> None:
    """Creating dash reports that pre-existing locks need a later fix."""
    installed.dash.rmdir()
    orphan = installed.locks / "ghost.lock"
    orphan.write_bytes(b"x")

    result = runner.invoke(app, ["doctor", "--home", str(home), "--fix"])

    assert result.exit_code == 1
    assert "note:" in result.output
    assert "later explicit" in result.output
    assert installed.dash.is_dir()
    assert orphan.exists()
    assert "locks.orphan" in result.output


def test_doctor_fix_refuses_missing_installation(home: Path) -> None:
    """--fix refuses to create .shlib itself."""
    result = runner.invoke(app, ["doctor", "--home", str(home), "--fix"])

    assert result.exit_code == 1
    assert "missing" in result.output
    assert not (home / ".shlib").exists()


def test_doctor_rejects_fix_and_dry_run_together(home: Path) -> None:
    """The two modes are mutually exclusive."""
    result = runner.invoke(app, ["doctor", "--home", str(home), "--fix", "--dry-run"])

    assert result.exit_code == 2
    assert "mutually exclusive" in result.stderr


def test_doctor_fix_reports_action_errors(installed: ShlibLayout, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed action is reported and forces exit 2."""
    installed.redaction_key.unlink()
    monkeypatch.setattr(doctor_cli, "execute_fixes", _fail_execute)

    result = runner.invoke(app, ["doctor", "--home", str(home), "--fix"])

    assert result.exit_code == 2
    assert "error: write failed" in result.stderr
    assert "doctor:" in result.output
