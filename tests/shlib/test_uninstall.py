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


def _write_export(layout: ShlibLayout, name: str, data: str | bytes, *, mode: int = 0o644) -> Path:
    """Write an export file with an explicit mode independent of the umask."""
    path = layout.exports / name
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")
    path.chmod(mode)
    return path


def test_flatten_orders_exports_and_shlibs(home: Path, layout: ShlibLayout) -> None:
    """Exports come first and shlib files follow in sorted order."""
    _install(layout)
    _write_export(layout, "BETA", "two\n\n")
    _write_export(layout, "ALPHA", "one\n")
    _write_export(layout, ".hidden", "secret\n")
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
    _write_export(layout, "SPACED", "a b\n")
    _write_export(layout, "QUOTED", "it's\n")
    _write_export(layout, "DOLLAR", "$HOME\n")
    _write_export(layout, "MULTI", "line1\nline2\n\n\n")
    _write_export(layout, "EMPTY", "")

    content = flatten(layout, layout.root.parent / ".shlib.uninstalled-test").content.decode()

    assert "export SPACED='a b'\n" in content
    assert "export QUOTED='it'\"'\"'s'\n" in content
    assert "export DOLLAR='$HOME'\n" in content
    assert "export MULTI='line1\nline2'\n" in content
    assert "export EMPTY=''\n" in content


def test_flatten_preserves_non_utf8_shlib_bytes(home: Path, layout: ShlibLayout) -> None:
    """Shlib bytes survive the round trip even when they are not valid UTF-8."""
    _install(layout)
    (layout.shlibs / "01-bytes.sh").write_bytes(b"export BYTES=\xff\n")
    _write_export(layout, "RAW", b"\xff\n")

    content = flatten(layout, layout.root.parent / ".shlib.uninstalled-test").content

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


def test_flatten_splits_owner_only_exports(home: Path, layout: ShlibLayout) -> None:
    """Owner-only files go to secrets.sh; group-readable files stay public."""
    _install(layout)
    _write_export(layout, "PUBLIC", "public-value\n", mode=0o644)
    _write_export(layout, "GROUP", "group-value\n", mode=0o640)
    _write_export(layout, "SECRET", "secret-value\n", mode=0o600)
    _write_export(layout, "READONLY", "readonly-value\n", mode=0o400)

    plan = plan_uninstall(layout)
    content = plan.content.decode()
    secrets = plan.secrets_content

    assert plan.export_names == ("GROUP", "PUBLIC")
    assert plan.secret_names == ("READONLY", "SECRET")
    assert secrets is not None
    secrets_text = secrets.decode()
    assert "export GROUP=group-value" in content
    assert "export PUBLIC=public-value" in content
    assert "group-value" not in secrets_text
    assert "public-value" not in secrets_text
    assert "export READONLY=readonly-value" in secrets_text
    assert "export SECRET=secret-value" in secrets_text
    assert "readonly-value" not in content
    assert "secret-value" not in content
    assert plan.secrets_path == plan.backup / "secrets.sh"


def test_flatten_without_secrets_keeps_structure(home: Path, layout: ShlibLayout) -> None:
    """No owner-only exports means no secrets file and no extra block."""
    _install(layout)
    _write_export(layout, "VALUE", "x\n", mode=0o644)

    plan = plan_uninstall(layout)

    assert plan.secret_names == ()
    assert plan.secrets_content is None
    assert "# >>> shlib uninstall: secrets" not in plan.content.decode()
    assert plan.content.decode().endswith("# <<< shlibs/00-original-zshrc.sh\n")


def test_flatten_quotes_secret_values(home: Path, layout: ShlibLayout) -> None:
    """Secrets use exactly the same quoting and escaping as public exports."""
    _install(layout)
    _write_export(layout, "SPACED", "a b\n", mode=0o600)
    _write_export(layout, "QUOTED", "it's\n", mode=0o600)
    _write_export(layout, "DOLLAR", "$HOME\n", mode=0o600)
    _write_export(layout, "MULTI", "line1\nline2\n\n\n", mode=0o600)
    _write_export(layout, "EMPTY", "", mode=0o600)
    _write_export(layout, "RAW", b"\xff\n", mode=0o600)

    plan = plan_uninstall(layout)
    secrets = plan.secrets_content

    assert secrets is not None
    assert b"export SPACED='a b'\n" in secrets
    assert b"export QUOTED='it'\"'\"'s'\n" in secrets
    assert b"export DOLLAR='$HOME'\n" in secrets
    assert b"export MULTI='line1\nline2'\n" in secrets
    assert b"export EMPTY=''\n" in secrets
    assert b"export RAW='\xff'\n" in secrets
    assert b"export SPACED" not in plan.content


def test_flatten_rejects_nul_in_secret(home: Path, layout: ShlibLayout) -> None:
    """A NUL byte is rejected before any output is written."""
    _install(layout)
    _write_export(layout, "BROKEN", b"a\0b", mode=0o600)

    with pytest.raises(ShlibError, match="NUL"):
        plan_uninstall(layout)


