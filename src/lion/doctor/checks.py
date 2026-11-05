"""Read-only checks and scoped fixes for ``lion doctor``."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from lion.dash.errors import DashError
from lion.dash.registry import (
    KIND_BROKEN,
    KIND_DIR,
    KIND_NOT_A_LINK,
    KIND_OTHER,
    list_entries,
    unlock_entry,
)
from lion.output import display_path, display_text
from lion.redaction import RedactionKeyError, create_key, read_key, scan_file
from lion.shlib.bootstrap import ORIGINAL_NAME
from lion.shlib.it import UNSORTED_NAME
from lion.shlib.layout import ShlibLayout
from lion.shlib.status import NOT_INSTALLED, StatusReport, collect_status
from lion.watch.errors import WatchError
from lion.watch.findings import collect_findings
from lion.watch.model import WatchFinding

from .model import (
    ERROR,
    INFO,
    WARNING,
    DiagnosisReport,
    Finding,
    FixAction,
    FixOutcome,
    FixPlan,
)

_EXPORT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DIRECTORIES = ("locks", "exports", "dash", "shlibs")


def collect_diagnosis(layout: ShlibLayout) -> DiagnosisReport:
    """Collect all shlib, dash and security findings without changing anything.

    The function is the reusable core for the future MCP ``diagnose`` tool and
    does not depend on Typer. It never prints or stores file contents.

    Args:
        layout: Paths below the selected home directory.

    Returns:
        The findings and their exit code.
    """
    status = collect_status(layout)
    home = layout.home
    findings: list[Finding] = []

    if status.installation == NOT_INSTALLED:
        findings.append(
            Finding(
                code="shlib.not_installed",
                severity=WARNING,
                summary=f"shlib is not installed below {display_path(layout.root, home)}",
                path=layout.root,
                hint="run `lion shlib install`",
            )
        )
        return _report(layout, status, findings)

    if layout.root.is_symlink() or not layout.root.is_dir():
        findings.extend(_inconsistent_findings(status, home))
        if not findings:
            findings.append(
                Finding(
                    code="shlib.inconsistent",
                    severity=ERROR,
                    summary=f"{display_path(layout.root, home)} is not a usable directory",
                    path=layout.root,
                )
            )
        return _report(layout, status, findings)

    findings.extend(_inconsistent_findings(status, home))
    findings.extend(_zshrc_findings(layout, status))
    findings.extend(_cleanup_findings(layout, status))
    findings.extend(_directory_findings(layout))
    findings.extend(_shlib_name_findings(layout))
    findings.extend(_export_name_findings(layout))
    findings.extend(_dash_findings(layout))
    findings.extend(_lock_findings(layout))
    findings.extend(_key_findings(layout))
    findings.extend(_security_findings(layout))
    if status.backups:
        findings.append(
            Finding(
                code="shlib.backups",
                severity=INFO,
                summary=f"{status.backups} uninstalled backup(s) remain in the home",
                path=home,
                hint="review and remove backups manually when they are no longer needed",
            )
        )
    return _report(layout, status, findings)


def plan_fixes(layout: ShlibLayout) -> FixPlan:
    """Return the actions ``lion doctor --fix`` would take.

    Args:
        layout: Paths below the selected home directory.

    Returns:
        The planned actions, notes and, for an unusable root, a refusal.
    """
    root = layout.root
    home = layout.home
    if root.is_symlink():
        return FixPlan((), refused=f"{display_path(root, home)} is a symlink; refusing to fix anything")
    if not root.exists():
        return FixPlan((), refused=f"{display_path(root, home)} is missing; run `lion shlib install`")
    if not root.is_dir():
        return FixPlan((), refused=f"{display_path(root, home)} is not a directory; refusing to fix anything")

    actions: list[FixAction] = []
    for name in _DIRECTORIES:
        path = _directory(layout, name)
        if not path.exists() and not path.is_symlink():
            actions.append(
                FixAction(
                    kind="create_dir",
                    path=path,
                    name=None,
                    summary=f"create {display_path(path, home)} with mode 0700",
                )
            )

    key = layout.redaction_key
    if not key.exists() and not key.is_symlink():
        actions.append(
            FixAction(
                kind="create_key",
                path=key,
                name=None,
                summary=f"create {display_path(key, home)} with mode 0600",
            )
        )

    if _is_real_directory(layout.dash) and _is_real_directory(layout.locks):
        for lock in _lock_files(layout.locks):
            name = lock.name[: -len(".lock")]
            if not _exists(layout.dash / name):
                actions.append(
                    FixAction(
                        kind="remove_lock",
                        path=lock,
                        name=name,
                        summary=f"remove orphaned lock {display_path(lock, home)}",
                    )
                )

    notes: list[str] = []
    dash_created = any(action.kind == "create_dir" and action.path == layout.dash for action in actions)
    if dash_created and _is_real_directory(layout.locks) and _lock_files(layout.locks):
        notes.append("pre-existing locks will be classified as orphaned only on a later explicit `lion doctor --fix`")
    return FixPlan(tuple(actions), tuple(notes))


def execute_fixes(layout: ShlibLayout, plan: FixPlan) -> FixOutcome:
    """Apply a fix plan, continuing after individual failures.

    Args:
        layout: Paths below the selected home directory.
        plan: Plan returned by :func:`plan_fixes`.

    Returns:
        One non-secret error message per failed action.
    """
    errors: list[str] = []
    for action in plan.actions:
        try:
            _apply_action(layout, action)
        except (OSError, DashError, RedactionKeyError) as exc:
            errors.append(f"{action.summary}: {exc}")
    return FixOutcome(tuple(errors))


def _apply_action(layout: ShlibLayout, action: FixAction) -> None:
    """Apply one fix action."""
    if action.kind == "create_dir":
        if action.path.exists() or action.path.is_symlink():
            if action.path.is_dir() and not action.path.is_symlink():
                return
            raise OSError("path appeared and is not a real directory")
        action.path.mkdir(mode=0o700)
        os.chmod(action.path, 0o700)
        return
    if action.kind == "create_key":
        try:
            create_key(action.path)
        except FileExistsError:
            return
        return
    if action.kind == "remove_lock":
        if action.name is None:
            raise OSError("missing lock name")
        unlock_entry(layout, action.name)
        return
    raise OSError(f"unknown fix action: {action.kind}")


def _report(layout: ShlibLayout, status: StatusReport, findings: list[Finding]) -> DiagnosisReport:
    """Build the report and derive the exit code from severities."""
    if any(finding.severity == ERROR for finding in findings):
        exit_code = 2
    elif any(finding.severity == WARNING for finding in findings):
        exit_code = 1
    else:
        exit_code = 0
    return DiagnosisReport(layout=layout, status=status, findings=tuple(findings), exit_code=exit_code)


def _inconsistent_findings(status: StatusReport, home: Path) -> list[Finding]:
    """Return one error per status issue."""
    return [
        Finding(
            code="shlib.inconsistent",
            severity=ERROR,
            summary=display_text(issue, home),
            hint="inspect the path manually; doctor never follows or replaces it",
        )
        for issue in status.issues
    ]


def _zshrc_findings(layout: ShlibLayout, status: StatusReport) -> list[Finding]:
    """Return findings for a drifted ``.zshrc``."""
    if status.zshrc != "drift":
        return []
    detail = status.zshrc_detail or "other changes"
    hint = "run `lion shlib it`" if "appended" in detail else None
    return [
        Finding(
            code="shlib.zshrc_drift",
            severity=WARNING,
            summary=f"{display_path(layout.zshrc, layout.home)} differs from the lock file ({detail})",
            path=layout.zshrc,
            detail=detail,
            hint=hint,
        )
    ]


def _cleanup_findings(layout: ShlibLayout, status: StatusReport) -> list[Finding]:
    """Return findings for the two cleanup-only shlib files."""
    home = layout.home
    findings: list[Finding] = []
    if status.original_exists:
        findings.append(
            Finding(
                code="shlib.cleanup_original",
                severity=WARNING,
                summary=f"{display_path(layout.original, home)} is a cleanup file ({_cleanup_size(status.original)})",
                path=layout.original,
                hint=f"resolve {ORIGINAL_NAME} into sorted NN-*.sh files, then delete it",
            )
        )
    if status.unsorted_exists:
        findings.append(
            Finding(
                code="shlib.cleanup_unsorted",
                severity=WARNING,
                summary=(
                    f"{display_path(layout.shlibs / UNSORTED_NAME, home)} is a cleanup file "
                    f"({_cleanup_size(status.unsorted)})"
                ),
                path=layout.shlibs / UNSORTED_NAME,
                hint=f"resolve {UNSORTED_NAME} into sorted NN-*.sh files, then delete it",
            )
        )
    return findings


def _cleanup_size(state: str) -> str:
    """Return the human-readable size part of a status cleanup state."""
    return state[4:] if state.startswith("yes ") else state


def _directory_findings(layout: ShlibLayout) -> list[Finding]:
    """Return findings for missing, symlinked or non-directory subdirectories."""
    home = layout.home
    findings: list[Finding] = []
    for name in _DIRECTORIES:
        path = _directory(layout, name)
        display = display_path(path, home)
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            findings.append(
                Finding(
                    code="shlib.invalid_dir",
                    severity=ERROR,
                    summary=f"{display} is a symlink or not a real directory",
                    path=path,
                    hint="inspect it manually; doctor never follows or replaces it",
                )
            )
            continue
        if path.exists():
            continue
        if name == "shlibs":
            summary = f"{display} is missing; --fix creates it, but its content is not restored"
            severity = ERROR
        else:
            summary = f"{display} is missing; --fix creates it with mode 0700"
            severity = WARNING
        findings.append(
            Finding(
                code="shlib.missing_dir",
                severity=severity,
                summary=summary,
                path=path,
                fixable=True,
            )
        )
    return findings


def _shlib_name_findings(layout: ShlibLayout) -> list[Finding]:
    """Return findings for regular shlib files the bootstrap does not source."""
    directory = layout.shlibs
    if not _is_real_directory(directory):
        return []
    home = layout.home
    try:
        entries = _file_entries(directory)
    except OSError as exc:
        return [
            Finding(
                code="shlib.scan_error",
                severity=ERROR,
                summary=f"cannot list {display_path(directory, home)}: {exc}",
                path=directory,
            )
        ]
    findings: list[Finding] = []
    for entry in entries:
        name = entry.name
        if _is_sourced(name):
            continue
        severity = WARNING if name.endswith(".sh") or name[:1].isdigit() else INFO
        hint = None
        if severity == WARNING:
            hint = "rename it to NN-*.sh if it should be sourced; doctor never renames files"
        findings.append(
            Finding(
                code="shlib.invalid_name",
                severity=severity,
                summary=f"{display_path(entry, home)} is not sourced by the bootstrap",
                path=entry,
                hint=hint,
            )
        )
    return findings


def _export_name_findings(layout: ShlibLayout) -> list[Finding]:
    """Return findings for export files that are not valid shell identifiers."""
    directory = layout.exports
    if not _is_real_directory(directory):
        return []
    home = layout.home
    try:
        entries = _file_entries(directory)
    except OSError as exc:
        return [
            Finding(
                code="shlib.scan_error",
                severity=ERROR,
                summary=f"cannot list {display_path(directory, home)}: {exc}",
                path=directory,
            )
        ]
    return [
        Finding(
            code="exports.invalid_name",
            severity=WARNING,
            summary=f"{display_path(entry, home)} is not a valid export name",
            path=entry,
            hint="rename it to a valid shell identifier; doctor never renames files",
        )
        for entry in entries
        if _EXPORT_NAME.fullmatch(entry.name) is None
    ]


def _dash_findings(layout: ShlibLayout) -> list[Finding]:
    """Return findings for dash entries and their watch state."""
    directory = layout.dash
    if not _is_real_directory(directory):
        return []
    home = layout.home
    findings: list[Finding] = []
    unlocked = 0
    try:
        entries = list_entries(layout)
    except DashError as exc:
        findings.append(
            Finding(
                code="dash.scan_error",
                severity=ERROR,
                summary=f"cannot inspect {display_path(directory, home)}: {exc}",
                path=directory,
            )
        )
        entries = ()
    for entry in entries:
        display = display_path(entry.path, home)
        if entry.kind == KIND_BROKEN:
            findings.append(
                Finding(
                    code="dash.broken_link",
                    severity=WARNING,
                    summary=f"dash entry {entry.name!r} is a broken link",
                    path=entry.path,
                    hint=f"rm {display}",
                )
            )
        elif entry.kind == KIND_NOT_A_LINK:
            findings.append(
                Finding(
                    code="dash.not_a_link",
                    severity=WARNING,
                    summary=f"dash entry {entry.name!r} is not a symlink",
                    path=entry.path,
                    hint="lock and watch cannot use it; replace or remove it manually",
                )
            )
        elif entry.kind == KIND_DIR:
            findings.append(
                Finding(
                    code="dash.directory",
                    severity=WARNING,
                    summary=f"dash entry {entry.name!r} points to a directory",
                    path=entry.path,
                    hint="lock and watch cannot use it; retarget or remove it manually",
                )
            )
        elif entry.kind == KIND_OTHER:
            findings.append(
                Finding(
                    code="dash.other",
                    severity=WARNING,
                    summary=f"dash entry {entry.name!r} has an unusable target",
                    path=entry.path,
                    hint="lock and watch cannot use it; retarget or remove it manually",
                )
            )
        elif not entry.locked:
            unlocked += 1

    if unlocked:
        findings.append(
            Finding(
                code="dash.unlocked",
                severity=INFO,
                summary=f"{unlocked} unlocked dash entr(y/ies)",
                hint="run `lion dash lock <name>` for entries you want to watch",
            )
        )

    try:
        watch_findings = collect_findings(layout, render_diffs=False)
    except WatchError as exc:
        findings.append(
            Finding(
                code="dash.scan_error",
                severity=ERROR,
                summary=f"cannot inspect the dash register: {exc}",
                path=directory,
            )
        )
        return findings
    for finding in watch_findings:
        mapped = _watch_finding(finding)
        if mapped is not None:
            findings.append(mapped)
    return findings


def _watch_finding(finding: WatchFinding) -> Finding | None:
    """Map a watch finding to a doctor finding without its diff body."""
    if finding.kind in {"changed", "binary_changed"}:
        what = "binary content changed" if finding.kind == "binary_changed" else "changed since it was locked"
        return Finding(
            code="dash.locked_changed",
            severity=WARNING,
            summary=f"dash entry {finding.name!r} {what}",
            hint=f"restore the target or run `lion dash lock {finding.name}`",
        )
    if finding.kind == "missing":
        return Finding(
            code="dash.missing_target",
            severity=WARNING,
            summary=f"dash entry {finding.name!r} has a missing target",
            hint=f"restore the target or run `lion dash lock {finding.name}`",
        )
    if finding.kind == "read_error":
        return Finding(
            code="dash.read_error",
            severity=ERROR,
            summary=f"cannot read the lock or target for {finding.name!r}",
        )
    return None


def _lock_findings(layout: ShlibLayout) -> list[Finding]:
    """Return findings for non-regular and orphaned lock files."""
    locks = layout.locks
    if not _is_real_directory(locks):
        return []
    home = layout.home
    try:
        children = list(locks.iterdir())
    except OSError as exc:
        return [
            Finding(
                code="locks.scan_error",
                severity=ERROR,
                summary=f"cannot list {display_path(locks, home)}: {exc}",
                path=locks,
            )
        ]
    dash_exists = _is_real_directory(layout.dash)
    findings: list[Finding] = []
    for child in sorted(children, key=lambda path: path.name):
        if child.name.startswith(".") or not child.name.endswith(".lock"):
            continue
        name = child.name[: -len(".lock")]
        if child.is_symlink() or not child.is_file():
            findings.append(
                Finding(
                    code="dash.lock_not_regular",
                    severity=ERROR,
                    summary=f"{display_path(child, home)} is a symlink or not a regular file",
                    path=child,
                    hint="remove or repair the lock manually; doctor refuses non-regular locks",
                )
            )
            continue
        if not dash_exists:
            continue
        if not _exists(layout.dash / name):
            findings.append(
                Finding(
                    code="locks.orphan",
                    severity=WARNING,
                    summary=f"{display_path(child, home)} has no dash entry",
                    path=child,
                    fixable=True,
                )
            )
    return findings


def _key_findings(layout: ShlibLayout) -> list[Finding]:
    """Return findings for the redaction key."""
    path = layout.redaction_key
    home = layout.home
    try:
        key = read_key(path)
    except RedactionKeyError as exc:
        return [
            Finding(
                code="redaction.key_invalid",
                severity=ERROR,
                summary=display_text(str(exc), home),
                path=path,
                hint="inspect or remove the key manually; doctor never replaces it",
            )
        ]
    if key is None:
        return [
            Finding(
                code="redaction.key_missing",
                severity=WARNING,
                summary=f"{display_path(path, home)} is missing; masks will have no fingerprint",
                path=path,
                fixable=True,
                hint="run `lion doctor --fix`",
            )
        ]
    return []


def _security_findings(layout: ShlibLayout) -> list[Finding]:
    """Return metadata-only secret findings for exports and shlib files."""
    findings = _scan_export_findings(layout)
    findings.extend(_scan_shlib_findings(layout))
    return findings


def _scan_export_findings(layout: ShlibLayout) -> list[Finding]:
    """Scan export files and report metadata only."""
    directory = layout.exports
    if not _is_real_directory(directory):
        return []
    home = layout.home
    try:
        entries = _file_entries(directory)
    except OSError as exc:
        return [
            Finding(
                code="scan.skipped",
                severity=INFO,
                summary=f"cannot list {display_path(directory, home)}: {exc}",
                path=directory,
            )
        ]
    findings: list[Finding] = []
    for entry in entries:
        result = scan_file(entry, key_name=entry.name)
        display = display_path(entry, home)
        if result.skipped is not None:
            findings.append(
                Finding(
                    code="scan.skipped",
                    severity=INFO,
                    summary=f"{display}: not scanned ({result.skipped})",
                    path=entry,
                )
            )
            continue
        try:
            mode = stat.S_IMODE(entry.stat().st_mode)
        except OSError:
            mode = None
        public = mode is None or mode & 0o077 != 0
        severity = WARNING if public else INFO
        mode_text = f"{mode:04o}" if mode is not None else "unknown"
        for secret, line in zip(result.findings, result.line_numbers, strict=True):
            key_name = secret.key_name or entry.name
            hint = None
            if public:
                hint = f"review the value and run `chmod 600 {display}`; doctor never changes secrets"
            findings.append(
                Finding(
                    code="exports.possible_secret",
                    severity=severity,
                    summary=(
                        f"{display}: line {line}: possible {secret.kind} in {key_name!r} "
                        f"(confidence {secret.confidence}%, mode {mode_text})"
                    ),
                    path=entry,
                    line=line,
                    detail=f"rule {secret.rule}",
                    hint=hint,
                )
            )
    return findings


def _scan_shlib_findings(layout: ShlibLayout) -> list[Finding]:
    """Scan shlib files and report metadata only."""
    directory = layout.shlibs
    if not _is_real_directory(directory):
        return []
    home = layout.home
    try:
        entries = _file_entries(directory)
    except OSError as exc:
        return [
            Finding(
                code="scan.skipped",
                severity=INFO,
                summary=f"cannot list {display_path(directory, home)}: {exc}",
                path=directory,
            )
        ]
    findings: list[Finding] = []
    for entry in entries:
        result = scan_file(entry)
        display = display_path(entry, home)
        if result.skipped is not None:
            findings.append(
                Finding(
                    code="scan.skipped",
                    severity=INFO,
                    summary=f"{display}: not scanned ({result.skipped})",
                    path=entry,
                )
            )
            continue
        for secret, line in zip(result.findings, result.line_numbers, strict=True):
            findings.append(
                Finding(
                    code="shlibs.possible_secret",
                    severity=WARNING,
                    summary=(f"{display}: line {line}: possible {secret.kind} (confidence {secret.confidence}%)"),
                    path=entry,
                    line=line,
                    detail=f"rule {secret.rule}",
                    hint=(
                        "move the value to an owner-only export or a future secret mechanism; "
                        "chmod 600 on the shlib file is not sufficient because uninstall inlines shlib files"
                    ),
                )
            )
    return findings


def _directory(layout: ShlibLayout, name: str) -> Path:
    """Return one named shlib subdirectory."""
    if name == "locks":
        return layout.locks
    if name == "exports":
        return layout.exports
    if name == "dash":
        return layout.dash
    return layout.shlibs


def _is_real_directory(path: Path) -> bool:
    """Return whether ``path`` is a real directory and not a symlink."""
    return not path.is_symlink() and path.is_dir()


def _is_sourced(name: str) -> bool:
    """Return whether the bootstrap sources a shlib file name."""
    return len(name) >= 2 and name[:2].isdigit()


def _file_entries(directory: Path) -> tuple[Path, ...]:
    """Return sorted non-hidden regular files or symlinks to regular files."""
    children = list(directory.iterdir())
    files = (child for child in children if not child.name.startswith(".") and child.is_file())
    return tuple(sorted(files, key=lambda path: path.name))


def _lock_files(directory: Path) -> list[Path]:
    """Return regular, non-hidden ``*.lock`` files, sorted by name."""
    try:
        children = list(directory.iterdir())
    except OSError:
        return []
    return [
        child
        for child in sorted(children, key=lambda path: path.name)
        if (
            not child.name.startswith(".")
            and child.name.endswith(".lock")
            and not child.is_symlink()
            and child.is_file()
        )
    ]


def _exists(path: Path) -> bool:
    """Return whether a path exists, including a dangling symlink."""
    return path.exists() or path.is_symlink()
