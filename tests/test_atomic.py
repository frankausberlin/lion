"""Tests for the shared atomic replacement write."""

import stat
from pathlib import Path

from lion.program.atomic import write_text_atomic


def test_write_text_atomic_creates_parents_and_private_file(tmp_path: Path) -> None:
    """The file appears with mode 600, parents created, no leftovers."""
    target = tmp_path / "nested" / "dir" / "file.txt"

    assert write_text_atomic(target, "hello\n") == target

    assert target.read_text(encoding="utf-8") == "hello\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert [child.name for child in target.parent.iterdir()] == ["file.txt"]


def test_write_text_atomic_replaces_existing_content(tmp_path: Path) -> None:
    """An existing file is replaced in place without remnants."""
    target = tmp_path / "file.txt"
    target.write_text("old content that is longer", encoding="utf-8")

    write_text_atomic(target, "new\n")

    assert target.read_text(encoding="utf-8") == "new\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert [child.name for child in tmp_path.iterdir()] == ["file.txt"]
