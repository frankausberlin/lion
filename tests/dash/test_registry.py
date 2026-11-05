"""Tests for the dash registry."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest

from lion.dash.errors import DashError
from lion.dash.registry import (
    KIND_BROKEN,
    KIND_DIR,
    KIND_FILE,
    KIND_NOT_A_LINK,
    KIND_OTHER,
    list_entries,
    lock_entry,
    unlock_entry,
    validate_name,
)
from lion.shlib.layout import ShlibLayout


def _link(layout: ShlibLayout, name: str, target: Path) -> Path:
    """Create a dash symlink and return its path."""
    path = layout.dash / name
    path.symlink_to(target)
    return path


def _mode(path: Path) -> int:
    """Return permission bits."""
    return stat.S_IMODE(path.stat().st_mode)


def test_list_entries_reports_types_locks_and_sorting(installed: ShlibLayout, home: Path) -> None:
    """Entries are classified by metadata and sorted by name."""
    file_target = home / "target"
    file_target.write_text("data\n", encoding="utf-8")
    directory_target = home / "folder"
    directory_target.mkdir()
    _link(installed, "zeta", file_target)
    _link(installed, "alpha", directory_target)
    _link(installed, "broken", home / "missing")
    (installed.dash / "plain").write_text("not a link\n", encoding="utf-8")
    fifo = home / "pipe"
    os.mkfifo(fifo)
    _link(installed, "pipe", fifo)
    installed.lock_for("zeta").write_bytes(b"lock")

    entries = list_entries(installed)

    assert [entry.name for entry in entries] == ["alpha", "broken", "pipe", "plain", "zeta"]
    by_name = {entry.name: entry for entry in entries}
    assert by_name["alpha"].kind == KIND_DIR
    assert by_name["alpha"].target == directory_target
    assert not by_name["alpha"].locked
    assert by_name["broken"].kind == KIND_BROKEN
    assert by_name["pipe"].kind == KIND_OTHER
    assert by_name["plain"].kind == KIND_NOT_A_LINK
    assert by_name["plain"].target is None
    assert by_name["zeta"].kind == KIND_FILE
    assert by_name["zeta"].target == file_target
    assert by_name["zeta"].locked


def test_list_entries_hides_dotfiles(installed: ShlibLayout, home: Path) -> None:
    """Hidden direct children are ignored."""
    target = home / "target"
    target.write_text("data\n", encoding="utf-8")
    _link(installed, ".hidden", target)

    assert list_entries(installed) == ()


def test_list_entries_empty(installed: ShlibLayout) -> None:
    """An empty dash directory has no entries."""
    assert list_entries(installed) == ()


def test_list_entries_missing_dash(installed: ShlibLayout) -> None:
    """A missing dash directory points to install."""
    shutil.rmtree(installed.dash)

    with pytest.raises(DashError, match="lion shlib install"):
        list_entries(installed)


def test_list_entries_symlinked_dash(installed: ShlibLayout, home: Path) -> None:
    """A symlinked dash directory is refused."""
    outside = home / "outside-dash"
    outside.mkdir()
    shutil.rmtree(installed.dash)
    installed.dash.symlink_to(outside)

    with pytest.raises(DashError, match="symlink"):
        list_entries(installed)


def test_list_entries_dash_file(installed: ShlibLayout) -> None:
    """A regular file at the dash path is refused."""
    shutil.rmtree(installed.dash)
    installed.dash.write_text("not a directory\n", encoding="utf-8")

    with pytest.raises(DashError, match="not a directory"):
        list_entries(installed)


def test_list_entries_unreadable_dash(installed: ShlibLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    """A listing error becomes a dash error."""
    original = Path.iterdir

    def fail_iterdir(self: Path) -> object:
        if self == installed.dash:
            raise OSError("denied")
        return original(self)

    monkeypatch.setattr(Path, "iterdir", fail_iterdir)

    with pytest.raises(DashError, match="cannot list"):
        list_entries(installed)


def test_lock_entry_writes_private_copy(installed: ShlibLayout, home: Path) -> None:
    """Locking copies bytes exactly and uses mode 0600."""
    target = home / "target"
    target.write_bytes(b"secret\n")
    entry = _link(installed, "conf", target)

    result = lock_entry(installed, "conf")

    lock = installed.lock_for("conf")
    assert lock.read_bytes() == target.read_bytes()
    assert _mode(lock) == 0o600
    assert result.name == "conf"
    assert result.target == target.resolve()
    assert result.size == len(target.read_bytes())
    assert len(result.sha256) == 64
    assert not result.updated
    assert entry.is_symlink()


def test_lock_entry_overwrites_existing_lock(installed: ShlibLayout, home: Path) -> None:
    """An existing regular lock is overwritten without a prompt."""
    target = home / "target"
    target.write_bytes(b"new\n")
    _link(installed, "conf", target)
    lock = installed.lock_for("conf")
    lock.write_bytes(b"old\n")
    lock.chmod(0o644)

    result = lock_entry(installed, "conf")

    assert result.updated
    assert lock.read_bytes() == b"new\n"
    assert _mode(lock) == 0o600


def test_lock_entry_resolves_relative_link(installed: ShlibLayout) -> None:
    """A relative link target is resolved for the result and error display."""
    target = installed.home / "target"
    target.write_bytes(b"x\n")
    (installed.dash / "conf").symlink_to(Path("../..") / "target")

    result = lock_entry(installed, "conf")

    assert result.target == target.resolve()


@pytest.mark.parametrize("name", ["", ".hidden", "nested/name", "line\nbreak", "tab\tname"])
def test_lock_entry_rejects_invalid_names(installed: ShlibLayout, name: str) -> None:
    """Invalid names never reach the filesystem."""
    with pytest.raises(DashError):
        lock_entry(installed, name)


def test_validate_name_accepts_plain_names() -> None:
    """A normal direct name is valid."""
    validate_name("config")
    validate_name("name with space")
    validate_name("ümlaut")


def test_lock_entry_rejects_missing_entry(installed: ShlibLayout) -> None:
    """A missing dash entry is refused."""
    with pytest.raises(DashError, match="does not exist"):
        lock_entry(installed, "missing")


def test_lock_entry_rejects_regular_file(installed: ShlibLayout) -> None:
    """A regular file inside dash/ is not lockable."""
    (installed.dash / "plain").write_text("x\n", encoding="utf-8")

    with pytest.raises(DashError, match="not a symlink"):
        lock_entry(installed, "plain")


def test_lock_entry_rejects_directory(installed: ShlibLayout, home: Path) -> None:
    """A link to a directory is not lockable."""
    folder = home / "folder"
    folder.mkdir()
    _link(installed, "folder", folder)

    with pytest.raises(DashError, match="regular file"):
        lock_entry(installed, "folder")


def test_lock_entry_rejects_broken_link(installed: ShlibLayout, home: Path) -> None:
    """A broken link is refused."""
    _link(installed, "broken", home / "missing")

    with pytest.raises(DashError, match="broken"):
        lock_entry(installed, "broken")


def test_lock_entry_rejects_special_file(installed: ShlibLayout, home: Path) -> None:
    """A link to a FIFO is not lockable."""
    fifo = home / "pipe"
    os.mkfifo(fifo)
    _link(installed, "pipe", fifo)

    with pytest.raises(DashError, match="regular file"):
        lock_entry(installed, "pipe")


def test_lock_entry_rejects_unreadable_target(
    installed: ShlibLayout, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read error is reported without a traceback."""
    target = home / "target"
    target.write_bytes(b"x\n")
    entry = _link(installed, "conf", target)
    original = Path.read_bytes

    def fail_read(self: Path) -> bytes:
        if self == entry:
            raise OSError("denied")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", fail_read)

    with pytest.raises(DashError, match="cannot read"):
        lock_entry(installed, "conf")


