"""Tests for ``lion shlib uninstall``."""

from __future__ import annotations

import re
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lion.cli import app
from lion.shlib.bootstrap import BOOTSTRAP
from lion.shlib.errors import ShlibError
from lion.shlib.install import apply_install, plan_install
from lion.shlib.layout import ShlibLayout
from lion.shlib.uninstall import apply_uninstall, flatten, plan_uninstall

runner = CliRunner()

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Return text without the ANSI styling Rich adds on terminals."""
    return _ANSI_ESCAPE.sub("", text)


def _install(layout: ShlibLayout) -> None:
    """Install a fresh shlib tree."""
    apply_install(plan_install(layout))


def _mode(path: Path) -> int:
    """Return the permission bits of ``path``."""
    return stat.S_IMODE(path.stat().st_mode)


def test_flatten_orders_exports_and_shlibs(home: Path, layout: ShlibLayout) -> None:
    """Exports come first and shlib files follow in sorted order."""
    _install(layout)
    (layout.exports / "BETA").write_text("two\n\n", encoding="utf-8")
    (layout.exports / "ALPHA").write_text("one\n", encoding="utf-8")
    (layout.exports / ".hidden").write_text("secret\n", encoding="utf-8")
    (layout.shlibs / "02-second.sh").write_text("export SECOND=2\n", encoding="utf-8")
    (layout.shlibs / "01-first.sh").write_text("export FIRST=1", encoding="utf-8")
    (layout.shlibs / "1-single.sh").write_text("export SINGLE=1\n", encoding="utf-8")
    (layout.shlibs / "not-loaded.sh").write_text("export NOT_LOADED=1\n", encoding="utf-8")
    (layout.shlibs / ".hidden.sh").write_text("export HIDDEN=1\n", encoding="utf-8")

    plan = plan_uninstall(layout)
    content = plan.content.decode()

    assert plan.export_names == ("ALPHA", "BETA")
    assert [path.name for path in plan.shlib_files] == [
        "00-original-zshrc.sh",
        "01-first.sh",
        "02-second.sh",
    ]
    assert content.index("export ALPHA=one") < content.index("export BETA=two")
    assert content.index("export BETA=two") < content.index("# >>> shlibs/00-original-zshrc.sh")
    assert (
        content.index("# >>> shlibs/00-original-zshrc.sh")
        < content.index("# >>> shlibs/01-first.sh")
        < content.index("# >>> shlibs/02-second.sh")
    )
    assert "export FIRST=1\n# <<< shlibs/01-first.sh" in content
    assert "not-loaded.sh" not in content
    assert "1-single.sh" not in content
    assert ".hidden" not in content
    assert "secret" not in content


def test_flatten_quotes_export_values(home: Path, layout: ShlibLayout) -> None:
    """Values with spaces, quotes, dollars and newlines stay valid shell."""
    _install(layout)
    (layout.exports / "SPACED").write_text("a b\n", encoding="utf-8")
    (layout.exports / "QUOTED").write_text("it's\n", encoding="utf-8")
    (layout.exports / "DOLLAR").write_text("$HOME\n", encoding="utf-8")
    (layout.exports / "MULTI").write_text("line1\nline2\n\n\n", encoding="utf-8")
    (layout.exports / "EMPTY").write_text("", encoding="utf-8")

    content = flatten(layout).content.decode()

    assert "export SPACED='a b'\n" in content
    assert "export QUOTED='it'\"'\"'s'\n" in content
    assert "export DOLLAR='$HOME'\n" in content
    assert "export MULTI='line1\nline2'\n" in content
    assert "export EMPTY=''\n" in content


def test_flatten_preserves_non_utf8_shlib_bytes(home: Path, layout: ShlibLayout) -> None:
    """Shlib bytes survive the round trip even when they are not valid UTF-8."""
    _install(layout)
    (layout.shlibs / "01-bytes.sh").write_bytes(b"export BYTES=\xff\n")
    (layout.exports / "RAW").write_bytes(b"\xff\n")

    content = flatten(layout).content

    assert b"export BYTES=\xff\n" in content
    assert b"export RAW='\xff'\n" in content


def test_flatten_accepts_symlinked_shlib_file(home: Path, layout: ShlibLayout) -> None:
    """A symlink to a regular file behaves like ``[ -f ]`` in the bootstrap."""
    _install(layout)
    target = home / "outside.sh"
    target.write_text("export LINKED=1\n", encoding="utf-8")
    (layout.shlibs / "03-linked.sh").symlink_to(target)

    plan = plan_uninstall(layout)

    assert layout.shlibs / "03-linked.sh" in plan.shlib_files
    assert b"export LINKED=1\n" in plan.content


def test_uninstall_moves_tree_to_backup(home: Path, layout: ShlibLayout) -> None:
    """Uninstall writes the flat file and renames the old tree."""
    _install(layout)
    (layout.exports / "VALUE").write_text("x\n", encoding="utf-8")

    plan = plan_uninstall(layout)
    report = apply_uninstall(plan)

    assert not layout.root.exists()
    assert report.backup.is_dir()
    assert report.backup.name.startswith(".shlib.uninstalled-")
    assert re.fullmatch(r"\.shlib\.uninstalled-\d{8}T\d{6}Z", report.backup.name)
    assert (report.backup / "shlibs" / "00-original-zshrc.sh").exists()
    content = layout.zshrc.read_text(encoding="utf-8")
    assert "SHLIB for zsh" not in content
    assert "export ORIGINAL=1" in content
    assert "export VALUE=x" in content


def test_uninstall_backup_name_gets_suffix(home: Path, layout: ShlibLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    """An existing backup name is extended with a counter."""
    import lion.shlib.uninstall as uninstall_module

    monkeypatch.setattr(uninstall_module, "_utc_timestamp", lambda: "20260101T000000Z")
    taken = home / ".shlib.uninstalled-20260101T000000Z"
    taken.mkdir()
    _install(layout)

    report = apply_uninstall(plan_uninstall(layout))

    assert report.backup == home / ".shlib.uninstalled-20260101T000000Z-1"
    assert report.backup.is_dir()
    assert taken.is_dir()


def test_uninstall_rejects_drift_without_force(home: Path, layout: ShlibLayout) -> None:
    """A modified .zshrc requires --force and is then flattened."""
    _install(layout)
    zshrc = home / ".zshrc"
    zshrc.write_text(zshrc.read_text(encoding="utf-8") + "# manual change\n", encoding="utf-8")

    with pytest.raises(ShlibError, match="differs"):
        plan_uninstall(layout)

    plan = plan_uninstall(layout, force=True)
    assert plan.warnings
    apply_uninstall(plan)
    assert "manual change" not in zshrc.read_text(encoding="utf-8")


def test_uninstall_rejects_missing_lock_without_force(home: Path, layout: ShlibLayout) -> None:
    """A missing lock file requires --force when .zshrc exists."""
    _install(layout)
    layout.lock_file.unlink()

    with pytest.raises(ShlibError, match="lock"):
        plan_uninstall(layout)

    plan = plan_uninstall(layout, force=True)
    assert plan.warnings
    apply_uninstall(plan)
    assert not layout.root.exists()


def test_uninstall_without_zshrc_creates_flat_file(home: Path, layout: ShlibLayout) -> None:
    """A missing .zshrc does not block the uninstallation."""
    _install(layout)
    (home / ".zshrc").unlink()

    apply_uninstall(plan_uninstall(layout))

    assert (home / ".zshrc").is_file()
    assert _mode(home / ".zshrc") == 0o644
    assert "export ORIGINAL=1" in (home / ".zshrc").read_text(encoding="utf-8")


def test_uninstall_rejects_invalid_export_name(home: Path, layout: ShlibLayout) -> None:
    """An export name outside the shell identifier rules aborts before writing."""
    _install(layout)
    (layout.exports / "bad-name").write_text("x", encoding="utf-8")
    zshrc_bytes = (home / ".zshrc").read_bytes()

    with pytest.raises(ShlibError, match="invalid export name"):
        plan_uninstall(layout)

    assert (home / ".zshrc").read_bytes() == zshrc_bytes


def test_uninstall_rejects_nul_export(home: Path, layout: ShlibLayout) -> None:
    """A NUL byte cannot be represented as a shell variable."""
    _install(layout)
    (layout.exports / "BROKEN").write_bytes(b"a\0b")

    with pytest.raises(ShlibError, match="NUL"):
        plan_uninstall(layout)


def test_uninstall_keeps_tree_when_write_fails(
    home: Path, layout: ShlibLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed flatten write leaves the old tree and .zshrc untouched."""
    import lion.shlib.uninstall as uninstall_module

    _install(layout)
    zshrc_bytes = (home / ".zshrc").read_bytes()
    plan = plan_uninstall(layout)

    def fail_write(path: Path, data: bytes, mode: int) -> None:
        raise OSError("write failed")

    monkeypatch.setattr(uninstall_module, "atomic_write", fail_write)

    with pytest.raises(ShlibError, match="cannot write"):
        apply_uninstall(plan)

    assert layout.root.is_dir()
    assert (home / ".zshrc").read_bytes() == zshrc_bytes


