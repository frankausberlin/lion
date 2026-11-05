"""Tests for ``lion shlib status``."""

from __future__ import annotations

import io
import re
import shutil
import stat
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

import lion.shlib.cli as shlib_cli
from lion.cli import app
from lion.shlib.bootstrap import BOOTSTRAP
from lion.shlib.install import apply_install, plan_install
from lion.shlib.it import UNSORTED_NAME
from lion.shlib.layout import ShlibLayout
from lion.shlib.status import (
    INCONSISTENT,
    INSTALLED,
    NOT_INSTALLED,
    StatusReport,
    collect_status,
)

runner = CliRunner()

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _install(layout: ShlibLayout) -> None:
    """Install a fresh shlib tree."""
    apply_install(plan_install(layout))


def _append(home: Path, data: bytes) -> None:
    """Append bytes to the current ``.zshrc``."""
    zshrc = home / ".zshrc"
    zshrc.write_bytes(zshrc.read_bytes() + data)


def _target(layout: ShlibLayout) -> Path:
    """Return the fallback shlib file."""
    return layout.shlibs / UNSORTED_NAME


def _write_export(layout: ShlibLayout, name: str, data: str, *, mode: int = 0o644) -> Path:
    """Write an export file with an explicit mode independent of the umask."""
    path = layout.exports / name
    path.write_text(data, encoding="utf-8")
    path.chmod(mode)
    return path


def _drift_report(layout: ShlibLayout) -> StatusReport:
    """Return a report with a yellow drift state."""
    _install(layout)
    _append(layout.home, b"export TAIL=1\n")
    return collect_status(layout)


def test_status_not_installed(home: Path, layout: ShlibLayout) -> None:
    """An unmanaged home is reported without an issue."""
    report = collect_status(layout)

    assert report.installation == NOT_INSTALLED
    assert report.zshrc == "ok"
    assert report.unsorted == "no"
    assert not report.unsorted_exists
    assert report.exports == 0
    assert report.shlibs == 0
    assert report.backups == 0
    assert report.issues == ()
    assert report.exit_code == 1


def test_status_installed_without_warnings(home: Path, layout: ShlibLayout) -> None:
    """A clean installation is green and exits 0."""
    _install(layout)

    report = collect_status(layout)

    assert report.installation == INSTALLED
    assert report.zshrc == "ok"
    assert report.zshrc_detail is None
    assert report.exports == 0
    assert report.owner_only_exports == 0
    assert report.shlibs == 1
    assert report.unsorted == "no"
    assert report.backups == 0
    assert report.issues == ()
    assert report.exit_code == 0


def test_status_drift_with_tail(home: Path, layout: ShlibLayout) -> None:
    """Appended lines are drift with a line count and exit 1."""
    _install(layout)
    _append(home, b"export TAIL=1\nprintf 'tail\\n'\n")

    report = collect_status(layout)

    assert report.zshrc == "drift"
    assert report.zshrc_detail == "2 appended line(s)"
    assert report.exit_code == 1


def test_status_drift_without_tail(home: Path, layout: ShlibLayout) -> None:
    """Other changes inside the block are reported as drift."""
    _install(layout)
    (home / ".zshrc").write_bytes(BOOTSTRAP.encode().replace(b"SHLIB for zsh", b"SHLIB for zsh changed", 1))

    report = collect_status(layout)

    assert report.zshrc == "drift"
    assert report.zshrc_detail == "other changes"
    assert report.exit_code == 1


def test_status_managed_block_missing(home: Path, layout: ShlibLayout) -> None:
    """A removed bootstrap is drift with a specific reason."""
    _install(layout)
    (home / ".zshrc").write_bytes(b"export UNMANAGED=1\n")

    report = collect_status(layout)

    assert report.zshrc == "drift"
    assert report.zshrc_detail == "managed block missing"
    assert report.exit_code == 1


def test_status_whitespace_drift(home: Path, layout: ShlibLayout) -> None:
    """Whitespace-only appended content is still drift."""
    _install(layout)
    _append(home, b"  \n\t\n")

    report = collect_status(layout)

    assert report.zshrc == "drift"
    assert report.zshrc_detail == "whitespace only"
    assert report.exit_code == 1