def test_flatten_treats_symlink_to_owner_only_file_as_secret(home: Path, layout: ShlibLayout) -> None:
    """The mode of the symlink target decides, mirroring ``[ -f ]``."""
    _install(layout)
    target = home / "outside-secret"
    target.write_text("linked\n", encoding="utf-8")
    target.chmod(0o600)
    (layout.exports / "LINKED").symlink_to(target)

    plan = plan_uninstall(layout)

    assert plan.export_names == ()
    assert plan.secret_names == ("LINKED",)
    assert plan.secrets_content is not None
    assert b"export LINKED=linked\n" in plan.secrets_content


def test_secret_block_precedes_shlibs(home: Path, layout: ShlibLayout) -> None:
    """Shlibs are sourced after secrets so they can use the variables."""
    _install(layout)
    _write_export(layout, "SECRET", "x\n", mode=0o600)

    content = plan_uninstall(layout).content.decode()

    assert content.index("# >>> shlib uninstall: secrets") < content.index("# >>> shlibs/00-original-zshrc.sh")
    assert content.index("# <<< shlib uninstall: secrets") < content.index("# >>> shlibs/00-original-zshrc.sh")


def test_uninstall_moves_tree_to_backup(home: Path, layout: ShlibLayout) -> None:
    """Uninstall writes the flat file and renames the old tree."""
    _install(layout)
    _write_export(layout, "VALUE", "x\n")

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


def test_uninstall_writes_secrets_into_backup(home: Path, layout: ShlibLayout) -> None:
    """Owner-only values land in a 0600 secrets.sh and not in the flat file."""
    _install(layout)
    _write_export(layout, "PUBLIC", "visible\n", mode=0o644)
    _write_export(layout, "SECRET", "hidden\n", mode=0o600)

    report = apply_uninstall(plan_uninstall(layout))

    secrets_file = report.backup / "secrets.sh"
    assert secrets_file.is_file()
    assert _mode(secrets_file) == 0o600
    assert b"export SECRET=hidden\n" in secrets_file.read_bytes()
    zshrc = layout.zshrc.read_text(encoding="utf-8")
    assert "hidden" not in zshrc
    assert "export PUBLIC=visible" in zshrc
    assert f'source "$HOME/{report.backup.name}/secrets.sh"' in zshrc
    assert report.secrets_path == secrets_file