def test_uninstall_reports_failed_rename(home: Path, layout: ShlibLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed rename keeps the new file and names the manual cleanup."""
    _install(layout)
    plan = plan_uninstall(layout)

    def fail_rename(self: Path, target: Path) -> Path:
        raise OSError("rename failed")

    monkeypatch.setattr(Path, "rename", fail_rename)

    with pytest.raises(ShlibError, match=r"remove .* manually"):
        apply_uninstall(plan)

    assert layout.root.is_dir()
    assert "SHLIB for zsh" not in (home / ".zshrc").read_text(encoding="utf-8")


def test_uninstall_dry_run_changes_nothing(home: Path, layout: ShlibLayout) -> None:
    """The CLI dry run validates and prints but does not write."""
    _install(layout)
    zshrc_bytes = (home / ".zshrc").read_bytes()
    result = runner.invoke(app, ["shlib", "uninstall", "--home", str(home), "--dry-run"])

    assert result.exit_code == 0
    assert "Would write flattened" in result.output
    assert "Dry run: no changes made." in result.output
    assert layout.root.is_dir()
    assert (home / ".zshrc").read_bytes() == zshrc_bytes


def test_uninstall_cli_success(home: Path, layout: ShlibLayout) -> None:
    """The CLI prints the counts, backup path and flatten notice."""
    _install(layout)
    (layout.exports / "VALUE").write_text("x\n", encoding="utf-8")

    result = runner.invoke(app, ["shlib", "uninstall", "--home", str(home)])

    assert result.exit_code == 0
    assert "Wrote flattened" in result.output
    assert "1 export(s) and 1 shlib file(s)" in result.output
    assert "Backup:" in result.output
    assert "flattened, not restored" in result.output
    assert "source ~/.zshrc" in result.output
    assert not layout.root.exists()


def test_uninstall_roundtrip(home: Path, layout: ShlibLayout) -> None:
    """Install, uninstall and install again work in sequence."""
    _install(layout)
    (layout.shlibs / "01-extra.sh").write_text("export EXTRA=1\n", encoding="utf-8")
    apply_uninstall(plan_uninstall(layout))

    flattened = (home / ".zshrc").read_text(encoding="utf-8")
    report = apply_install(plan_install(layout))

    assert "export EXTRA=1" in flattened
    assert report.original.read_text(encoding="utf-8") == flattened
    assert (home / ".zshrc").read_bytes() == BOOTSTRAP.encode()


def test_uninstall_cli_reports_errors(home: Path, layout: ShlibLayout) -> None:
    """A damaged tree exits with status 1."""
    _install(layout)
    (layout.exports / "bad-name").write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["shlib", "uninstall", "--home", str(home)])

    assert result.exit_code == 1
    assert "error:" in result.output
    assert layout.root.is_dir()


def test_uninstall_cli_help_lists_force() -> None:
    """The help text documents --force."""
    result = runner.invoke(app, ["shlib", "uninstall", "--help"])

    assert result.exit_code == 0
    help_text = strip_ansi(result.output)
    assert "--force" in help_text
    assert "--dry-run" in help_text


def test_uninstall_rejects_missing_root(home: Path, layout: ShlibLayout) -> None:
    """A missing shlib root means there is nothing to uninstall."""
    with pytest.raises(ShlibError, match="nothing to uninstall"):
        plan_uninstall(layout)


def test_uninstall_rejects_root_symlink(home: Path, layout: ShlibLayout) -> None:
    """A symlinked shlib root is never moved."""
    target = home / "elsewhere"
    (target / "shlibs").mkdir(parents=True)
    layout.root.symlink_to(target)

    with pytest.raises(ShlibError, match="symlink"):
        plan_uninstall(layout)


def test_uninstall_rejects_missing_shlibs(home: Path, layout: ShlibLayout) -> None:
    """A tree without shlibs/ must not silently flatten to an empty file."""
    _install(layout)
    (layout.shlibs / "00-original-zshrc.sh").unlink()
    layout.shlibs.rmdir()

    with pytest.raises(ShlibError, match="missing"):
        plan_uninstall(layout)


def test_uninstall_rejects_zshrc_directory(home: Path, layout: ShlibLayout) -> None:
    """A directory named .zshrc is not a readable configuration file."""
    _install(layout)
    (home / ".zshrc").unlink()
    (home / ".zshrc").mkdir()

    with pytest.raises(ShlibError, match="not a regular file"):
        plan_uninstall(layout)


def test_uninstall_rejects_zshrc_symlink(home: Path, layout: ShlibLayout) -> None:
    """A symlinked .zshrc is not overwritten."""
    _install(layout)
    target = home / "target.zshrc"
    target.write_bytes((home / ".zshrc").read_bytes())
    (home / ".zshrc").unlink()
    (home / ".zshrc").symlink_to(target)

    with pytest.raises(ShlibError, match="symlink"):
        plan_uninstall(layout)


def test_uninstall_rejects_exports_file(home: Path, layout: ShlibLayout) -> None:
    """An exports path that is not a directory aborts the flattening."""
    _install(layout)
    layout.exports.rmdir()
    layout.exports.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ShlibError, match="not a directory"):
        plan_uninstall(layout)


def test_uninstall_accepts_missing_exports_directory(home: Path, layout: ShlibLayout) -> None:
    """A missing exports directory simply contributes no variables."""
    _install(layout)
    layout.exports.rmdir()

    plan = plan_uninstall(layout)

    assert plan.export_names == ()
    assert b"# >>> exports\n" in plan.content
