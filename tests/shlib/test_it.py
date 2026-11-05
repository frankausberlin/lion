"""Tests for ``lion shlib it``."""

from __future__ import annotations

import hashlib
import shutil
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lion.cli import app
from lion.shlib.bootstrap import BOOTSTRAP
from lion.shlib.errors import ShlibError
from lion.shlib.install import apply_install, plan_install
from lion.shlib.it import UNSORTED_NAME, apply_it, plan_it
from lion.shlib.layout import ShlibLayout

runner = CliRunner()


def _install(layout: ShlibLayout) -> None:
    """Install a fresh shlib tree."""
    apply_install(plan_install(layout))


def _target(layout: ShlibLayout) -> Path:
    """Return the fallback shlib file."""
    return layout.shlibs / UNSORTED_NAME


def _append(home: Path, data: bytes) -> None:
    """Append bytes to the current ``.zshrc``."""
    zshrc = home / ".zshrc"
    zshrc.write_bytes(zshrc.read_bytes() + data)


def _mode(path: Path) -> int:
    """Return the permission bits of ``path``."""
    return stat.S_IMODE(path.stat().st_mode)


def test_it_captures_appended_tail(home: Path, layout: ShlibLayout) -> None:
    """The tail is copied, the .zshrc stays byte-identical and the target is 0600."""
    _install(layout)
    tail = b"export TAIL=1\nprintf 'tail\\n' >> \"$HOME/order.log\"\n"
    _append(home, tail)
    zshrc_before = (home / ".zshrc").read_bytes()
    digest = hashlib.sha256(tail).hexdigest()

    plan = plan_it(layout)
    report = apply_it(plan)

    assert not plan.empty
    assert plan.tail == tail
    assert plan.tail_sha256 == digest
    assert plan.line_start == BOOTSTRAP.count("\n") + 1
    assert plan.line_end == plan.line_start + 1
    assert report.wrote
    assert report.line_count == 2
    assert (home / ".zshrc").read_bytes() == zshrc_before
    content = _target(layout).read_bytes()
    assert tail in content
    assert b"# >>> lion shlib it " in content
    assert f"sha256:{digest}".encode() in content
    assert b"# <<< lion shlib it sha256:" in content
    assert _mode(_target(layout)) == 0o600


def test_it_without_tail_is_noop(home: Path, layout: ShlibLayout) -> None:
    """A managed .zshrc without appended content creates nothing."""
    _install(layout)
    zshrc_before = (home / ".zshrc").read_bytes()

    plan = plan_it(layout)
    report = apply_it(plan)

    assert plan.empty
    assert not report.wrote
    assert report.line_count == 0
    assert not _target(layout).exists()
    assert (home / ".zshrc").read_bytes() == zshrc_before


def test_it_whitespace_tail_is_noop(home: Path, layout: ShlibLayout) -> None:
    """Whitespace-only appended content is treated as no tail."""
    _install(layout)
    _append(home, b"  \n\t\n")

    plan = plan_it(layout)
    report = apply_it(plan)

    assert plan.empty
    assert not report.wrote
    assert not _target(layout).exists()


def test_it_second_run_is_idempotent(home: Path, layout: ShlibLayout) -> None:
    """Repeating the capture with the same tail does not add a second block."""
    _install(layout)
    _append(home, b"export TAIL=1\n")
    apply_it(plan_it(layout))

    plan = plan_it(layout)
    report = apply_it(plan)

    assert plan.already_captured
    assert not report.wrote
    assert _target(layout).read_bytes().count(b"# >>> lion shlib it ") == 1


@pytest.mark.parametrize("existing", [b"# existing\n", b"# existing"])
def test_it_appends_to_existing_file_and_keeps_mode(home: Path, layout: ShlibLayout, existing: bytes) -> None:
    """Existing content and its mode survive; group-readable targets warn."""
    _install(layout)
    target = _target(layout)
    target.write_bytes(existing)
    target.chmod(0o640)
    _append(home, b"export TAIL=1\n")

    plan = plan_it(layout)
    report = apply_it(plan)

    assert plan.mode == 0o640
    assert any("readable by group" in warning for warning in report.warnings)
    content = target.read_bytes()
    assert content.startswith(existing)
    assert b"# existing\n\n# >>> lion shlib it " in content
    assert b"export TAIL=1\n" in content
    assert _mode(target) == 0o640


def test_it_rejects_symlink_target(home: Path, layout: ShlibLayout) -> None:
    """A symlinked fallback file is never overwritten."""
    _install(layout)
    outside = home / "outside.sh"
    outside.write_text("export OUTSIDE=1\n", encoding="utf-8")
    _target(layout).symlink_to(outside)
    _append(home, b"export TAIL=1\n")

    with pytest.raises(ShlibError, match="symlink"):
        plan_it(layout)

    assert outside.read_text(encoding="utf-8") == "export OUTSIDE=1\n"


