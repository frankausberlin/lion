"""CLI tests for ``lion dash``."""

from __future__ import annotations

import shutil
from pathlib import Path

from typer.testing import CliRunner

from lion.cli import app
from lion.shlib.layout import ShlibLayout

runner = CliRunner()


def test_dash_list_output(installed: ShlibLayout, home: Path) -> None:
    """The list shows lock state, type and target."""
    target = home / "target"
    target.write_text("data\n", encoding="utf-8")
    (installed.dash / "conf").symlink_to(target)
    installed.lock_for("conf").write_bytes(b"data\n")

    result = runner.invoke(app, ["dash", "list", "--home", str(home)])

    assert result.exit_code == 0
    assert "dash: ~/.shlib/dash" in result.output
    assert "conf: locked, file -> ~/target" in result.output


def test_dash_list_empty(installed: ShlibLayout, home: Path) -> None:
    """An empty register is an explicit success."""
    result = runner.invoke(app, ["dash", "list", "--home", str(home)])

    assert result.exit_code == 0
    assert "no entries" in result.output


def test_dash_list_missing_dash_is_error(home: Path) -> None:
    """A missing dash directory exits 2 with an install hint."""
    result = runner.invoke(app, ["dash", "list", "--home", str(home)])

    assert result.exit_code == 2
    assert "error:" in result.stderr
    assert "lion shlib install" in result.stderr


def test_dash_lock_and_unlock(installed: ShlibLayout, home: Path) -> None:
    """Lock and unlock report their action and keep the link."""
    target = home / "target"
    target.write_text("secret\n", encoding="utf-8")
    (installed.dash / "conf").symlink_to(target)

    locked = runner.invoke(app, ["dash", "lock", "conf", "--home", str(home)])
    assert locked.exit_code == 0
    assert "locked conf: ~/target" in locked.output
    assert "bytes, sha256:" in locked.output
    assert "secret" not in locked.output

    updated = runner.invoke(app, ["dash", "lock", "conf", "--home", str(home)])
    assert updated.exit_code == 0
    assert "updated conf:" in updated.output

    unlocked = runner.invoke(app, ["dash", "unlock", "conf", "--home", str(home)])
    assert unlocked.exit_code == 0
    assert "unlocked conf" in unlocked.output
    assert not installed.lock_for("conf").exists()
    assert (installed.dash / "conf").is_symlink()


def test_dash_unlock_missing_is_noop(installed: ShlibLayout, home: Path) -> None:
    """A missing lock is reported without failing."""
    result = runner.invoke(app, ["dash", "unlock", "conf", "--home", str(home)])

    assert result.exit_code == 0
    assert "no lock for conf" in result.output


def test_dash_lock_error_exits_2(installed: ShlibLayout, home: Path) -> None:
    """A rejected lock exits 2 and names the reason."""
    result = runner.invoke(app, ["dash", "lock", "missing", "--home", str(home)])

    assert result.exit_code == 2
    assert "error:" in result.stderr


def test_dash_help_lists_commands() -> None:
    """The dash help lists its subcommands."""
    result = runner.invoke(app, ["dash", "--help"])

    assert result.exit_code == 0
    assert "list" in result.output
    assert "lock" in result.output
    assert "unlock" in result.output


def test_dash_lock_help() -> None:
    """The lock help documents the argument and --home."""
    result = runner.invoke(app, ["dash", "lock", "--help"])

    assert result.exit_code == 0
    assert "--home" in result.output


def test_dash_symlinked_dash_is_refused(installed: ShlibLayout, home: Path) -> None:
    """A symlinked dash directory exits 2."""
    outside = home / "outside"
    outside.mkdir()
    shutil.rmtree(installed.dash)
    installed.dash.symlink_to(outside)

    result = runner.invoke(app, ["dash", "list", "--home", str(home)])

    assert result.exit_code == 2
    assert "error:" in result.stderr


def test_dash_lock_completion_suggests_lockable_entries(installed: ShlibLayout, home: Path) -> None:
    """Tab completion offers regular-file dash links for ``lock``."""
    target = home / "test2.cfg"
    target.write_text("cfg\n", encoding="utf-8")
    (installed.dash / "config-of-app.cfg").symlink_to(target)
    (installed.dash / "folder").symlink_to(home)
    (installed.dash / "broken").symlink_to(home / "missing")

    result = runner.invoke(
        app,
        [],
        env={
            "_LION_COMPLETE": "complete_zsh",
            "_TYPER_COMPLETE_ARGS": f"lion dash lock --home {home} config-o",
        },
    )

    assert result.exit_code == 0
    assert "config-of-app.cfg" in result.output
    assert "folder" not in result.output
    assert "broken" not in result.output


def test_dash_lock_completion_without_dash_is_silent(home: Path) -> None:
    """A missing dash directory yields an empty completion, not an error."""
    result = runner.invoke(
        app,
        [],
        env={
            "_LION_COMPLETE": "complete_zsh",
            "_TYPER_COMPLETE_ARGS": f"lion dash lock --home {home} conf",
        },
    )

    assert result.exit_code == 0
    assert "error" not in result.stderr
