"""Tests for shared filesystem helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from lion.shlib.files import exists, line_count, link_target


def test_exists_includes_regular_files_and_dangling_symlinks(tmp_path: Path) -> None:
    """A regular file exists and a dangling symlink counts as an entry."""
    regular = tmp_path / "regular.txt"
    regular.write_text("content\n", encoding="utf-8")
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "missing.txt")

    assert exists(regular)
    assert exists(dangling)
    assert not exists(tmp_path / "absent")


def test_link_target_returns_absolute_target(tmp_path: Path) -> None:
    """An absolute link target is returned unchanged."""
    target = tmp_path / "target.txt"
    target.write_text("content\n", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target)

    assert link_target(link) == target


def test_link_target_resolves_relative_against_parent(tmp_path: Path) -> None:
    """A relative link target is joined with the link's parent."""
    target = tmp_path / "target.txt"
    target.write_text("content\n", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to("target.txt")

    assert link_target(link) == tmp_path / "target.txt"


def test_link_target_propagates_read_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A readlink failure surfaces as ``OSError`` for the caller to translate."""
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "target.txt")

    def failing_readlink(self: Path) -> Path:
        raise OSError("read failed")

    monkeypatch.setattr(Path, "readlink", failing_readlink)

    with pytest.raises(OSError, match="read failed"):
        link_target(link)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"", 0),
        (b"\n", 1),
        (b"one\n", 1),
        (b"one\ntwo\n", 2),
        (b"one\ntwo", 2),
        (b"one\ntwo\n\n", 3),
    ],
)
def test_line_count_matches_newline_semantics(data: bytes, expected: int) -> None:
    """The count includes a final line without a trailing newline."""
    assert line_count(data) == expected
