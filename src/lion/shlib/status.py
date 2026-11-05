"""Read-only status model for ``lion shlib status``."""

from __future__ import annotations

import stat
from dataclasses import dataclass

from lion.shlib.bootstrap import BOOTSTRAP_BEGIN
from lion.shlib.errors import ShlibError
from lion.shlib.it import UNSORTED_NAME
from lion.shlib.layout import ShlibLayout
from lion.shlib.reference import load_reference, split_tail

INSTALLED = "installed"
NOT_INSTALLED = "not installed"
INCONSISTENT = "inconsistent"


@dataclass(frozen=True, slots=True)
class StatusReport:
    """Snapshot of the shlib installation.

    Attributes:
        layout: Paths below the selected home directory.
        installation: ``installed``, ``not installed`` or ``inconsistent``.
        zshrc: ``ok``, ``drift``, ``missing`` or ``inconsistent``.
        zshrc_detail: Why ``.zshrc`` drifted, or ``None``.
        exports: Number of regular, non-hidden export files.
        owner_only_exports: Number of those files with ``mode & 0o077 == 0``.
        shlibs: Number of files the bootstrap sources.
        unsorted: Human-readable state of ``99-unsorted.sh``.
        unsorted_exists: Whether ``99-unsorted.sh`` exists as an entry.
        backups: Number of ``~/.shlib.uninstalled-*`` directories.
        issues: Non-fatal problems found while collecting the status.
        exit_code: ``0`` ok, ``1`` warning or not installed, ``2`` inconsistent.
    """

    layout: ShlibLayout
    installation: str
    zshrc: str
    zshrc_detail: str | None
    exports: int
    owner_only_exports: int
    shlibs: int
    unsorted: str
    unsorted_exists: bool
    backups: int
    issues: tuple[str, ...]
    exit_code: int


def collect_status(layout: ShlibLayout) -> StatusReport:
    """Collect the status of a shlib installation without changing anything.

    Export files are counted by metadata only; their contents are never read.
    Unexpected filesystem errors become issues instead of tracebacks.

    Args:
        layout: Paths below the selected home directory.

    Returns:
        The status snapshot and its exit code.
    """
    issues: list[str] = []
    installation = _installation(layout, issues)
    zshrc, zshrc_detail = _zshrc_state(layout, installation, issues)
    exports, owner_only = _count_exports(layout, issues)
    shlibs = _count_shlibs(layout, issues)
    unsorted, unsorted_exists = _unsorted_state(layout, issues)
    backups = _count_backups(layout, issues)

    inconsistent = issues or installation == INCONSISTENT
    warning = installation != INSTALLED or zshrc == "drift" or unsorted_exists
    if inconsistent:
        exit_code = 2
    elif warning:
        exit_code = 1
    else:
        exit_code = 0

    return StatusReport(
        layout=layout,
        installation=installation,
        zshrc=zshrc,
        zshrc_detail=zshrc_detail,
        exports=exports,
        owner_only_exports=owner_only,
        shlibs=shlibs,
        unsorted=unsorted,
        unsorted_exists=unsorted_exists,
        backups=backups,
        issues=tuple(issues),
        exit_code=exit_code,
    )


def _installation(layout: ShlibLayout, issues: list[str]) -> str:
    """Return the installation state and record issues."""
    root = layout.root
    if root.is_symlink():
        issues.append(f"{root} is a symlink; shlib does not support symlinked roots")
        return INCONSISTENT
    if root.exists() and not root.is_dir():
        issues.append(f"{root} is not a directory")
        return INCONSISTENT
    if not root.exists():
        if _has_bootstrap_marker(layout, issues):
            issues.append(f"{root} is missing but {layout.zshrc} contains the shlib bootstrap")
            return INCONSISTENT
        return NOT_INSTALLED

    inconsistent = False
    shlibs = layout.shlibs
    if shlibs.is_symlink() or not shlibs.is_dir():
        issues.append(f"{shlibs} is missing or not a real directory")
        inconsistent = True

    lock_file = layout.lock_file
    if lock_file.is_symlink() or not lock_file.is_file():
        issues.append(f"{lock_file} is missing or not a regular file")
        inconsistent = True

    zshrc = layout.zshrc
    if not zshrc.exists():
        issues.append(f"{zshrc} does not exist")
        inconsistent = True
    elif zshrc.is_symlink() or not zshrc.is_file():
        issues.append(f"{zshrc} is not a regular file")
        inconsistent = True

    return INCONSISTENT if inconsistent else INSTALLED


