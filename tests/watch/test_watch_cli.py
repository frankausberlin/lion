"""CLI tests for ``lion watch``."""

from __future__ import annotations

import shutil
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lion.cli import app
from lion.shlib.layout import ShlibLayout
from lion.watch.dog import WATCHDOG_CONTENT, WATCHDOG_MODE, WATCHDOG_NAME, disable, enable, status
from lion.watch.errors import WatchDogError

runner = CliRunner()


def _locked(layout: ShlibLayout, home: Path, content: bytes = b"hello\n") -> Path:
    """Create a locked dash entry."""
    target = home / "target"
    target.write_bytes(content)
    (layout.dash / "conf").symlink_to(target)
    layout.lock_for("conf").write_bytes(content)
    return target


def test_watch_clean_is_silent(installed: ShlibLayout, home: Path) -> None:
    """A clean state prints nothing and exits 0."""
    _locked(installed, home)

    result = runner.invoke(app, ["watch", "--home", str(home)])

    assert result.exit_code == 0
    assert result.output == ""


def test_watch_without_dash_is_silent(home: Path) -> None:
    """A home without a shlib tree is silent."""
    result = runner.invoke(app, ["watch", "--home", str(home)])

    assert result.exit_code == 0
    assert result.output == ""


def test_watch_drift_prints_diff(installed: ShlibLayout, home: Path) -> None:
    """Drift prints a unified diff and still exits 0."""
    target = _locked(installed, home)
    target.write_bytes(b"world\n")

    result = runner.invoke(app, ["watch", "--home", str(home)])

    assert result.exit_code == 0
    assert "dash: conf: changed" in result.output
    assert "--- lock: ~/.shlib/locks/conf.lock" in result.output
    assert "+++ target: ~/target" in result.output
    assert "-hello" in result.output
    assert "+world" in result.output


def test_watch_lock_error_exits_2(installed: ShlibLayout, home: Path) -> None:
    """A lock that is not a regular file is an error finding with exit 2."""
    target = _locked(installed, home)
    lock = installed.lock_for("conf")
    lock.unlink()
    lock.mkdir()
    assert target.exists()

    result = runner.invoke(app, ["watch", "--home", str(home)])

    assert result.exit_code == 2
    assert "lock is not a regular file" in result.output


def test_watch_directory_error_exits_2(installed: ShlibLayout, home: Path) -> None:
    """A symlinked dash directory is a scan error on stderr."""
    outside = home / "outside"
    outside.mkdir()
    shutil.rmtree(installed.dash)
    installed.dash.symlink_to(outside)

    result = runner.invoke(app, ["watch", "--home", str(home)])

    assert result.exit_code == 2
    assert "error:" in result.stderr


def test_watch_help_lists_home() -> None:
    """The watch help documents --home."""
    result = runner.invoke(app, ["watch", "--help"])

    assert result.exit_code == 0
    assert "--home" in result.output


def test_watch_dog_help_lists_commands() -> None:
    """The watch-dog help lists its subcommands."""
    result = runner.invoke(app, ["watch-dog", "--help"])

    assert result.exit_code == 0
    assert "on" in result.output
    assert "off" in result.output
    assert "status" in result.output


def test_watch_dog_on_off_status(installed: ShlibLayout, home: Path) -> None:
    """The subcommands write, report and remove the managed file."""
    on = runner.invoke(app, ["watch-dog", "on", "--home", str(home)])
    assert on.exit_code == 0
    assert WATCHDOG_NAME in on.output
    path = installed.shlibs / WATCHDOG_NAME
    assert path.read_bytes() == WATCHDOG_CONTENT
    assert stat.S_IMODE(path.stat().st_mode) == WATCHDOG_MODE

    state_on = runner.invoke(app, ["watch-dog", "status", "--home", str(home)])
    assert state_on.exit_code == 0
    assert "watchdog: on" in state_on.output

    off = runner.invoke(app, ["watch-dog", "off", "--home", str(home)])
    assert off.exit_code == 0
    assert "removed" in off.output
    assert not path.exists()

    state_off = runner.invoke(app, ["watch-dog", "status", "--home", str(home)])
    assert state_off.exit_code == 1
    assert "watchdog: off" in state_off.output


def test_watch_dog_off_missing_is_noop(installed: ShlibLayout, home: Path) -> None:
    """Removing a missing watchdog file succeeds."""
    result = runner.invoke(app, ["watch-dog", "off", "--home", str(home)])

    assert result.exit_code == 0
    assert "no watchdog file" in result.output


def test_watch_dog_missing_shlibs_is_error(home: Path) -> None:
    """Enabling without a shlib tree exits 2 with an install hint."""
    result = runner.invoke(app, ["watch-dog", "on", "--home", str(home)])

    assert result.exit_code == 2
    assert "error:" in result.stderr
    assert "lion shlib install" in result.stderr


def test_watch_dog_status_missing_shlibs_is_off(home: Path) -> None:
    """Status without a shlib tree reports off and exits 1."""
    result = runner.invoke(app, ["watch-dog", "status", "--home", str(home)])

    assert result.exit_code == 1
    assert "watchdog: off" in result.output


def test_watch_dog_directory_path_is_error(installed: ShlibLayout, home: Path) -> None:
    """A directory at the watchdog path is an error."""
    (installed.shlibs / WATCHDOG_NAME).mkdir()

    result = runner.invoke(app, ["watch-dog", "status", "--home", str(home)])

    assert result.exit_code == 2
    assert "error:" in result.stderr


def test_enable_and_disable_helpers(installed: ShlibLayout) -> None:
    """The core helpers expose the same behavior without the CLI."""
    path = enable(installed)
    assert path == installed.shlibs / WATCHDOG_NAME
    assert status(installed) == "on"
    assert disable(installed)
    assert not disable(installed)
    assert status(installed) == "off"


def test_enable_overwrites_existing_content(installed: ShlibLayout) -> None:
    """Enabling replaces a manually edited file completely."""
    path = installed.shlibs / WATCHDOG_NAME
    path.write_text("# manual\n", encoding="utf-8")
    path.chmod(0o600)

    written = enable(installed)

    assert written.read_bytes() == WATCHDOG_CONTENT
    assert stat.S_IMODE(written.stat().st_mode) == WATCHDOG_MODE


def test_disable_removes_symlink(installed: ShlibLayout, home: Path) -> None:
    """Disabling removes a symlink at the watchdog path."""
    other = home / "other"
    other.write_text("x\n", encoding="utf-8")
    path = installed.shlibs / WATCHDOG_NAME
    path.symlink_to(other)

    assert disable(installed)
    assert not path.exists()
    assert other.exists()


def test_disable_directory_is_error(installed: ShlibLayout) -> None:
    """Disabling a directory at the watchdog path is an error."""
    (installed.shlibs / WATCHDOG_NAME).mkdir()

    with pytest.raises(WatchDogError, match="directory"):
        disable(installed)


def test_symlinked_shlibs_is_refused(installed: ShlibLayout, home: Path) -> None:
    """All watchdog operations refuse a symlinked shlibs directory."""
    outside = home / "outside"
    outside.mkdir()
    shutil.rmtree(installed.shlibs)
    installed.shlibs.symlink_to(outside)

    with pytest.raises(WatchDogError, match="symlink"):
        enable(installed)
    with pytest.raises(WatchDogError, match="symlink"):
        disable(installed)
    with pytest.raises(WatchDogError, match="symlink"):
        status(installed)