def test_lock_entry_rejects_symlinked_lock(installed: ShlibLayout, home: Path) -> None:
    """A symlink at the lock path is refused."""
    target = home / "target"
    target.write_bytes(b"x\n")
    _link(installed, "conf", target)
    other = home / "other-lock"
    other.write_bytes(b"other")
    installed.lock_for("conf").symlink_to(other)

    with pytest.raises(DashError, match="symlink"):
        lock_entry(installed, "conf")


def test_lock_entry_rejects_lock_directory(installed: ShlibLayout, home: Path) -> None:
    """A directory at the lock path is refused."""
    target = home / "target"
    target.write_bytes(b"x\n")
    _link(installed, "conf", target)
    installed.lock_for("conf").mkdir()

    with pytest.raises(DashError, match="not a regular file"):
        lock_entry(installed, "conf")


def test_lock_entry_missing_lock_directory(installed: ShlibLayout, home: Path) -> None:
    """A missing lock directory names the install step."""
    target = home / "target"
    target.write_bytes(b"x\n")
    _link(installed, "conf", target)
    shutil.rmtree(installed.locks)

    with pytest.raises(DashError, match="lion shlib install"):
        lock_entry(installed, "conf")


def test_lock_entry_symlinked_lock_directory(installed: ShlibLayout, home: Path) -> None:
    """A symlinked lock directory is refused."""
    target = home / "target"
    target.write_bytes(b"x\n")
    _link(installed, "conf", target)
    outside = home / "outside-locks"
    outside.mkdir()
    shutil.rmtree(installed.locks)
    installed.locks.symlink_to(outside)

    with pytest.raises(DashError, match="symlink"):
        lock_entry(installed, "conf")


