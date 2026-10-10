"""Shlib lifecycle and safe failure tests, isolated from the user's shell."""

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import typer
from typer.testing import CliRunner

from lion.cli import app
from lion.command.shlib import Action
from lion.command.shlib import run as run_shlib
from lion.program import shlib
from lion.program.checks import CheckStatus, DoctorContext

runner = CliRunner()

ANSI_PATTERN = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _plain(text: str) -> str:
    """Normalize help text: strip ANSI escapes and collapse hard wrapping.

    Rich wraps help to the detected terminal width, so a single substring can be
    split across lines depending on the viewer. Collapsing every whitespace run
    to one space makes the content assertions width- and color-independent.
    """
    return " ".join(ANSI_PATTERN.sub("", text).split())


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use a disposable home and a mocked external syntax checker."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ZDOTDIR", raising=False)
    monkeypatch.setattr(shlib, "_validate", Mock())
    return tmp_path


def invoke(action: str = "status", *options: str) -> str:
    """Invoke a successful shlib operation."""
    result = runner.invoke(app, ["shlib", action, *options])
    assert result.exit_code == 0, result.output
    return result.stdout


def test_roundtrip(home: Path) -> None:
    """Flatten current scripts, symlinks and exports, preserving outside changes."""
    rc = home / ".zshrc"
    rc.write_text("alias hello='echo hello'\n")
    rc.chmod(0o644)
    invoke("install")
    installed = rc.read_text()
    assert (home / ".zshrc.before-shlib").read_text() == "alias hello='echo hello'\n"
    assert invoke("install").startswith("Shlib is already installed")
    assert rc.read_text() == installed
    root = home / ".shlib"
    (root / "exports" / "TOKEN").write_text("literal '$HOME'\nsecond line\n\n")
    (root / "exports" / "TOKEN").chmod(0o600)
    (root / "shlibs" / "20-last.sh").write_text("LAST=yes\n")
    source = home / "linked.sh"
    source.write_text("FIRST=yes\n")
    (root / "shlibs" / "10-first.sh").symlink_to(source)
    rc.write_text("# before\n" + installed + "# installer\nINSTALLER=yes\n")
    status = invoke("status", "--json")
    assert "literal" not in status
    assert json.loads(status)["lock"] == "changed"
    output = invoke("uninstall")
    assert "file-relative" in output
    flattened = rc.read_text()
    assert flattened.startswith('# before\nsource "$HOME/.zshrc.exports"\n')
    assert flattened.index("FIRST=yes") < flattened.index("LAST=yes")
    assert "# Shlib: '10-first.sh'" in flattened
    assert flattened.endswith("# installer\nINSTALLER=yes\n")
    assert (home / ".zshrc.before-shlib-uninstall").read_text().startswith("# before\n")
    assert stat.S_IMODE(rc.stat().st_mode) == 0o644
    exports = home / ".zshrc.exports"
    assert stat.S_IMODE(exports.stat().st_mode) == 0o600
    assert "export TOKEN=" in exports.read_text()
    assert root.exists() and source.exists()
    assert not json.loads(invoke("status", "--json"))["installed"]
    assert "not installed" in invoke("uninstall")


def test_reinstall_after_uninstall_is_refused(home: Path) -> None:
    """A reinstall after uninstall fails loudly and changes nothing (ADR-0008)."""
    rc = home / ".zshrc"
    rc.write_text("alias hello='echo hello'\n")
    invoke("install")
    token = home / ".shlib" / "exports" / "TOKEN"
    token.write_text("secret\n")
    token.chmod(0o600)
    (home / ".shlib" / "shlibs" / "10-first.sh").write_text("FIRST=yes\n")
    invoke("uninstall")
    before = rc.read_bytes()
    result = runner.invoke(app, ["shlib", "install"])
    assert result.exit_code == 1
    assert rc.read_bytes() == before
    assert not (home / ".zshrc.before-shlib.1").exists()
    assert str(home / ".zshrc.lock") in _plain(result.stderr)


def test_empty_home_and_status_readonly(home: Path) -> None:
    """Status does not create files; installation works without a previous rc."""
    assert not json.loads(invoke("status", "--json"))["installed"]
    assert list(home.iterdir()) == []
    invoke("install")
    assert stat.S_IMODE((home / ".zshrc").stat().st_mode) == 0o644
    assert "unchanged" in invoke()


