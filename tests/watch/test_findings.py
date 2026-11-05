"""Tests for the dash watch source."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lion.shlib.layout import ShlibLayout
from lion.watch.dash import collect_dash_findings
from lion.watch.errors import WatchError


def _locked(
    layout: ShlibLayout,
    home: Path,
    *,
    name: str = "conf",
    content: bytes = b"hello\n",
) -> Path:
    """Create a dash link and a matching lock copy."""
    target = home / "target"
    target.write_bytes(content)
    (layout.dash / name).symlink_to(target)
    layout.lock_for(name).write_bytes(content)
    return target


def test_clean_state_is_silent(installed: ShlibLayout, home: Path) -> None:
    """Identical lock and target produce no finding."""
    _locked(installed, home)

    assert collect_dash_findings(installed) == ()


def test_drift_renders_unified_diff(installed: ShlibLayout, home: Path) -> None:
    """A changed target produces a unified diff with both paths."""
    target = _locked(installed, home)
    target.write_bytes(b"world\n")

    findings = collect_dash_findings(installed)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.source == "dash"
    assert finding.name == "conf"
    assert finding.kind == "changed"
    assert not finding.is_error
    assert finding.lines == (
        "dash: conf: changed",
        "--- lock: ~/.shlib/locks/conf.lock",
        "+++ target: ~/target",
        "@@ -1 +1 @@",
        "-hello",
        "+world",
    )


def test_unlocked_entry_is_ignored(installed: ShlibLayout, home: Path) -> None:
    """A link without a lock copy is not compared."""
    target = home / "target"
    target.write_text("hello\n", encoding="utf-8")
    (installed.dash / "conf").symlink_to(target)

    assert collect_dash_findings(installed) == ()


def test_missing_dash_is_silent(installed: ShlibLayout) -> None:
    """A missing dash directory is silent."""
    shutil.rmtree(installed.dash)

    assert collect_dash_findings(installed) == ()


def test_missing_locks_is_silent(installed: ShlibLayout, home: Path) -> None:
    """A missing lock directory is silent."""
    target = home / "target"
    target.write_text("x\n", encoding="utf-8")
    (installed.dash / "conf").symlink_to(target)
    shutil.rmtree(installed.locks)

    assert collect_dash_findings(installed) == ()


def test_symlinked_dash_is_error(installed: ShlibLayout, home: Path) -> None:
    """A symlinked dash directory raises a watch error."""
    outside = home / "outside"
    outside.mkdir()
    shutil.rmtree(installed.dash)
    installed.dash.symlink_to(outside)

    with pytest.raises(WatchError, match="symlink"):
        collect_dash_findings(installed)


def test_symlinked_locks_is_error(installed: ShlibLayout, home: Path) -> None:
    """A symlinked lock directory raises a watch error."""
    outside = home / "outside"
    outside.mkdir()
    shutil.rmtree(installed.locks)
    installed.locks.symlink_to(outside)

    with pytest.raises(WatchError, match="symlink"):
        collect_dash_findings(installed)


def test_dash_file_is_error(installed: ShlibLayout) -> None:
    """A regular file at the dash path raises a watch error."""
    shutil.rmtree(installed.dash)
    installed.dash.write_text("x\n", encoding="utf-8")

    with pytest.raises(WatchError, match="not a directory"):
        collect_dash_findings(installed)


def test_locks_file_is_error(installed: ShlibLayout) -> None:
    """A regular file at the lock path raises a watch error."""
    shutil.rmtree(installed.locks)
    installed.locks.write_text("x\n", encoding="utf-8")

    with pytest.raises(WatchError, match="not a directory"):
        collect_dash_findings(installed)


def test_unreadable_dash_is_error(installed: ShlibLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    """A listing error raises a watch error."""
    original = Path.iterdir

    def fail_iterdir(self: Path) -> object:
        if self == installed.dash:
            raise OSError("denied")
        return original(self)

    monkeypatch.setattr(Path, "iterdir", fail_iterdir)

    with pytest.raises(WatchError, match="cannot list"):
        collect_dash_findings(installed)


def test_binary_content_uses_hashes(installed: ShlibLayout, home: Path) -> None:
    """NUL bytes are not diffed; hashes identify both sides."""
    target = _locked(installed, home, content=b"a\x00b")
    target.write_bytes(b"a\x00c")

    findings = collect_dash_findings(installed)

    assert len(findings) == 1
    assert findings[0].kind == "binary_changed"
    assert findings[0].lines[0].startswith("dash: conf: binary content differs (sha256 lock=")
    assert " target=" in findings[0].lines[0]


def test_invalid_utf8_uses_hashes(installed: ShlibLayout, home: Path) -> None:
    """Invalid UTF-8 is treated as binary."""
    target = _locked(installed, home, content=b"\xff")
    target.write_bytes(b"\xfe")

    findings = collect_dash_findings(installed)

    assert "binary content differs" in findings[0].lines[0]


def test_directory_target_is_reported(installed: ShlibLayout, home: Path) -> None:
    """A link to a directory is reported without a diff."""
    folder = home / "folder"
    folder.mkdir()
    (installed.dash / "conf").symlink_to(folder)
    installed.lock_for("conf").write_bytes(b"x")

    findings = collect_dash_findings(installed)

    assert findings[0].lines == ("dash: conf: target is not a regular file: ~/folder",)
    assert findings[0].kind == "not_regular"
    assert not findings[0].is_error


def test_broken_link_is_reported(installed: ShlibLayout, home: Path) -> None:
    """A missing link target is reported as broken."""
    (installed.dash / "conf").symlink_to(home / "missing")
    installed.lock_for("conf").write_bytes(b"x")

    findings = collect_dash_findings(installed)

    assert findings[0].lines == ("dash: conf: broken link: ~/missing",)
    assert findings[0].kind == "broken"
    assert not findings[0].is_error


def test_not_a_symlink_is_reported(installed: ShlibLayout) -> None:
    """A regular file in dash/ with a lock is reported."""
    (installed.dash / "conf").write_text("x\n", encoding="utf-8")
    installed.lock_for("conf").write_bytes(b"x")

    findings = collect_dash_findings(installed)

    assert findings[0].lines == ("dash: conf: not a symlink",)
    assert findings[0].kind == "not_symlink"
    assert not findings[0].is_error


def test_missing_target_during_read_is_reported(
    installed: ShlibLayout, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A target that disappears after stat is reported as missing."""
    _locked(installed, home)
    entry = installed.dash / "conf"
    original = Path.read_bytes

    def fail_read(self: Path) -> bytes:
        if self == entry:
            raise FileNotFoundError("gone")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", fail_read)

    findings = collect_dash_findings(installed)

    assert findings[0].lines == ("dash: conf: target is missing: ~/target",)
    assert findings[0].kind == "missing"