def test_lock_entry_write_error(installed: ShlibLayout, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed write becomes a dash error."""
    target = home / "target"
    target.write_bytes(b"x\n")
    _link(installed, "conf", target)

    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("lion.dash.registry.atomic_write", fail_write)

    with pytest.raises(DashError, match="cannot write"):
        lock_entry(installed, "conf")


def test_unlock_entry_removes_only_lock(installed: ShlibLayout, home: Path) -> None:
    """Unlocking leaves the link and target untouched."""
    target = home / "target"
    target.write_bytes(b"x\n")
    entry = _link(installed, "conf", target)
    lock = installed.lock_for("conf")
    lock.write_bytes(b"x\n")

    assert unlock_entry(installed, "conf")

    assert not lock.exists()
    assert entry.is_symlink()
    assert target.exists()


def test_unlock_entry_missing_lock_is_noop(installed: ShlibLayout) -> None:
    """A missing lock is a no-op."""
    assert not unlock_entry(installed, "conf")


def test_unlock_entry_missing_lock_directory_is_noop(installed: ShlibLayout) -> None:
    """A missing lock directory is a no-op."""
    shutil.rmtree(installed.locks)

    assert not unlock_entry(installed, "conf")


def test_unlock_entry_removes_orphan_lock(installed: ShlibLayout) -> None:
    """A lock without a dash entry can be removed."""
    installed.lock_for("orphan").write_bytes(b"x")

    assert unlock_entry(installed, "orphan")
    assert not installed.lock_for("orphan").exists()


def test_unlock_entry_removes_symlinked_lock(installed: ShlibLayout, home: Path) -> None:
    """A symlink at the lock path is removed as an entry."""
    other = home / "other"
    other.write_bytes(b"x")
    lock = installed.lock_for("conf")
    lock.symlink_to(other)

    assert unlock_entry(installed, "conf")
    assert not lock.exists()
    assert other.exists()


def test_unlock_entry_rejects_lock_directory(installed: ShlibLayout) -> None:
    """A directory at the lock path is an error."""
    installed.lock_for("conf").mkdir()

    with pytest.raises(DashError, match="directory"):
        unlock_entry(installed, "conf")


def test_unlock_entry_rejects_symlinked_lock_directory(installed: ShlibLayout, home: Path) -> None:
    """A symlinked lock directory is refused."""
    outside = home / "outside-locks"
    outside.mkdir()
    shutil.rmtree(installed.locks)
    installed.locks.symlink_to(outside)

    with pytest.raises(DashError, match="symlink"):
        unlock_entry(installed, "conf")


def test_unlock_entry_rejects_lock_file_directory(installed: ShlibLayout) -> None:
    """A regular file at the lock directory path is an error."""
    shutil.rmtree(installed.locks)
    installed.locks.write_text("not a directory\n", encoding="utf-8")

    with pytest.raises(DashError, match="not a directory"):
        unlock_entry(installed, "conf")


def test_unlock_entry_rejects_invalid_name(installed: ShlibLayout) -> None:
    """Invalid names are refused before touching the filesystem."""
    with pytest.raises(DashError):
        unlock_entry(installed, "nested/name")