def test_status_unsorted_missing(home: Path, layout: ShlibLayout) -> None:
    """Without the fallback file, unsorted is no and the status stays green."""
    _install(layout)

    report = collect_status(layout)

    assert report.unsorted == "no"
    assert not report.unsorted_exists
    assert report.exit_code == 0


def test_status_unsorted_empty_is_yellow(home: Path, layout: ShlibLayout) -> None:
    """An empty fallback file is reported and forces exit 1."""
    _install(layout)
    _target(layout).write_bytes(b"")

    report = collect_status(layout)

    assert report.unsorted == "yes (empty)"
    assert report.unsorted_exists
    assert report.exit_code == 1


def test_status_unsorted_with_content(home: Path, layout: ShlibLayout) -> None:
    """A non-empty fallback file reports its size and forces exit 1."""
    _install(layout)
    _target(layout).write_bytes(b"x" * 128)

    report = collect_status(layout)

    assert report.unsorted == "yes (128 bytes)"
    assert report.unsorted_exists
    assert report.exit_code == 1


def test_status_counts_exports_shlibs_and_backups(home: Path, layout: ShlibLayout) -> None:
    """Counters include only entries the bootstrap actually loads."""
    _install(layout)
    _write_export(layout, "PUBLIC", "a\n", mode=0o644)
    _write_export(layout, "GROUP", "b\n", mode=0o640)
    _write_export(layout, "SECRET", "c\n", mode=0o600)
    _write_export(layout, ".hidden", "d\n", mode=0o600)
    (layout.shlibs / "01-first.sh").write_text("export FIRST=1\n", encoding="utf-8")
    (layout.shlibs / "02-second.sh").write_text("export SECOND=2\n", encoding="utf-8")
    (layout.shlibs / "1-single.sh").write_text("export SINGLE=1\n", encoding="utf-8")
    (layout.shlibs / "not-loaded.sh").write_text("export NOT_LOADED=1\n", encoding="utf-8")
    (home / ".shlib.uninstalled-20260101T000000Z").mkdir()

    report = collect_status(layout)

    assert report.exports == 3
    assert report.owner_only_exports == 1
    assert report.shlibs == 3
    assert report.backups == 1
    assert report.exit_code == 0


