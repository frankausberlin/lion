"""Tests for the scoped doctor fixes."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

import lion.doctor.checks as checks
from lion.doctor import collect_diagnosis, execute_fixes, plan_fixes
from lion.doctor.model import FixAction, FixPlan
from lion.shlib.layout import ShlibLayout


def _mode(path: Path) -> int:
    """Return the permission bits of ``path``."""
    return stat.S_IMODE(path.stat().st_mode)


def test_plan_does_not_write(installed: ShlibLayout) -> None:
    """Planning creates no directories."""
    installed.exports.rmdir()
    installed.dash.rmdir()

    plan = plan_fixes(installed)

    assert plan.refused is None
    assert {action.kind for action in plan.actions} == {"create_dir"}
    assert not installed.exports.exists()
    assert not installed.dash.exists()


def test_fix_creates_missing_directories_with_mode_0700(installed: ShlibLayout) -> None:
    """Missing subdirectories are created with owner-only permissions."""
    installed.exports.rmdir()
    installed.dash.rmdir()

    outcome = execute_fixes(installed, plan_fixes(installed))

    assert outcome.errors == ()
    assert _mode(installed.exports) == 0o700
    assert _mode(installed.dash) == 0o700
    assert collect_diagnosis(installed).exit_code == 0


def test_fix_creates_missing_key_once(installed: ShlibLayout) -> None:
    """A missing key is created and a second plan has no key action."""
    installed.redaction_key.unlink()

    first = plan_fixes(installed)
    assert any(action.kind == "create_key" for action in first.actions)
    assert execute_fixes(installed, first).errors == ()
    assert len(installed.redaction_key.read_bytes()) == 32
    assert _mode(installed.redaction_key) == 0o600
    assert not any(action.kind == "create_key" for action in plan_fixes(installed).actions)


def test_fix_does_not_overwrite_an_existing_key(installed: ShlibLayout) -> None:
    """An existing key is never replaced."""
    original = installed.redaction_key.read_bytes()

    plan = plan_fixes(installed)

    assert not any(action.kind == "create_key" for action in plan.actions)
    assert installed.redaction_key.read_bytes() == original


def test_fix_removes_only_orphaned_locks(installed: ShlibLayout, home: Path) -> None:
    """An orphaned lock is deleted while dash links and paired locks survive."""
    target = home / "target"
    target.write_text("keep\n", encoding="utf-8")
    (installed.dash / "keep").symlink_to(target)
    installed.lock_for("keep").write_bytes(b"keep\n")
    orphan = installed.locks / "ghost.lock"
    orphan.write_bytes(b"x")

    plan = plan_fixes(installed)
    outcome = execute_fixes(installed, plan)

    assert outcome.errors == ()
    assert not orphan.exists()
    assert (installed.dash / "keep").is_symlink()
    assert installed.lock_for("keep").exists()


def test_fix_never_removes_a_non_regular_lock(installed: ShlibLayout) -> None:
    """A directory at a lock path is reported but never deleted."""
    lock = installed.locks / "ghost.lock"
    lock.mkdir()

    plan = plan_fixes(installed)
    outcome = execute_fixes(installed, plan)

    assert not any(action.kind == "remove_lock" for action in plan.actions)
    assert outcome.errors == ()
    assert lock.is_dir()


def test_missing_dash_defers_orphan_classification(installed: ShlibLayout) -> None:
    """The first fix creates dash and leaves pre-existing locks for a later run."""
    installed.dash.rmdir()
    orphan = installed.locks / "ghost.lock"
    orphan.write_bytes(b"x")

    plan = plan_fixes(installed)

    assert plan.notes
    assert "later explicit" in plan.notes[0]
    assert not any(action.kind == "remove_lock" for action in plan.actions)

    assert execute_fixes(installed, plan).errors == ()
    assert installed.dash.is_dir()
    assert orphan.exists()
    assert any(finding.code == "locks.orphan" for finding in collect_diagnosis(installed).findings)

    second = plan_fixes(installed)
    assert any(action.kind == "remove_lock" and action.name == "ghost" for action in second.actions)
    assert execute_fixes(installed, second).errors == ()
    assert not orphan.exists()


@pytest.mark.parametrize("state", ["missing", "symlink", "file"])
def test_fix_refuses_an_unusable_root(layout: ShlibLayout, home: Path, state: str) -> None:
    """Fixes are refused when .shlib is missing, symlinked or not a directory."""
    if state == "symlink":
        (home / "elsewhere").mkdir()
        layout.root.symlink_to(home / "elsewhere")
    elif state == "file":
        layout.root.write_text("x\n", encoding="utf-8")

    plan = plan_fixes(layout)

    assert plan.refused is not None
    assert plan.actions == ()


def test_fix_continues_after_an_action_failure(installed: ShlibLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed action is reported while later actions still run."""
    installed.exports.rmdir()
    installed.dash.rmdir()
    original = checks.os.chmod

    def fail_chmod(path: str | bytes | int | os.PathLike[str] | os.PathLike[bytes], mode: int) -> None:
        if path == installed.dash:
            raise OSError("chmod failed")
        original(path, mode)

    monkeypatch.setattr(checks.os, "chmod", fail_chmod)

    outcome = execute_fixes(installed, plan_fixes(installed))

    assert len(outcome.errors) == 1
    assert "chmod failed" in outcome.errors[0]
    assert installed.exports.is_dir()
    assert installed.dash.is_dir()


def test_fix_skips_a_key_that_appeared_meanwhile(installed: ShlibLayout) -> None:
    """A key created between planning and execution is not overwritten."""
    installed.redaction_key.unlink()
    plan = plan_fixes(installed)
    installed.redaction_key.write_bytes(b"x" * 32)

    outcome = execute_fixes(installed, plan)

    assert outcome.errors == ()
    assert installed.redaction_key.read_bytes() == b"x" * 32


def test_unknown_action_is_an_error(installed: ShlibLayout) -> None:
    """An unknown action is reported instead of crashing."""
    plan = FixPlan((FixAction(kind="unknown", path=installed.root, name=None, summary="unknown action"),))

    outcome = execute_fixes(installed, plan)

    assert outcome.errors
    assert "unknown action" in outcome.errors[0]


def test_missing_name_for_lock_removal_is_an_error(installed: ShlibLayout) -> None:
    """A lock removal without a name is reported."""
    plan = FixPlan((FixAction(kind="remove_lock", path=installed.locks, name=None, summary="remove lock"),))

    outcome = execute_fixes(installed, plan)

    assert any("missing lock name" in error for error in outcome.errors)