def test_it_rejects_directory_target(home: Path, layout: ShlibLayout) -> None:
    """A directory at the fallback name aborts."""
    _install(layout)
    _target(layout).mkdir()
    _append(home, b"export TAIL=1\n")

    with pytest.raises(ShlibError, match="not a regular file"):
        plan_it(layout)


def test_it_rejects_changed_managed_block(home: Path, layout: ShlibLayout) -> None:
    """Content before or inside the managed block is not moved."""
    _install(layout)
    zshrc = home / ".zshrc"
    zshrc.write_bytes(b"# changed before the block\n" + BOOTSTRAP.encode() + b"export TAIL=1\n")
    zshrc_before = zshrc.read_bytes()

    with pytest.raises(ShlibError, match="managed block does not match"):
        plan_it(layout)

    assert zshrc.read_bytes() == zshrc_before
    assert not _target(layout).exists()


def test_it_rejects_missing_root(home: Path, layout: ShlibLayout) -> None:
    """The command requires an installed shlib root."""
    with pytest.raises(ShlibError, match="does not exist"):
        plan_it(layout)


def test_it_rejects_missing_shlibs(home: Path, layout: ShlibLayout) -> None:
    """A damaged tree without shlibs aborts."""
    _install(layout)
    shutil.rmtree(layout.shlibs)

    with pytest.raises(ShlibError, match="missing or not a real directory"):
        plan_it(layout)


def test_it_rejects_missing_zshrc(home: Path, layout: ShlibLayout) -> None:
    """A missing .zshrc aborts."""
    _install(layout)
    (home / ".zshrc").unlink()

    with pytest.raises(ShlibError, match="does not exist"):
        plan_it(layout)


def test_it_rejects_root_symlink(home: Path, layout: ShlibLayout) -> None:
    """A symlinked shlib root aborts."""
    outside = home / "outside"
    outside.mkdir()
    layout.root.symlink_to(outside)

    with pytest.raises(ShlibError, match="symlink"):
        plan_it(layout)


def test_it_rejects_zshrc_symlink(home: Path, layout: ShlibLayout) -> None:
    """A symlinked .zshrc is not read."""
    _install(layout)
    zshrc = home / ".zshrc"
    target = home / "real.zshrc"
    zshrc.rename(target)
    zshrc.symlink_to(target)

    with pytest.raises(ShlibError, match="symlink"):
        plan_it(layout)


def test_it_falls_back_to_bootstrap_without_lock(home: Path, layout: ShlibLayout) -> None:
    """A missing lock uses the built-in bootstrap and warns."""
    _install(layout)
    layout.lock_file.unlink()
    _append(home, b"export TAIL=1\n")

    plan = plan_it(layout)
    report = apply_it(plan)

    assert any("using the built-in bootstrap" in warning for warning in plan.warnings)
    assert report.wrote
    assert b"export TAIL=1\n" in _target(layout).read_bytes()


def test_it_without_lock_and_without_bootstrap_prefix_aborts(home: Path, layout: ShlibLayout) -> None:
    """Without a lock, an unmanaged .zshrc cannot be split."""
    _install(layout)
    layout.lock_file.unlink()
    (home / ".zshrc").write_bytes(b"export UNMANAGED=1\n")

    with pytest.raises(ShlibError, match="does not begin with the built-in bootstrap"):
        plan_it(layout)


def test_it_rejects_lock_symlink(home: Path, layout: ShlibLayout) -> None:
    """A symlinked lock file is not used as a reference."""
    _install(layout)
    target = home / "real-lock"
    layout.lock_file.rename(target)
    layout.lock_file.symlink_to(target)

    with pytest.raises(ShlibError, match="symlink"):
        plan_it(layout)


def test_it_rejects_lock_directory(home: Path, layout: ShlibLayout) -> None:
    """A directory at the lock path is not a reference."""
    _install(layout)
    layout.lock_file.unlink()
    layout.lock_file.mkdir()

    with pytest.raises(ShlibError, match="not a regular file"):
        plan_it(layout)


def test_it_preserves_non_utf8_bytes(home: Path, layout: ShlibLayout) -> None:
    """The tail is copied as bytes, not decoded."""
    _install(layout)
    tail = b"export RAW=\xff\n"
    _append(home, tail)

    apply_it(plan_it(layout))

    assert tail in _target(layout).read_bytes()


def test_it_rejects_nul_byte(home: Path, layout: ShlibLayout) -> None:
    """A NUL byte cannot be written safely."""
    _install(layout)
    _append(home, b"export BROKEN=a\0b\n")

    with pytest.raises(ShlibError, match="NUL"):
        plan_it(layout)


def test_it_rejects_zshrc_directory(home: Path, layout: ShlibLayout) -> None:
    """A directory at the .zshrc path aborts."""
    _install(layout)
    (home / ".zshrc").unlink()
    (home / ".zshrc").mkdir()

    with pytest.raises(ShlibError, match="not a regular file"):
        plan_it(layout)


