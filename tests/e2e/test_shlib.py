"""End-to-end tests for ``lion shlib`` with a real zsh."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


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
    (home / ".zshrc").write_text(
        'export ORIGINAL=1\nprintf "original\\n" >> "$HOME/order.log"\n',
        encoding="utf-8",
    )
    return home


def test_install_bootstraps_exports_and_shlibs(tmp_path: Path) -> None:
    """A real zsh loads the copied original, exports and sorted shlib files."""
    home = _make_home(tmp_path)

    installed = _lion(home, "shlib", "install")
    assert installed.returncode == 0, installed.stderr

    (home / ".shlib" / "exports" / "GREETING").write_text("hello world\n", encoding="utf-8")
    (home / ".shlib" / "shlibs" / "01-extra.sh").write_text(
        'printf "extra\\n" >> "$HOME/order.log"\nexport FROM_SHLIB=yes\n',
        encoding="utf-8",
    )

    sourced = _zsh(home, 'source "$HOME/.zshrc"; printf "%s|%s|%s\\n" "$ORIGINAL" "$GREETING" "$FROM_SHLIB"')
    assert sourced.returncode == 0, sourced.stderr
    assert sourced.stdout.strip() == "1|hello world|yes"
    assert sourced.stderr == ""

    order = (home / "order.log").read_text(encoding="utf-8").splitlines()
    assert order == ["original", "extra"]


def test_drift_uninstall_and_reinstall(tmp_path: Path) -> None:
    """Drift needs --force; uninstall flattens and a new install works again."""
    home = _make_home(tmp_path)
    assert _lion(home, "shlib", "install").returncode == 0
    (home / ".shlib" / "shlibs" / "01-extra.sh").write_text("export FROM_SHLIB=yes\n", encoding="utf-8")

    zshrc = home / ".zshrc"
    zshrc.write_text(zshrc.read_text(encoding="utf-8") + "# drift for test\n", encoding="utf-8")

    drift = _zsh(home, 'source "$HOME/.zshrc"; printf "%s\\n" "$ORIGINAL"')
    assert drift.returncode == 0
    assert "drift for test" in drift.stdout + drift.stderr

    rejected = _lion(home, "shlib", "uninstall")
    assert rejected.returncode == 1
    assert "error:" in rejected.stderr

    forced = _lion(home, "shlib", "uninstall", "--force")
    assert forced.returncode == 0, forced.stderr

    flattened = zshrc.read_text(encoding="utf-8")
    assert "SHLIB for zsh" not in flattened
    assert "export ORIGINAL=1" in flattened
    assert "export FROM_SHLIB=yes" in flattened
    backups = list(home.glob(".shlib.uninstalled-*"))
    assert len(backups) == 1
    assert (backups[0] / "shlibs" / "01-extra.sh").is_file()

    reinstalled = _lion(home, "shlib", "install")
    assert reinstalled.returncode == 0, reinstalled.stderr

    sourced = _zsh(home, 'source "$HOME/.zshrc"; printf "%s|%s\\n" "$ORIGINAL" "$FROM_SHLIB"')
    assert sourced.returncode == 0, sourced.stderr
    assert sourced.stdout.strip() == "1|yes"
    assert sourced.stderr == ""


@pytest.mark.parametrize("arguments", [("shlib", "install", "--help"), ("shlib", "uninstall", "--help")])
def test_help_is_available_in_installed_package(tmp_path: Path, arguments: tuple[str, ...]) -> None:
    """The installed console script exposes the shlib commands."""
    home = _make_home(tmp_path)
    result = _lion(home, *arguments)

    assert result.returncode == 0
    assert "--home" in result.stdout
    assert "--dry-run" in result.stdout