def test_symlinked_lock_is_an_error(installed: ShlibLayout, home: Path) -> None:
    """A symlink at the lock path is an error finding."""
    target = home / "target"
    target.write_text("x\n", encoding="utf-8")
    (installed.dash / "conf").symlink_to(target)
    other = home / "other"
    other.write_bytes(b"x")
    installed.lock_for("conf").symlink_to(other)

    findings = collect_dash_findings(installed)

    assert findings[0].is_error
    assert findings[0].kind == "lock_error"
    assert "lock is not a regular file" in findings[0].lines[0]


def test_lock_directory_is_an_error(installed: ShlibLayout, home: Path) -> None:
    """A directory at the lock path is an error finding."""
    target = home / "target"
    target.write_text("x\n", encoding="utf-8")
    (installed.dash / "conf").symlink_to(target)
    installed.lock_for("conf").mkdir()

    findings = collect_dash_findings(installed)

    assert findings[0].is_error
    assert findings[0].kind == "lock_error"
    assert "lock is not a regular file" in findings[0].lines[0]


def test_unreadable_lock_is_an_error(installed: ShlibLayout, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A lock read error is an error finding and other entries continue."""
    target = _locked(installed, home)
    lock = installed.lock_for("conf")
    original = Path.read_bytes

    def fail_read(self: Path) -> bytes:
        if self == lock:
            raise OSError("denied")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", fail_read)

    findings = collect_dash_findings(installed)

    assert findings[0].is_error
    assert findings[0].kind == "read_error"
    assert findings[0].lines[0].startswith("dash: conf: cannot read lock/target:")
    assert target.exists()


def test_unreadable_target_is_an_error(installed: ShlibLayout, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A target read error is an error finding."""
    _locked(installed, home)
    entry = installed.dash / "conf"
    original = Path.read_bytes

    def fail_read(self: Path) -> bytes:
        if self == entry:
            raise OSError("denied")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", fail_read)

    findings = collect_dash_findings(installed)

    assert findings[0].is_error
    assert findings[0].kind == "read_error"
    assert findings[0].lines[0].startswith("dash: conf: cannot read lock/target:")


def test_findings_are_sorted_by_name(installed: ShlibLayout, home: Path) -> None:
    """Multiple entries are reported in lexical order."""
    for name in ("zeta", "alpha"):
        target = home / f"target-{name}"
        target.write_bytes(b"new\n")
        (installed.dash / name).symlink_to(target)
        installed.lock_for(name).write_bytes(b"old\n")

    findings = collect_dash_findings(installed)

    assert [finding.name for finding in findings] == ["alpha", "zeta"]


def test_trailing_newline_difference_is_a_diff(installed: ShlibLayout, home: Path) -> None:
    """A missing final newline still counts as a change."""
    target = _locked(installed, home, content=b"hello\n")
    target.write_bytes(b"hello")

    findings = collect_dash_findings(installed)

    assert findings[0].lines[0] == "dash: conf: changed"
    assert findings[0].kind == "changed"
    assert "-hello" in findings[0].lines
    assert "+hello" in findings[0].lines


def test_render_diffs_false_emits_only_summary(installed: ShlibLayout, home: Path) -> None:
    """Disabling diffs keeps the finding but drops the changed content."""
    target = _locked(installed, home)
    target.write_bytes(b"world\n")

    findings = collect_dash_findings(installed, render_diffs=False)

    assert len(findings) == 1
    assert findings[0].kind == "changed"
    assert findings[0].lines == ("dash: conf: changed",)
    assert "world" not in "".join(findings[0].lines)
