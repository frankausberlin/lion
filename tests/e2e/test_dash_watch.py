"""End-to-end tests for the dash register, ``lion watch`` and ``lion watch-dog``."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _lion(home: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the installed lion entry point with ``HOME`` pointed at ``home``."""
    executable = shutil.which("lion")
    assert executable is not None, "lion entry point is not on PATH"
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    return subprocess.run(
        [executable, *arguments],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _zsh(home: Path, script: str) -> subprocess.CompletedProcess[str]:
    """Run a zsh script with ``HOME`` pointed at ``home``."""
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    return subprocess.run(
        ["zsh", "-c", script],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _make_home(root: Path) -> Path:
    """Create a throwaway home with an original ``.zshrc``."""
    home = root / "home"
    home.mkdir()
    (home / ".zshrc").write_text("export ORIGINAL=1\n", encoding="utf-8")
    return home


def test_dash_lock_watch_and_watchdog(tmp_path: Path) -> None:
    """A locked dash entry drifts, runs at shell start and survives uninstall in the backup."""
    home = _make_home(tmp_path)
    assert _lion(home, "shlib", "install").returncode == 0

    target = home / "config"
    target.write_text("alpha\n", encoding="utf-8")
    dash = home / ".shlib" / "dash" / "config"
    dash.symlink_to(target)

    listed = _lion(home, "dash", "list")
    assert listed.returncode == 0, listed.stderr
    assert "config: unlocked, file -> ~/config" in listed.stdout

    locked = _lion(home, "dash", "lock", "config")
    assert locked.returncode == 0, locked.stderr
    assert "locked config: ~/config" in locked.stdout
    assert "alpha" not in locked.stdout
    lock = home / ".shlib" / "locks" / "config.lock"
    assert lock.read_bytes() == b"alpha\n"
    assert (lock.stat().st_mode & 0o777) == 0o600

    listed_locked = _lion(home, "dash", "list")
    assert "config: locked, file -> ~/config" in listed_locked.stdout

    target.write_text("beta\n", encoding="utf-8")

    watched = _lion(home, "watch")
    assert watched.returncode == 0, watched.stderr
    assert "dash: config: changed" in watched.stdout
    assert "--- lock: ~/.shlib/locks/config.lock" in watched.stdout
    assert "+++ target: ~/config" in watched.stdout
    assert "-alpha" in watched.stdout
    assert "+beta" in watched.stdout

    enabled = _lion(home, "watch-dog", "on")
    assert enabled.returncode == 0, enabled.stderr
    watchdog = home / ".shlib" / "shlibs" / "99-lion-watchdog.sh"
    assert watchdog.is_file()
    assert (watchdog.stat().st_mode & 0o777) == 0o644

    started = _zsh(home, 'source "$HOME/.zshrc"')
    assert started.returncode == 0, started.stderr
    assert "dash: config: changed" in started.stdout
    assert "+beta" in started.stdout

    target.write_text("alpha\n", encoding="utf-8")

    clean = _lion(home, "watch")
    assert clean.returncode == 0, clean.stderr
    assert clean.stdout == ""

    silent_start = _zsh(home, 'source "$HOME/.zshrc"')
    assert silent_start.returncode == 0, silent_start.stderr
    assert silent_start.stdout == ""
    assert silent_start.stderr == ""

    state_on = _lion(home, "watch-dog", "status")
    assert state_on.returncode == 0
    assert "watchdog: on" in state_on.stdout

    disabled = _lion(home, "watch-dog", "off")
    assert disabled.returncode == 0, disabled.stderr
    assert not watchdog.exists()

    state_off = _lion(home, "watch-dog", "status")
    assert state_off.returncode == 1
    assert "watchdog: off" in state_off.stdout

    assert _lion(home, "watch-dog", "on").returncode == 0

    uninstalled = _lion(home, "shlib", "uninstall")
    assert uninstalled.returncode == 0, uninstalled.stderr

    backups = list(home.glob(".shlib.uninstalled-*"))
    assert len(backups) == 1
    assert (backups[0] / "dash" / "config").is_symlink()
    assert (backups[0] / "locks" / "config.lock").read_bytes() == b"alpha\n"

    flattened = (home / ".zshrc").read_text(encoding="utf-8")
    assert "lion watch" in flattened

    after_uninstall = _lion(home, "watch")
    assert after_uninstall.returncode == 0, after_uninstall.stderr
    assert after_uninstall.stdout == ""

    flat_start = _zsh(home, 'source "$HOME/.zshrc"')
    assert flat_start.returncode == 0, flat_start.stderr
    assert flat_start.stdout == ""
    assert flat_start.stderr == ""


def test_dash_and_watch_handle_a_missing_layout(tmp_path: Path) -> None:
    """A home without a shlib tree reports a dash hint and a silent watch."""
    home = _make_home(tmp_path)

    listed = _lion(home, "dash", "list")
    assert listed.returncode == 2
    assert "lion shlib install" in listed.stderr

    watched = _lion(home, "watch")
    assert watched.returncode == 0
    assert watched.stdout == ""

    disabled = _lion(home, "watch-dog", "off")
    assert disabled.returncode == 0
    assert "no watchdog file" in disabled.stdout

    missing = _lion(home, "watch-dog", "on")
    assert missing.returncode == 2
    assert "lion shlib install" in missing.stderr

    state = _lion(home, "watch-dog", "status")
    assert state.returncode == 1
    assert "watchdog: off" in state.stdout