@pytest.mark.parametrize(
    "name",
    [".zshrc", ".zshrc.lock", ".shlib", ".shlib/shlibs/00-original-zshrc.sh", ".zshrc.exports"],
)
def test_install_conflicts(home: Path, name: str) -> None:
    """Refuse symlink targets and preexisting installation remnants."""
    path = home / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(home / "missing")
    result = runner.invoke(app, ["shlib", "install"])
    assert result.exit_code == 1
    assert path.is_symlink()


@pytest.mark.parametrize("conflict", ["scripts", "ignore", "export", "nul", "zshrc_exports"])
def test_install_preflight(home: Path, conflict: str) -> None:
    """Invalid inputs are caught before replacing the rc or writing backups."""
    rc = home / ".zshrc"
    rc.write_text("# original\n")
    root = home / ".shlib"
    (root / "exports").mkdir(parents=True)
    (root / "shlibs").mkdir()
    path = {
        "scripts": root / "shlibs" / "10-existing",
        "ignore": root / "exports" / ".gitignore",
        "export": root / "exports" / "bad-name",
        "nul": root / "exports" / "TOKEN",
        "zshrc_exports": home / ".zshrc.exports",
    }[conflict]
    path.write_text("\0" if conflict == "nul" else "existing")
    result = runner.invoke(app, ["shlib", "install"])
    assert result.exit_code == 1
    assert rc.read_text() == "# original\n"
    assert not (home / ".zshrc.before-shlib").exists()


@pytest.mark.parametrize("conflict", ["exports", "symlink", "script", "missing", "block", "markers"])
def test_uninstall_conflicts(home: Path, conflict: str) -> None:
    """Refuse unsafe or incomplete flattening without altering active files."""
    invoke("install")
    rc = home / ".zshrc"
    root = home / ".shlib"
    if conflict == "exports":
        (home / ".zshrc.exports").write_text("keep")
    elif conflict == "symlink":
        (home / ".zshrc.exports").symlink_to(home / "missing")
    elif conflict == "script":
        (root / "shlibs" / "10-broken").symlink_to(home / "missing")
    elif conflict == "missing":
        (root / "exports" / ".gitignore").unlink()
        (root / "exports").rmdir()
    elif conflict == "block":
        rc.write_text(rc.read_text().replace(shlib.END, "echo custom\n" + shlib.END))
    else:
        rc.write_text(rc.read_text() + shlib.START + "\n")
    before = rc.read_bytes()
    assert runner.invoke(app, ["shlib", "uninstall"]).exit_code == 1
    assert rc.read_bytes() == before


def test_status_warnings(home: Path) -> None:
    """Report permission problems and missing folders without reading secrets."""
    invoke("install")
    root = home / ".shlib"
    token = root / "exports" / "bad-name"
    token.write_text("SECRET")
    token.chmod(0o644)
    (root / "shlibs" / "00-original-zshrc.sh").unlink()
    (root / "shlibs").rmdir()
    (home / ".zshrc.lock").unlink()
    output = invoke("status", "--json")
    assert "SECRET" not in output
    assert len(json.loads(output)["warnings"]) == 3
    assert '"missing"' in output


def test_backup_collision(home: Path) -> None:
    """Keep earlier backups unchanged."""
    (home / ".zshrc").write_text("# current")
    (home / ".zshrc.before-shlib").write_text("# historical")
    invoke("install")
    assert (home / ".zshrc.before-shlib").read_text() == "# historical"
    assert (home / ".zshrc.before-shlib.1").read_text() == "# current"