def test_it_reports_unreadable_zshrc(home: Path, layout: ShlibLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    """A read error for the .zshrc becomes a ShlibError."""
    _install(layout)
    original = Path.read_bytes

    def guarded_read(self: Path) -> bytes:
        if self == home / ".zshrc":
            raise OSError("denied")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", guarded_read)

    with pytest.raises(ShlibError, match="cannot read"):
        plan_it(layout)


def test_it_tail_without_final_newline(home: Path, layout: ShlibLayout) -> None:
    """A last line without newline is captured and closed with one."""
    _install(layout)
    _append(home, b"export TAIL=1")

    report = apply_it(plan_it(layout))

    assert report.line_count == 1
    content = _target(layout).read_bytes()
    assert b"export TAIL=1\n# <<< lion shlib it sha256:" in content


def test_it_reports_unreadable_target(home: Path, layout: ShlibLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    """A read error for an existing target becomes a ShlibError."""
    _install(layout)
    target = _target(layout)
    target.write_bytes(b"# existing\n")
    _append(home, b"export TAIL=1\n")
    original = Path.read_bytes

    def guarded_read(self: Path) -> bytes:
        if self == target:
            raise OSError("denied")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", guarded_read)

    with pytest.raises(ShlibError, match="cannot read"):
        plan_it(layout)


def test_it_write_failure_keeps_target(home: Path, layout: ShlibLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed write leaves the target and .zshrc untouched."""
    import lion.shlib.it as it_module

    _install(layout)
    target = _target(layout)
    target.write_bytes(b"# keep\n")
    _append(home, b"export TAIL=1\n")
    zshrc_before = (home / ".zshrc").read_bytes()
    plan = plan_it(layout)

    def fail_write(path: Path, data: bytes, mode: int) -> None:
        raise OSError("write failed")

    monkeypatch.setattr(it_module, "atomic_write", fail_write)

    with pytest.raises(ShlibError, match="cannot write"):
        apply_it(plan)

    assert target.read_bytes() == b"# keep\n"
    assert (home / ".zshrc").read_bytes() == zshrc_before


def test_it_cli_dry_run_writes_nothing(home: Path, layout: ShlibLayout) -> None:
    """The dry run explains the capture and changes nothing."""
    _install(layout)
    _append(home, b"export TAIL=1\n")
    zshrc_before = (home / ".zshrc").read_bytes()

    result = runner.invoke(app, ["shlib", "it", "--home", str(home), "--dry-run"])

    assert result.exit_code == 0
    assert "Would capture 1 line(s)" in result.output
    assert "Tail: lines" in result.output
    assert "will run twice" in result.output
    assert "Dry run: no changes made." in result.output
    assert not _target(layout).exists()
    assert (home / ".zshrc").read_bytes() == zshrc_before


def test_it_cli_success_does_not_reveal_content(home: Path, layout: ShlibLayout) -> None:
    """The output names paths and counts but never the captured values."""
    _install(layout)
    _append(home, b"export SECRET=s3cr3t-value\n")

    result = runner.invoke(app, ["shlib", "it", "--home", str(home)])

    assert result.exit_code == 0
    assert "Captured 1 line(s)" in result.output
    assert "sha256:" in result.output
    assert "warning: the tail is still in ~/.zshrc" in result.output
    assert "s3cr3t-value" not in result.output
    assert b"export SECRET=s3cr3t-value\n" in _target(layout).read_bytes()


def test_it_cli_without_tail(home: Path, layout: ShlibLayout) -> None:
    """No tail is reported and exits successfully."""
    _install(layout)

    result = runner.invoke(app, ["shlib", "it", "--home", str(home)])

    assert result.exit_code == 0
    assert result.output.strip() == "No appended content after the managed block in ~/.zshrc."


def test_it_cli_already_captured(home: Path, layout: ShlibLayout) -> None:
    """A second CLI run reports the existing capture."""
    _install(layout)
    _append(home, b"export TAIL=1\n")
    assert runner.invoke(app, ["shlib", "it", "--home", str(home)]).exit_code == 0

    result = runner.invoke(app, ["shlib", "it", "--home", str(home)])

    assert result.exit_code == 0
    assert "Tail already captured in ~/.shlib/shlibs/99-unsorted.sh" in result.output
    assert "will run twice" in result.output


def test_it_cli_reports_errors(home: Path, layout: ShlibLayout) -> None:
    """A ShlibError exits with status 1 and no traceback."""
    _install(layout)
    (home / ".zshrc").unlink()

    result = runner.invoke(app, ["shlib", "it", "--home", str(home)])

    assert result.exit_code == 1
    assert "error:" in result.output


def test_it_cli_help_lists_options() -> None:
    """The help text documents --home and --dry-run."""
    result = runner.invoke(app, ["shlib", "it", "--help"])

    assert result.exit_code == 0
    assert "--home" in result.output
    assert "--dry-run" in result.output