def test_status_does_not_read_export_contents(home: Path, layout: ShlibLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    """Export files are counted by metadata only."""
    _install(layout)
    _write_export(layout, "SECRET", "hidden-value\n", mode=0o600)
    original = Path.read_bytes

    def guarded_read(self: Path) -> bytes:
        if self.parent == layout.exports:
            raise AssertionError("status must not read export contents")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", guarded_read)

    report = collect_status(layout)

    assert report.exports == 1
    assert report.owner_only_exports == 1


def test_status_missing_lock_is_inconsistent(home: Path, layout: ShlibLayout) -> None:
    """A missing lock is inconsistent and exits 2."""
    _install(layout)
    layout.lock_file.unlink()

    report = collect_status(layout)

    assert report.installation == INCONSISTENT
    assert report.exit_code == 2
    assert any("lock" in issue for issue in report.issues)


def test_status_missing_zshrc_is_inconsistent(home: Path, layout: ShlibLayout) -> None:
    """A missing .zshrc next to an installed tree is inconsistent."""
    _install(layout)
    (home / ".zshrc").unlink()

    report = collect_status(layout)

    assert report.installation == INCONSISTENT
    assert report.zshrc == "missing"
    assert report.exit_code == 2
    assert any(".zshrc" in issue for issue in report.issues)


def test_status_root_symlink_is_inconsistent(home: Path, layout: ShlibLayout) -> None:
    """A symlinked root is not a supported installation."""
    outside = home / "outside"
    outside.mkdir()
    layout.root.symlink_to(outside)

    report = collect_status(layout)

    assert report.installation == INCONSISTENT
    assert report.exit_code == 2
    assert any("symlink" in issue for issue in report.issues)


def test_status_root_file_is_inconsistent(home: Path, layout: ShlibLayout) -> None:
    """A regular file at the root path is inconsistent."""
    layout.root.write_text("not a directory\n", encoding="utf-8")

    report = collect_status(layout)

    assert report.installation == INCONSISTENT
    assert report.exit_code == 2
    assert any("not a directory" in issue for issue in report.issues)


def test_status_missing_root_with_bootstrap_is_inconsistent(home: Path, layout: ShlibLayout) -> None:
    """A bootstrap marker without the shlib root is inconsistent."""
    (home / ".zshrc").write_bytes(BOOTSTRAP.encode())

    report = collect_status(layout)

    assert report.installation == INCONSISTENT
    assert report.exit_code == 2
    assert any("is missing" in issue for issue in report.issues)


def test_status_zshrc_directory_is_inconsistent(home: Path, layout: ShlibLayout) -> None:
    """A directory at the .zshrc path is inconsistent."""
    _install(layout)
    (home / ".zshrc").unlink()
    (home / ".zshrc").mkdir()

    report = collect_status(layout)

    assert report.installation == INCONSISTENT
    assert report.zshrc == "inconsistent"
    assert report.exit_code == 2


def test_status_zshrc_symlink_without_root(home: Path, layout: ShlibLayout) -> None:
    """A symlinked .zshrc without a root is an issue, not a crash."""
    target = home / "real.zshrc"
    target.write_text("export ORIGINAL=1\n", encoding="utf-8")
    (home / ".zshrc").unlink()
    (home / ".zshrc").symlink_to(target)

    report = collect_status(layout)

    assert report.installation == NOT_INSTALLED
    assert report.zshrc == "inconsistent"
    assert report.exit_code == 2
    assert any("symlink" in issue for issue in report.issues)


def test_status_lock_symlink_is_inconsistent(home: Path, layout: ShlibLayout) -> None:
    """A symlinked lock file is inconsistent."""
    _install(layout)
    target = home / "real-lock"
    layout.lock_file.rename(target)
    layout.lock_file.symlink_to(target)

    report = collect_status(layout)

    assert report.installation == INCONSISTENT
    assert report.exit_code == 2
    assert any("symlink" in issue for issue in report.issues)


def test_status_lock_directory_is_inconsistent(home: Path, layout: ShlibLayout) -> None:
    """A directory at the lock path is inconsistent."""
    _install(layout)
    layout.lock_file.unlink()
    layout.lock_file.mkdir()

    report = collect_status(layout)

    assert report.installation == INCONSISTENT
    assert report.exit_code == 2
    assert any("not a regular file" in issue for issue in report.issues)


def test_status_unreadable_lock_is_inconsistent(
    home: Path, layout: ShlibLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable lock becomes an issue."""
    _install(layout)
    original = Path.read_bytes

    def guarded_read(self: Path) -> bytes:
        if self == layout.lock_file:
            raise OSError("denied")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", guarded_read)

    report = collect_status(layout)

    assert report.installation == INSTALLED
    assert report.exit_code == 2
    assert any("cannot read" in issue for issue in report.issues)


def test_status_missing_lock_and_unmanaged_zshrc(home: Path, layout: ShlibLayout) -> None:
    """Without a lock and without a bootstrap, the zshrc cannot be classified."""
    _install(layout)
    layout.lock_file.unlink()
    (home / ".zshrc").write_bytes(b"export UNMANAGED=1\n")

    report = collect_status(layout)

    assert report.installation == INCONSISTENT
    assert report.zshrc == "ok"
    assert report.exit_code == 2
    assert any("lock" in issue for issue in report.issues)


def test_status_zshrc_symlink_is_inconsistent(home: Path, layout: ShlibLayout) -> None:
    """A symlinked .zshrc is inconsistent."""
    _install(layout)
    zshrc = home / ".zshrc"
    real = home / "real.zshrc"
    zshrc.rename(real)
    zshrc.symlink_to(real)

    report = collect_status(layout)

    assert report.installation == INCONSISTENT
    assert report.zshrc == "inconsistent"
    assert report.exit_code == 2


def test_status_exports_file_is_an_issue(home: Path, layout: ShlibLayout) -> None:
    """A regular file at exports/ is inconsistent and exits 2."""
    _install(layout)
    shutil.rmtree(layout.exports)
    layout.exports.write_text("not a directory\n", encoding="utf-8")

    report = collect_status(layout)

    assert report.exit_code == 2
    assert any("export" in issue for issue in report.issues)


def test_status_exports_symlink_is_an_issue(home: Path, layout: ShlibLayout) -> None:
    """A symlinked exports directory is refused."""
    _install(layout)
    shutil.rmtree(layout.exports)
    outside = home / "outside-exports"
    outside.mkdir()
    layout.exports.symlink_to(outside)

    report = collect_status(layout)

    assert report.exit_code == 2
    assert any("export" in issue for issue in report.issues)


def test_status_unreadable_exports_directory(home: Path, layout: ShlibLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    """A listing error becomes an issue instead of a traceback."""
    _install(layout)
    original = Path.iterdir

    def fail_iterdir(self: Path) -> Iterator[Path]:
        if self == layout.exports:
            raise OSError("denied")
        return original(self)

    monkeypatch.setattr(Path, "iterdir", fail_iterdir)

    report = collect_status(layout)

    assert report.exit_code == 2
    assert any("cannot list" in issue for issue in report.issues)


def test_status_shlibs_symlink_is_inconsistent(home: Path, layout: ShlibLayout) -> None:
    """A symlinked shlibs directory is inconsistent."""
    _install(layout)
    shutil.rmtree(layout.shlibs)
    outside = home / "outside-shlibs"
    outside.mkdir()
    layout.shlibs.symlink_to(outside)

    report = collect_status(layout)

    assert report.installation == INCONSISTENT
    assert report.shlibs == 0
    assert report.exit_code == 2


def test_status_unsorted_symlink_is_an_issue(home: Path, layout: ShlibLayout) -> None:
    """A symlinked fallback file is reported and refused."""
    _install(layout)
    outside = home / "outside.sh"
    outside.write_text("export OUTSIDE=1\n", encoding="utf-8")
    _target(layout).symlink_to(outside)

    report = collect_status(layout)

    assert report.unsorted == "yes"
    assert report.unsorted_exists
    assert report.exit_code == 2
    assert any("symlink" in issue for issue in report.issues)


def test_status_unsorted_directory_is_an_issue(home: Path, layout: ShlibLayout) -> None:
    """A directory at the fallback name is reported and refused."""
    _install(layout)
    _target(layout).mkdir()

    report = collect_status(layout)

    assert report.unsorted == "yes"
    assert report.exit_code == 2
    assert any("directory" in issue for issue in report.issues)


def test_status_exports_subdirectory_is_ignored(home: Path, layout: ShlibLayout) -> None:
    """Only regular export files are counted."""
    _install(layout)
    (layout.exports / "nested").mkdir()

    report = collect_status(layout)

    assert report.exports == 0
    assert report.exit_code == 0


def test_status_unreadable_shlibs_directory(home: Path, layout: ShlibLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    """A listing error for shlibs becomes an issue."""
    _install(layout)
    original = Path.iterdir

    def fail_iterdir(self: Path) -> Iterator[Path]:
        if self == layout.shlibs:
            raise OSError("denied")
        return original(self)

    monkeypatch.setattr(Path, "iterdir", fail_iterdir)

    report = collect_status(layout)

    assert report.shlibs == 0
    assert report.exit_code == 2
    assert any("cannot list" in issue for issue in report.issues)


def test_status_unreadable_home(home: Path, layout: ShlibLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    """A listing error for the home becomes an issue."""
    _install(layout)
    original = Path.iterdir

    def fail_iterdir(self: Path) -> Iterator[Path]:
        if self == home:
            raise OSError("denied")
        return original(self)

    monkeypatch.setattr(Path, "iterdir", fail_iterdir)

    report = collect_status(layout)

    assert report.backups == 0
    assert report.exit_code == 2
    assert any("cannot list" in issue for issue in report.issues)


def test_status_tail_without_final_newline(home: Path, layout: ShlibLayout) -> None:
    """A last line without newline still counts as one line."""
    _install(layout)
    _append(home, b"export TAIL=1")

    report = collect_status(layout)

    assert report.zshrc == "drift"
    assert report.zshrc_detail == "1 appended line(s)"
    assert report.exit_code == 1


def test_status_does_not_print_export_names_or_values(home: Path, layout: ShlibLayout) -> None:
    """The status output never reveals export names or values."""
    _install(layout)
    _write_export(layout, "TOPSECRET", "hunter2-value\n", mode=0o600)

    result = runner.invoke(app, ["shlib", "status", "--home", str(home)])

    assert result.exit_code == 0
    assert "TOPSECRET" not in result.output
    assert "hunter2-value" not in result.output
    assert "exports: 1 (owner-only: 1)" in result.output


def test_status_cli_clean_has_no_ansi(home: Path, layout: ShlibLayout) -> None:
    """A non-interactive CLI run contains no ANSI codes."""
    _install(layout)

    result = runner.invoke(app, ["shlib", "status", "--home", str(home)])

    assert result.exit_code == 0
    assert "\x1b[" not in result.output
    assert "installation: installed" in result.output
    assert "zshrc: ok" in result.output
    assert "unsorted: no" in result.output


def test_status_cli_drift_is_plain_without_tty(home: Path, layout: ShlibLayout) -> None:
    """Drift is visible as text and colorless when stdout is not a tty."""
    _install(layout)
    _append(home, b"export TAIL=1\n")

    result = runner.invoke(app, ["shlib", "status", "--home", str(home)])

    assert result.exit_code == 1
    assert "\x1b[" not in result.output
    assert "zshrc: drift (1 appended line(s))" in result.output


def test_status_no_color_option(home: Path, layout: ShlibLayout) -> None:
    """--no-color disables colors."""
    _install(layout)

    result = runner.invoke(app, ["shlib", "status", "--home", str(home), "--no-color"])

    assert result.exit_code == 0
    assert "\x1b[" not in result.output


def test_status_helpers_honor_color_sources(home: Path, layout: ShlibLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit disabling, NO_COLOR and a non-tty all suppress colors."""
    assert not shlib_cli.status_color_enabled(True)

    monkeypatch.setenv("NO_COLOR", "")
    assert not shlib_cli.status_color_enabled(False)

    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert not shlib_cli.status_color_enabled(False)


def test_status_renderer_marks_drift_yellow(home: Path, layout: ShlibLayout) -> None:
    """The color helper styles drift while the state word stays visible."""
    report = _drift_report(layout)

    colored = "\n".join(shlib_cli.render_status(report, color=True))
    plain = "\n".join(shlib_cli.render_status(report, color=False))

    assert "\x1b[33m" in colored
    assert "drift (1 appended line(s))" in colored
    assert _ANSI_ESCAPE.sub("", colored) == plain
    assert "\x1b[" not in plain


def test_status_cli_help_lists_options() -> None:
    """The help text documents --home and --no-color."""
    result = runner.invoke(app, ["shlib", "status", "--help"])

    assert result.exit_code == 0
    assert "--home" in result.output
    assert "--no-color" in result.output


def test_status_marks_owner_only_by_mode(home: Path, layout: ShlibLayout) -> None:
    """Only files without group/other permission bits are owner-only."""
    _install(layout)
    _write_export(layout, "PUBLIC", "x\n", mode=0o644)
    _write_export(layout, "GROUP", "x\n", mode=0o640)
    _write_export(layout, "SECRET", "x\n", mode=0o400)

    report = collect_status(layout)

    assert report.exports == 3
    assert report.owner_only_exports == 1
    assert stat.S_IMODE((layout.exports / "SECRET").stat().st_mode) == 0o400