def _has_bootstrap_marker(layout: ShlibLayout, issues: list[str]) -> bool:
    """Return whether ``.zshrc`` contains the bootstrap marker without raising."""
    zshrc = layout.zshrc
    if zshrc.is_symlink() or not zshrc.is_file():
        return False
    try:
        return BOOTSTRAP_BEGIN.encode() in zshrc.read_bytes()
    except OSError as exc:
        issues.append(f"cannot read {zshrc}: {exc}")
        return False


def _zshrc_state(layout: ShlibLayout, installation: str, issues: list[str]) -> tuple[str, str | None]:
    """Return the ``.zshrc`` state and drift reason."""
    zshrc = layout.zshrc
    if zshrc.is_symlink():
        issues.append(f"{zshrc} is a symlink; shlib does not read symlinked configuration files")
        return "inconsistent", None
    if not zshrc.exists():
        return "missing", None
    if not zshrc.is_file():
        issues.append(f"{zshrc} is not a regular file")
        return "inconsistent", None
    try:
        current = zshrc.read_bytes()
    except OSError as exc:
        issues.append(f"cannot read {zshrc}: {exc}")
        return "inconsistent", None

    if installation != INSTALLED:
        return "ok", None

    try:
        reference = load_reference(layout, current)
    except ShlibError as exc:
        issues.append(str(exc))
        return "drift", "other changes"

    try:
        tail = split_tail(current, reference.data)
    except ShlibError:
        if BOOTSTRAP_BEGIN.encode() not in current:
            return "drift", "managed block missing"
        return "drift", "other changes"

    if not tail:
        return "ok", None
    if not tail.strip():
        return "drift", "whitespace only"
    return "drift", f"{_line_count(tail)} appended line(s)"


def _count_exports(layout: ShlibLayout, issues: list[str]) -> tuple[int, int]:
    """Count export files and owner-only files by metadata only."""
    directory = layout.exports
    if directory.is_symlink():
        issues.append(f"{directory} is a symlink; shlib does not support symlinked export directories")
        return 0, 0
    if not directory.exists():
        return 0, 0
    if not directory.is_dir():
        issues.append(f"{directory} is not a directory")
        return 0, 0
    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        issues.append(f"cannot list {directory}: {exc}")
        return 0, 0

    exports = 0
    owner_only = 0
    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            if not entry.is_file():
                continue
            mode = stat.S_IMODE(entry.stat().st_mode)
        except OSError as exc:
            issues.append(f"cannot inspect an export file in {directory}: {exc}")
            continue
        exports += 1
        if mode & 0o077 == 0:
            owner_only += 1
    return exports, owner_only


def _count_shlibs(layout: ShlibLayout, issues: list[str]) -> int:
    """Count the regular files the bootstrap sources."""
    directory = layout.shlibs
    if directory.is_symlink() or not directory.is_dir():
        return 0
    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        issues.append(f"cannot list {directory}: {exc}")
        return 0

    count = 0
    for entry in entries:
        name = entry.name
        if len(name) < 2 or not name[:2].isascii() or not name[:2].isdigit():
            continue
        try:
            if entry.is_file():
                count += 1
        except OSError as exc:
            issues.append(f"cannot inspect a shlib file in {directory}: {exc}")
    return count


def _unsorted_state(layout: ShlibLayout, issues: list[str]) -> tuple[str, bool]:
    """Return whether ``99-unsorted.sh`` exists and its human-readable size."""
    target = layout.shlibs / UNSORTED_NAME
    if not target.exists() and not target.is_symlink():
        return "no", False
    if target.is_symlink():
        issues.append(f"{target} is a symlink; shlib sources only regular files")
        return "yes", True
    if target.is_dir():
        issues.append(f"{target} is a directory")
        return "yes", True
    try:
        size = target.stat().st_size
    except OSError as exc:
        issues.append(f"cannot stat {target}: {exc}")
        return "yes", True
    if size == 0:
        return "yes (empty)", True
    return f"yes ({size} bytes)", True


def _count_backups(layout: ShlibLayout, issues: list[str]) -> int:
    """Count ``.shlib.uninstalled-*`` directories in the home."""
    try:
        entries = list(layout.home.iterdir())
    except OSError as exc:
        issues.append(f"cannot list {layout.home}: {exc}")
        return 0
    return sum(1 for entry in entries if entry.name.startswith(".shlib.uninstalled-") and entry.is_dir())


def _line_count(tail: bytes) -> int:
    """Return the number of lines in ``tail``, including a last line without newline."""
    count = tail.count(b"\n")
    if tail and not tail.endswith(b"\n"):
        count += 1
    return count