def test_custom_zdotdir_and_json(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject unsupported destinations and mutation JSON options."""
    rejected = runner.invoke(app, ["shlib", "install", "--json"])
    assert rejected.exit_code == 2
    assert "No such option" in rejected.output
    monkeypatch.setenv("ZDOTDIR", str(home / "other"))
    assert runner.invoke(app, ["shlib"]).exit_code == 1
    assert list(home.iterdir()) == []


def test_help_documents_shlib_system() -> None:
    """``lion shlib --help`` explains the library and shows dash link examples."""
    result = runner.invoke(app, ["shlib", "--help"])
    assert result.exit_code == 0
    help_text = _plain(result.stdout)
    assert "ln -s" in help_text
    assert "~/.shlib/dash" in help_text
    assert "lion shlib status" in help_text


def test_run_rejects_json_for_mutations(home: Path) -> None:
    """The command entry point still rejects JSON for mutating actions."""
    with pytest.raises(typer.Exit) as caught:
        run_shlib(Action.INSTALL, True)
    assert caught.value.exit_code == 1


def test_shlib_without_operation_shows_status(home: Path) -> None:
    """Running ``lion shlib`` without an operation is ``lion shlib status``."""
    invoke("install")
    result = runner.invoke(app, ["shlib"])
    assert result.exit_code == 0
    assert "installed: True" in result.stdout


_HOLD_LOCK = """
import fcntl
import sys
import time

stream = open(sys.argv[1], "a")
fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
print("READY", flush=True)
time.sleep(60)
"""


def test_mutation_lock_blocks_second_process(home: Path) -> None:
    """A concurrent install in another process aborts instead of interleaving."""
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLD_LOCK, str(home / ".shlib.lock")],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "READY"
        result = runner.invoke(app, ["shlib", "install"])
        assert result.exit_code == 1
        assert "already running" in result.stderr
        assert not (home / ".zshrc").exists()
    finally:
        holder.terminate()
        holder.wait(timeout=10)


def test_mutation_lock_released_after_failure(home: Path) -> None:
    """A failed operation releases the lock so the next attempt can proceed."""
    (home / ".zshrc").write_text("# original\n")
    (home / ".zshrc.exports").write_text("conflict")
    assert runner.invoke(app, ["shlib", "install"]).exit_code == 1
    (home / ".zshrc.exports").unlink()
    assert runner.invoke(app, ["shlib", "install"]).exit_code == 0
    assert (home / ".zshrc").read_text().startswith(shlib.START)


def test_retained_lock_file_is_not_a_remnant(home: Path) -> None:
    """A permanently present process-lock file never blocks an install."""
    (home / ".shlib.lock").write_text("")
    assert runner.invoke(app, ["shlib", "install"]).exit_code == 0
    assert (home / ".zshrc").exists()


def test_status_does_not_create_lock(home: Path) -> None:
    """``shlib status`` stays read-only and never creates the process lock."""
    invoke("status")
    assert not (home / ".shlib.lock").exists()


@pytest.mark.parametrize("action", ["install", "uninstall"])
def test_write_failure_rollback(home: Path, monkeypatch: pytest.MonkeyPatch, action: str) -> None:
    """A failed rc publication leaves the original rc active and rolls back companions."""
    rc = home / ".zshrc"
    rc.write_text("# original\n")
    if action == "uninstall":
        invoke("install")
    before = rc.read_bytes()
    replace = os.replace

    def fail_rc(source: str | Path, target: str | Path) -> None:
        if Path(target) == rc:
            raise OSError("simulated publication failure")
        replace(source, target)

    monkeypatch.setattr(shlib.os, "replace", fail_rc)
    assert runner.invoke(app, ["shlib", action]).exit_code == 1
    assert rc.read_bytes() == before
    assert not (home / ".zshrc.exports").exists()
    if action == "install":
        assert not (home / ".zshrc.lock").exists()


@pytest.mark.parametrize("outcome", ["missing", "bad", "good", "timeout"])
def test_syntax_checker(monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    """Validate without executing code or exposing diagnostics with secrets."""
    monkeypatch.setattr(shlib.shutil, "which", Mock(return_value=None if outcome == "missing" else "/bin/zsh"))
    process = Mock(return_value=subprocess.CompletedProcess([], 1 if outcome == "bad" else 0, "", "SECRET"))
    if outcome == "timeout":
        process.side_effect = subprocess.TimeoutExpired("zsh", 10)
    monkeypatch.setattr(shlib.subprocess, "run", process)
    if outcome == "good":
        shlib._validate("echo SECRET")  # pyright: ignore[reportPrivateUsage]
        assert process.call_args.args[0] == ["/bin/zsh", "-f", "-n"]
    else:
        with pytest.raises((ValueError, subprocess.TimeoutExpired)) as caught:
            shlib._validate("echo SECRET")  # pyright: ignore[reportPrivateUsage]
        assert "SECRET" not in str(caught.value)


def test_invalid_syntax_leaves_rc(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Failed syntax validation and timeouts never publish configuration."""
    rc = home / ".zshrc"
    rc.write_text("invalid")
    for error in (ValueError("invalid syntax"), subprocess.TimeoutExpired("zsh", 10)):
        monkeypatch.setattr(shlib, "_validate", Mock(side_effect=error))
        assert runner.invoke(app, ["shlib", "install"]).exit_code == 1
        assert rc.read_text() == "invalid"
        assert not (home / ".shlib").exists()


def test_documented_manual_installation(home: Path) -> None:
    """Recognize and flatten the original documented manual installation."""
    invoke("install")
    legacy = (Path(__file__).parent / "fixtures" / "shlib-legacy.zsh").read_text()
    rc = home / ".zshrc"
    rc.write_text("# p10k stays here\n" + legacy + "# direnv stays at the end\n")
    assert json.loads(invoke("status", "--json"))["installed"]
    invoke("uninstall")
    assert rc.read_text().startswith("# p10k stays here\n")
    assert rc.read_text().endswith("# direnv stays at the end\n")


def _doctor_ctx(home: Path) -> DoctorContext:
    return DoctorContext(state={}, home=home)


def test_shlib_checks_not_installed_is_skip(home: Path) -> None:
    """An absent installation is a neutral skip."""
    findings = shlib.shlib_checks(_doctor_ctx(home))
    assert [finding.status for finding in findings] == [CheckStatus.SKIP]


def test_shlib_checks_installed_ok(home: Path) -> None:
    """A clean installation reports ok."""
    invoke("install")
    findings = shlib.shlib_checks(_doctor_ctx(home))
    assert [finding.status for finding in findings] == [CheckStatus.OK]


def test_shlib_checks_reports_warnings(home: Path) -> None:
    """Export warnings and a changed reference become warnings."""
    invoke("install")
    token = home / ".shlib" / "exports" / "bad-name"
    token.write_text("SECRET")
    token.chmod(0o644)
    (home / ".zshrc").write_text("# changed\n" + (home / ".zshrc").read_text())

    findings = shlib.shlib_checks(_doctor_ctx(home))
    statuses = [finding.status for finding in findings]

    assert statuses.count(CheckStatus.WARN) >= 3
    assert all("SECRET" not in finding.message for finding in findings)
    assert any(finding.name == "shlib.lock" for finding in findings)


def test_shlib_checks_dash_entries(home: Path) -> None:
    """A regular file and a broken symlink in ``dash`` are warnings."""
    invoke("install")
    dash = home / ".shlib" / "dash"
    (dash / "regular").write_text("x")
    (dash / "broken").symlink_to(home / "missing")

    findings = {finding.name: finding.status for finding in shlib.shlib_checks(_doctor_ctx(home))}

    assert findings["shlib.dash.regular"] == CheckStatus.WARN
    assert findings["shlib.dash.broken"] == CheckStatus.WARN


def test_shlib_checks_ambiguous_markers_is_error(home: Path) -> None:
    """Ambiguous markers become an error finding instead of raising."""
    (home / ".zshrc").write_text(shlib.START + "\n" + shlib.START + "\n")

    findings = shlib.shlib_checks(_doctor_ctx(home))

    assert len(findings) == 1
    assert findings[0].status == CheckStatus.ERROR
    assert findings[0].name == "shlib.status"


def test_shlib_checks_missing_dash_dir_is_ok(home: Path) -> None:
    """A missing ``dash`` directory is not a problem."""
    invoke("install")
    (home / ".shlib" / "dash").rmdir()

    findings = shlib.shlib_checks(_doctor_ctx(home))

    assert [finding.status for finding in findings] == [CheckStatus.OK]


def test_shlib_checks_dash_ignores_hidden(home: Path) -> None:
    """Hidden entries in ``dash`` are ignored."""
    invoke("install")
    dash = home / ".shlib" / "dash"
    (dash / ".hidden").write_text("x")

    findings = shlib.shlib_checks(_doctor_ctx(home))

    assert [finding.status for finding in findings] == [CheckStatus.OK]