def test_uninstall_uses_backup_suffix_in_secret_source(
    home: Path, layout: ShlibLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The source line points at the same suffixed backup that receives the file."""
    import lion.shlib.uninstall as uninstall_module

    monkeypatch.setattr(uninstall_module, "_utc_timestamp", lambda: "20260101T000000Z")
    taken = home / ".shlib.uninstalled-20260101T000000Z"
    taken.mkdir()
    _install(layout)
    _write_export(layout, "SECRET", "hidden\n", mode=0o600)

    report = apply_uninstall(plan_uninstall(layout))

    assert report.backup.name == ".shlib.uninstalled-20260101T000000Z-1"
    assert (report.backup / "secrets.sh").is_file()
    assert f'source "$HOME/{report.backup.name}/secrets.sh"' in layout.zshrc.read_text(encoding="utf-8")


def test_uninstall_secret_write_failure_keeps_state(
    home: Path, layout: ShlibLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed secrets write leaves .zshrc and the old tree untouched."""
    import lion.shlib.uninstall as uninstall_module

    _install(layout)
    secret = _write_export(layout, "SECRET", "hidden\n", mode=0o600)
    zshrc_bytes = (home / ".zshrc").read_bytes()
    plan = plan_uninstall(layout)

    def fail_write(path: Path, data: bytes, mode: int) -> None:
        assert path.name == "secrets.sh"
        raise OSError("write failed")

    monkeypatch.setattr(uninstall_module, "atomic_write", fail_write)

    with pytest.raises(ShlibError, match=r"cannot write .*secrets\.sh"):
        apply_uninstall(plan)

    assert layout.root.is_dir()
    assert secret.read_text(encoding="utf-8") == "hidden\n"
    assert (home / ".zshrc").read_bytes() == zshrc_bytes


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
    _write_export(layout, "bad-name", "x")
    zshrc_bytes = (home / ".zshrc").read_bytes()

    with pytest.raises(ShlibError, match="invalid export name"):
        plan_uninstall(layout)

    assert (home / ".zshrc").read_bytes() == zshrc_bytes


def test_uninstall_rejects_nul_export(home: Path, layout: ShlibLayout) -> None:
    """A NUL byte cannot be represented as a shell variable."""
    _install(layout)
    _write_export(layout, "BROKEN", b"a\0b")

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


def test_uninstall_rename_failure_names_secret_paths(
    home: Path, layout: ShlibLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The error names the leftover secrets file and the missing backup path."""
    import lion.shlib.uninstall as uninstall_module

    monkeypatch.setattr(uninstall_module, "_utc_timestamp", lambda: "20260101T000000Z")
    _install(layout)
    _write_export(layout, "SECRET", "hidden\n", mode=0o600)
    plan = plan_uninstall(layout)

    def fail_rename(self: Path, target: Path) -> Path:
        raise OSError("rename failed")

    monkeypatch.setattr(Path, "rename", fail_rename)

    with pytest.raises(ShlibError, match=r"remove .* manually") as caught:
        apply_uninstall(plan)

    message = str(caught.value)
    assert str(layout.root / "secrets.sh") in message
    assert str(plan.secrets_path) in message
    assert ".shlib.uninstalled-20260101T000000Z/secrets.sh" in message
    assert (layout.root / "secrets.sh").is_file()


def test_uninstall_dry_run_changes_nothing(home: Path, layout: ShlibLayout) -> None:
    """The CLI dry run validates and prints but does not write."""
    _install(layout)
    zshrc_bytes = (home / ".zshrc").read_bytes()
    result = runner.invoke(app, ["shlib", "uninstall", "--home", str(home), "--dry-run"])

    assert result.exit_code == 0
    assert "Would write flattened" in result.output
    assert "Dry run: no changes made." in result.output
    assert "owner-only" not in result.output
    assert layout.root.is_dir()
    assert (home / ".zshrc").read_bytes() == zshrc_bytes


def test_uninstall_dry_run_with_secrets_changes_nothing(home: Path, layout: ShlibLayout) -> None:
    """The dry run explains the secret split and still writes nothing."""
    _install(layout)
    _write_export(layout, "PUBLIC", "visible\n", mode=0o644)
    _write_export(layout, "SECRET", "hidden\n", mode=0o600)
    zshrc_bytes = (home / ".zshrc").read_bytes()

    result = runner.invoke(app, ["shlib", "uninstall", "--home", str(home), "--dry-run"])

    assert result.exit_code == 0
    assert "1 owner-only export file(s) would not be flattened into ~/.zshrc." in result.output
    assert "They would be stored in ~/.shlib.uninstalled-" in result.output
    assert re.search(r"~/.shlib\.uninstalled-\d{8}T\d{6}Z/secrets\.sh \(mode 0600\)", result.output)
    assert "and would be sourced by ~/.zshrc." in result.output
    assert "chmod 600" in result.output
    assert "would become part of your shell configuration" in result.output
    assert "1 public export(s), 1 owner-only export(s) and 1 shlib file(s)" in result.output
    assert "hidden" not in result.output
    assert "SECRET" not in result.output
    assert layout.root.is_dir()
    assert (home / ".zshrc").read_bytes() == zshrc_bytes
    assert not list(home.glob(".shlib.uninstalled-*"))


def test_uninstall_cli_success(home: Path, layout: ShlibLayout) -> None:
    """The CLI prints the counts, backup path and flatten notice."""
    _install(layout)
    _write_export(layout, "VALUE", "x\n")

    result = runner.invoke(app, ["shlib", "uninstall", "--home", str(home)])

    assert result.exit_code == 0
    assert "Wrote flattened" in result.output
    assert "1 export(s) and 1 shlib file(s)" in result.output
    assert "Backup:" in result.output
    assert "flattened, not restored" in result.output
    assert "source ~/.zshrc" in result.output
    assert "owner-only" not in result.output
    assert not layout.root.exists()


def test_uninstall_cli_success_with_secrets(home: Path, layout: ShlibLayout) -> None:
    """The CLI reports count, path, mode and rule without leaking values."""
    _install(layout)
    _write_export(layout, "PUBLIC", "visible\n", mode=0o644)
    _write_export(layout, "SECRET", "hidden\n", mode=0o600)

    result = runner.invoke(app, ["shlib", "uninstall", "--home", str(home)])

    assert result.exit_code == 0
    assert "1 owner-only export file(s) were not flattened into ~/.zshrc." in result.output
    assert "They are stored in ~/.shlib.uninstalled-" in result.output
    assert re.search(r"~/.shlib\.uninstalled-\d{8}T\d{6}Z/secrets\.sh \(mode 0600\)", result.output)
    assert "and are sourced by ~/.zshrc." in result.output
    assert "chmod 600" in result.output
    assert "is now part of your shell configuration" in result.output
    assert "1 public export(s), 1 owner-only export(s) and 1 shlib file(s)" in result.output
    assert "hidden" not in result.output
    assert "SECRET" not in result.output
    backup = next(home.glob(".shlib.uninstalled-*"))
    secrets_file = backup / "secrets.sh"
    assert secrets_file.is_file()
    assert _mode(secrets_file) == 0o600
    assert f'source "$HOME/{backup.name}/secrets.sh"' in (home / ".zshrc").read_text(encoding="utf-8")


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
    _write_export(layout, "bad-name", "x")
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
    assert plan.secret_names == ()
    assert plan.secrets_content is None
    assert b"# >>> exports\n" in plan.content
