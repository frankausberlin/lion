"""Tests for the doctor checks."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from lion.doctor import collect_diagnosis
from lion.shlib.layout import ShlibLayout


def _codes(layout: ShlibLayout) -> list[str]:
    """Return the finding codes for a layout."""
    return [finding.code for finding in collect_diagnosis(layout).findings]


def test_not_installed_is_a_warning(home: Path, layout: ShlibLayout) -> None:
    """A missing shlib root is a warning with an install hint."""
    report = collect_diagnosis(layout)

    assert report.exit_code == 1
    assert [finding.code for finding in report.findings] == ["shlib.not_installed"]
    assert report.findings[0].hint == "run `lion shlib install`"
    assert home.exists()


def test_installed_clean_state_exits_zero(installed: ShlibLayout) -> None:
    """An installed tree without cleanup files and findings is clean."""
    report = collect_diagnosis(installed)

    assert report.exit_code == 0
    assert report.findings == ()
    assert report.fixable == ()
    assert report.manual == ()


def test_zshrc_drift_is_a_warning_with_it_hint(installed: ShlibLayout) -> None:
    """Appended .zshrc content points at `lion shlib it`."""
    with installed.zshrc.open("a", encoding="utf-8") as handle:
        handle.write("export EXTRA=1\n")

    report = collect_diagnosis(installed)

    assert report.exit_code == 1
    finding = next(finding for finding in report.findings if finding.code == "shlib.zshrc_drift")
    assert finding.hint == "run `lion shlib it`"
    assert "appended" in (finding.detail or "")


def test_cleanup_files_warn(installed: ShlibLayout) -> None:
    """Both cleanup-only shlib files produce warnings."""
    installed.original.write_text("old\n", encoding="utf-8")
    (installed.shlibs / "99-unsorted.sh").write_text("new\n", encoding="utf-8")

    report = collect_diagnosis(installed)

    assert report.exit_code == 1
    assert {"shlib.cleanup_original", "shlib.cleanup_unsorted"} <= set(_codes(installed))
    for finding in report.findings:
        if finding.code.startswith("shlib.cleanup"):
            assert "resolve" in (finding.hint or "")


def test_missing_directories_are_fixable(installed: ShlibLayout) -> None:
    """Missing exports and dash are fixable warnings."""
    for directory in (installed.exports, installed.dash):
        shutil.rmtree(directory)

    report = collect_diagnosis(installed)

    fixable = {finding.summary for finding in report.fixable}
    assert report.exit_code == 1
    assert len(report.fixable) == 2
    assert any("exports" in summary for summary in fixable)
    assert any("dash" in summary for summary in fixable)


def test_missing_locks_is_fixable_but_status_is_inconsistent(installed: ShlibLayout) -> None:
    """A missing locks directory is fixable and also breaks the status check."""
    shutil.rmtree(installed.locks)

    report = collect_diagnosis(installed)

    assert report.exit_code == 2
    assert any(finding.code == "shlib.missing_dir" and finding.fixable for finding in report.findings)
    assert any(finding.code == "shlib.inconsistent" for finding in report.findings)


def test_missing_shlibs_is_an_error_that_says_content_is_lost(installed: ShlibLayout) -> None:
    """A missing shlibs directory is an error and does not promise restoration."""
    shutil.rmtree(installed.shlibs)

    report = collect_diagnosis(installed)

    assert report.exit_code == 2
    finding = next(finding for finding in report.findings if finding.code == "shlib.missing_dir")
    assert finding.severity == "error"
    assert "content is not restored" in finding.summary


def test_symlinked_subdirectory_is_an_error(installed: ShlibLayout, home: Path) -> None:
    """A symlinked subdirectory is reported and never followed."""
    outside = home / "outside"
    outside.mkdir()
    shutil.rmtree(installed.exports)
    installed.exports.symlink_to(outside)

    report = collect_diagnosis(installed)

    assert report.exit_code == 2
    assert "shlib.invalid_dir" in _codes(installed)


def test_invalid_shlib_names_are_reported(installed: ShlibLayout) -> None:
    """Names that the bootstrap does not source are reported by severity."""
    (installed.shlibs / "README").write_text("x\n", encoding="utf-8")
    (installed.shlibs / "bad.sh").write_text("x\n", encoding="utf-8")
    (installed.shlibs / "1-one.sh").write_text("x\n", encoding="utf-8")
    (installed.shlibs / "01-ok.sh").write_text("x\n", encoding="utf-8")

    report = collect_diagnosis(installed)
    findings = [finding for finding in report.findings if finding.code == "shlib.invalid_name"]

    by_name = {finding.path.name: finding for finding in findings if finding.path is not None}
    assert by_name["README"].severity == "info"
    assert by_name["bad.sh"].severity == "warning"
    assert by_name["1-one.sh"].severity == "warning"
    assert "01-ok.sh" not in by_name
    assert report.exit_code == 1


def test_invalid_export_names_are_reported(installed: ShlibLayout) -> None:
    """Export files must be valid shell identifiers."""
    (installed.exports / "bad-name").write_text("x", encoding="utf-8")
    (installed.exports / "GOOD_NAME").write_text("x", encoding="utf-8")

    report = collect_diagnosis(installed)
    findings = [finding for finding in report.findings if finding.code == "exports.invalid_name"]

    assert [finding.path.name for finding in findings if finding.path is not None] == ["bad-name"]
    assert report.exit_code == 1


def test_broken_dash_link_is_reported_with_rm_hint(installed: ShlibLayout, home: Path) -> None:
    """A broken dash link is a warning and is not removed."""
    entry = installed.dash / "conf"
    entry.symlink_to(home / "missing")

    report = collect_diagnosis(installed)
    finding = next(finding for finding in report.findings if finding.code == "dash.broken_link")

    assert report.exit_code == 1
    assert finding.hint is not None
    assert finding.hint.startswith("rm ")
    assert entry.is_symlink()


def test_dash_entry_kinds_are_reported(installed: ShlibLayout, home: Path) -> None:
    """Non-link, directory and special targets have distinct findings."""
    (installed.dash / "plain").write_text("x\n", encoding="utf-8")
    folder = home / "folder"
    folder.mkdir()
    (installed.dash / "folder").symlink_to(folder)
    fifo = home / "fifo"
    os.mkfifo(fifo)
    (installed.dash / "fifo").symlink_to(fifo)

    report = collect_diagnosis(installed)
    codes = _codes(installed)

    assert "dash.not_a_link" in codes
    assert "dash.directory" in codes
    assert "dash.other" in codes
    assert report.exit_code == 1


def test_unlocked_entries_are_info_only(installed: ShlibLayout, home: Path) -> None:
    """An unlocked dash entry is counted as info and does not change the exit code."""
    target = home / "target"
    target.write_text("x\n", encoding="utf-8")
    (installed.dash / "conf").symlink_to(target)

    report = collect_diagnosis(installed)
    finding = next(finding for finding in report.findings if finding.code == "dash.unlocked")

    assert finding.severity == "info"
    assert report.exit_code == 0


def test_changed_locked_entry_has_no_diff_body(installed: ShlibLayout, home: Path) -> None:
    """A changed locked entry is a warning without the changed content."""
    target = home / "target"
    target.write_text("new value\n", encoding="utf-8")
    (installed.dash / "conf").symlink_to(target)
    installed.lock_for("conf").write_bytes(b"old value\n")

    report = collect_diagnosis(installed)
    finding = next(finding for finding in report.findings if finding.code == "dash.locked_changed")

    assert report.exit_code == 1
    assert "new value" not in finding.summary
    assert "old value" not in finding.summary
    assert "--- lock" not in finding.summary
    assert finding.hint is not None and "lion dash lock conf" in finding.hint


def test_orphan_lock_is_fixable_but_not_removed(installed: ShlibLayout) -> None:
    """A lock without a dash entry is fixable and stays until `--fix`."""
    lock = installed.locks / "ghost.lock"
    lock.write_bytes(b"x")

    report = collect_diagnosis(installed)
    finding = next(finding for finding in report.findings if finding.code == "locks.orphan")

    assert finding.fixable
    assert report.exit_code == 1
    assert lock.exists()


def test_non_lock_entries_and_shlib_lock_are_skipped(installed: ShlibLayout) -> None:
    """Non-lock files and hidden entries are never classified as orphans."""
    (installed.locks / "notes.txt").write_text("x\n", encoding="utf-8")
    (installed.locks / ".hidden.lock").write_text("x\n", encoding="utf-8")

    report = collect_diagnosis(installed)

    assert "locks.orphan" not in _codes(installed)
    assert report.exit_code == 0


def test_orphans_are_not_classified_without_dash(installed: ShlibLayout) -> None:
    """Locks are not called orphaned while the dash directory is missing."""
    shutil.rmtree(installed.dash)
    (installed.locks / "ghost.lock").write_bytes(b"x")

    codes = _codes(installed)

    assert "shlib.missing_dir" in codes
    assert "locks.orphan" not in codes


def test_non_regular_lock_is_an_error(installed: ShlibLayout) -> None:
    """A directory at a lock path is an error, not an orphan action."""
    (installed.locks / "ghost.lock").mkdir()

    report = collect_diagnosis(installed)

    assert report.exit_code == 2
    assert "dash.lock_not_regular" in _codes(installed)


def test_public_export_secret_is_a_warning_without_content(installed: ShlibLayout) -> None:
    """A public export with a secret is a warning that never prints the value."""
    path = installed.exports / "DB_PASSWORD"
    path.write_text("hunter2\n", encoding="utf-8")
    path.chmod(0o644)

    report = collect_diagnosis(installed)
    finding = next(finding for finding in report.findings if finding.code == "exports.possible_secret")

    assert report.exit_code == 1
    assert finding.severity == "warning"
    assert "possible password" in finding.summary
    assert "0644" in finding.summary
    assert "confidence" in finding.summary
    assert "hunter2" not in finding.summary
    assert "hunter2" not in repr(finding)
    assert finding.hint is not None and "chmod 600" in finding.hint


def test_owner_only_export_secret_is_info_only(installed: ShlibLayout) -> None:
    """An owner-only export gets an info finding and does not change the exit code."""
    path = installed.exports / "DB_PASSWORD"
    path.write_text("hunter2\n", encoding="utf-8")
    path.chmod(0o600)

    report = collect_diagnosis(installed)
    finding = next(finding for finding in report.findings if finding.code == "exports.possible_secret")

    assert finding.severity == "info"
    assert report.exit_code == 0


def test_shlib_secret_warns_about_uninstall_inlining(installed: ShlibLayout) -> None:
    """A secret in a shlib file is a warning with the uninstall caveat."""
    token = "ghp_" + "a" * 36
    path = installed.shlibs / "10-api.sh"
    path.write_text(f"GITHUB_TOKEN={token}\n", encoding="utf-8")

    report = collect_diagnosis(installed)
    finding = next(finding for finding in report.findings if finding.code == "shlibs.possible_secret")

    assert report.exit_code == 1
    assert finding.severity == "warning"
    assert finding.line == 1
    assert "github-token" in finding.summary
    assert token not in finding.summary
    assert token not in repr(finding)
    assert "uninstall inlines" in (finding.hint or "")


def test_binary_export_is_skipped_as_info(installed: ShlibLayout) -> None:
    """Binary export files are reported as skipped without affecting the exit code."""
    (installed.exports / "BINARY").write_bytes(b"a\x00b")

    report = collect_diagnosis(installed)

    assert report.exit_code == 0
    finding = next(finding for finding in report.findings if finding.code == "scan.skipped")
    assert finding.severity == "info"
    assert "binary content" in finding.summary


def test_missing_key_is_fixable(installed: ShlibLayout) -> None:
    """A missing redaction key is a fixable warning."""
    installed.redaction_key.unlink()

    report = collect_diagnosis(installed)
    finding = next(finding for finding in report.findings if finding.code == "redaction.key_missing")

    assert finding.fixable
    assert report.exit_code == 1


def test_invalid_key_is_an_error(installed: ShlibLayout) -> None:
    """A directory at the key path is an error and is not replaced."""
    installed.redaction_key.unlink()
    installed.redaction_key.mkdir()

    report = collect_diagnosis(installed)

    assert report.exit_code == 2
    assert "redaction.key_invalid" in _codes(installed)


def test_backup_count_is_info_only(installed: ShlibLayout, home: Path) -> None:
    """A backup directory is informational."""
    (home / ".shlib.uninstalled-20260101T000000Z").mkdir()

    report = collect_diagnosis(installed)

    assert report.exit_code == 0
    assert "shlib.backups" in _codes(installed)


def test_manual_count_excludes_fixable_and_info(installed: ShlibLayout, home: Path) -> None:
    """Manual counts only cover non-fixable warnings and errors."""
    (installed.dash / "conf").symlink_to(home / "missing")
    installed.redaction_key.unlink()

    report = collect_diagnosis(installed)

    assert len(report.fixable) == 1
    assert all(not finding.fixable for finding in report.manual)
    assert all(finding.severity in {"warning", "error"} for finding in report.manual)
