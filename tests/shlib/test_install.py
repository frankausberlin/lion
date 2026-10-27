"""Tests for ``lion shlib install``."""

from __future__ import annotations

import re
import shutil
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lion.cli import app
from lion.shlib.bootstrap import BOOTSTRAP
from lion.shlib.errors import ShlibError
from lion.shlib.install import apply_install, plan_install
from lion.shlib.layout import ShlibLayout

runner = CliRunner()

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Return text without the ANSI styling Rich adds on terminals."""
    return _ANSI_ESCAPE.sub("", text)


def _mode(path: Path) -> int:
    """Return the permission bits of ``path``."""
    return stat.S_IMODE(path.stat().st_mode)


def test_install_writes_bootstrap_and_keeps_original(home: Path, layout: ShlibLayout) -> None:
    """The lock mirrors the bootstrap and the original keeps content and mode."""
    zshrc = home / ".zshrc"
    zshrc.chmod(0o600)
    original_bytes = zshrc.read_bytes()

    plan = plan_install(layout)
    report = apply_install(plan)

    assert zshrc.read_bytes() == BOOTSTRAP.encode()
    assert layout.lock_file.read_bytes() == BOOTSTRAP.encode()
    assert layout.original.read_bytes() == original_bytes
    assert _mode(layout.original) == 0o600
    assert _mode(zshrc) == 0o600
    assert report.original == layout.original
    assert report.lock_file == layout.lock_file
    assert report.zshrc == zshrc


def test_install_creates_private_directories(home: Path, layout: ShlibLayout) -> None:
    """The shlib root and its subdirectories are created with mode 0700."""
    report = apply_install(plan_install(layout))

    assert report.directories == (layout.root, layout.locks, layout.shlibs, layout.exports)
    for directory in report.directories:
        assert directory.is_dir()
        assert _mode(directory) == 0o700


def test_install_rejects_existing_root(home: Path, layout: ShlibLayout) -> None:
    """An existing shlib root aborts the installation."""
    layout.root.mkdir()
    with pytest.raises(ShlibError, match="already exists"):
        plan_install(layout)


def test_install_rejects_dangling_root_symlink(home: Path, layout: ShlibLayout) -> None:
    """A dangling shlib root symlink counts as existing."""
    layout.root.symlink_to(home / "missing")
    with pytest.raises(ShlibError, match="already exists"):
        plan_install(layout)


def test_install_rejects_missing_home(tmp_path: Path) -> None:
    """A home that does not exist aborts the installation."""
    layout = ShlibLayout(tmp_path / "missing")
    with pytest.raises(ShlibError, match="home directory"):
        plan_install(layout)


def test_install_rejects_missing_zshrc(home: Path, layout: ShlibLayout) -> None:
    """A missing .zshrc aborts the installation."""
    (home / ".zshrc").unlink()
    with pytest.raises(ShlibError, match="does not exist"):
        plan_install(layout)


def test_install_rejects_zshrc_directory(home: Path, layout: ShlibLayout) -> None:
    """A directory named .zshrc is not a valid configuration file."""
    (home / ".zshrc").unlink()
    (home / ".zshrc").mkdir()
    with pytest.raises(ShlibError, match="not a regular file"):
        plan_install(layout)


def test_install_rejects_zshrc_symlink(home: Path, layout: ShlibLayout) -> None:
    """A symlinked .zshrc is not overwritten."""
    target = home / "target.zshrc"
    target.write_text("export ORIGINAL=1\n", encoding="utf-8")
    (home / ".zshrc").unlink()
    (home / ".zshrc").symlink_to(target)

    with pytest.raises(ShlibError, match="symlink"):
        plan_install(layout)


def test_install_rejects_existing_bootstrap_marker(home: Path, layout: ShlibLayout) -> None:
    """A .zshrc with the marker but no shlib tree is already installed."""
    (home / ".zshrc").write_bytes(BOOTSTRAP.encode())
    with pytest.raises(ShlibError, match="already installed"):
        plan_install(layout)


def test_install_rolls_back_when_copy_fails(home: Path, layout: ShlibLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed copy removes the new tree and leaves .zshrc unchanged."""

    def fail_copy(source: Path, target: Path) -> None:
        raise OSError("copy failed")

    zshrc_bytes = (home / ".zshrc").read_bytes()
    monkeypatch.setattr(shutil, "copy2", fail_copy)

    with pytest.raises(ShlibError, match="install failed"):
        apply_install(plan_install(layout))

    assert not layout.root.exists()
    assert (home / ".zshrc").read_bytes() == zshrc_bytes


def test_install_keeps_tree_when_zshrc_replace_fails(
    home: Path, layout: ShlibLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed final replace keeps the prepared tree and the old .zshrc."""
    import lion.shlib.install as install_module

    real_write = install_module.atomic_write

    def fail_write(path: Path, data: bytes, mode: int) -> None:
        if path == layout.zshrc:
            raise OSError("replace failed")
        real_write(path, data, mode)

    zshrc_bytes = (home / ".zshrc").read_bytes()
    monkeypatch.setattr(install_module, "atomic_write", fail_write)

    with pytest.raises(ShlibError, match="prepared shlib tree remains"):
        apply_install(plan_install(layout))

    assert (home / ".zshrc").read_bytes() == zshrc_bytes
    assert layout.original.exists()
    assert layout.lock_file.exists()


def test_install_dry_run_changes_nothing(home: Path) -> None:
    """The CLI dry run only prints the plan."""
    zshrc_bytes = (home / ".zshrc").read_bytes()
    result = runner.invoke(app, ["shlib", "install", "--home", str(home), "--dry-run"])

    assert result.exit_code == 0
    assert "Would copy" in result.output
    assert "Dry run: no changes made." in result.output
    assert not (home / ".shlib").exists()
    assert (home / ".zshrc").read_bytes() == zshrc_bytes


def test_install_cli_success(home: Path) -> None:
    """The CLI installs the layout and prints the created paths."""
    result = runner.invoke(app, ["shlib", "install", "--home", str(home)])

    assert result.exit_code == 0
    assert "Created:" in result.output
    assert "Wrote lock file" in result.output
    assert "source ~/.zshrc" in result.output
    assert (home / ".shlib" / "locks" / "shlib-version-of.zshrc").read_bytes() == BOOTSTRAP.encode()


def test_install_cli_reports_errors(home: Path) -> None:
    """Expected preflight failures exit with status 1."""
    (home / ".zshrc").unlink()
    result = runner.invoke(app, ["shlib", "install", "--home", str(home)])

    assert result.exit_code == 1
    assert "error:" in result.output


def test_install_cli_help_lists_options() -> None:
    """The help text documents --home and --dry-run."""
    result = runner.invoke(app, ["shlib", "install", "--help"])

    assert result.exit_code == 0
    help_text = strip_ansi(result.output)
    assert "--home" in help_text
    assert "--dry-run" in help_text
