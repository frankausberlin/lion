"""Read and manage dash entries and their lock copies."""

from __future__ import annotations

import hashlib
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from lion.dash.errors import DashError
from lion.shlib.files import atomic_write
from lion.shlib.layout import ShlibLayout

KIND_FILE = "file"
KIND_DIR = "dir"
KIND_BROKEN = "broken"
KIND_OTHER = "other"
KIND_NOT_A_LINK = "not-a-link"

LOCK_MODE = 0o600


@dataclass(frozen=True, slots=True)
class DashEntry:
    """One direct child of the dash directory.

    Attributes:
        name: Direct child name.
        path: Path of the dash entry.
        target: Resolved link target, or ``None`` for a non-link.
        kind: ``file``, ``dir``, ``broken``, ``other`` or ``not-a-link``.
        locked: Whether ``locks/<name>.lock`` exists.
    """

    name: str
    path: Path
    target: Path | None
    kind: str
    locked: bool


@dataclass(frozen=True, slots=True)
class LockResult:
    """Result of writing a lock copy.

    Attributes:
        name: Dash entry name.
        target: File that was read.
        size: Number of bytes copied.
        sha256: Full SHA-256 digest of the copied bytes.
        updated: Whether an existing lock file was overwritten.
    """

    name: str
    target: Path
    size: int
    sha256: str
    updated: bool


def list_entries(layout: ShlibLayout) -> tuple[DashEntry, ...]:
    """List direct, non-hidden dash entries with metadata only.

    Link targets and locks are classified by ``lstat``/``stat``/``readlink``;
    the contents of targets or locks are never read.

    Args:
        layout: Paths below the selected home directory.

    Returns:
        Entries sorted lexicographically by link name.

    Raises:
        DashError: If the dash directory is missing, symlinked or unreadable.
    """
    dash = layout.dash
    _require_directory(dash, hint="run `lion shlib install`")

    try:
        children = list(dash.iterdir())
    except OSError as exc:
        raise DashError(f"cannot list {dash}: {exc}") from exc

    entries: list[DashEntry] = []
    for child in children:
        if child.name.startswith("."):
            continue
        kind, target = _classify(child)
        entries.append(
            DashEntry(
                name=child.name,
                path=child,
                target=target,
                kind=kind,
                locked=_exists(layout.lock_for(child.name)),
            )
        )
    return tuple(sorted(entries, key=lambda entry: entry.name))


def lockable_entry_names(layout: ShlibLayout) -> tuple[str, ...]:
    """Return direct dash entry names whose target is a regular file.

    Args:
        layout: Paths below the selected home directory.

    Returns:
        Sorted names that ``lock_entry`` accepts.

    Raises:
        DashError: If the dash directory is missing, symlinked or unreadable.
    """
    return tuple(entry.name for entry in list_entries(layout) if entry.kind == KIND_FILE)


def lock_entry(layout: ShlibLayout, name: str) -> LockResult:
    """Write the current content of one dash entry to its lock copy.

    An existing regular lock file is always overwritten. Directories and
    symlinks at the lock path are refused.

    Args:
        layout: Paths below the selected home directory.
        name: Direct, non-hidden dash entry name.

    Returns:
        The lock result with byte count and digest.

    Raises:
        DashError: If the name, dash entry, target or lock directory is not
            suitable for locking.
    """
    validate_name(name)
    dash = layout.dash
    _require_directory(dash, hint="run `lion shlib install`")

    entry = dash / name
    if not entry.is_symlink():
        if not entry.exists():
            raise DashError(f"dash entry does not exist: {entry}")
        raise DashError(f"dash entry is not a symlink: {entry}")

    target = _link_target(entry)
    try:
        status = entry.stat()
    except FileNotFoundError as exc:
        raise DashError(f"dash entry is a broken link: {entry} -> {target}") from exc
    except OSError as exc:
        raise DashError(f"cannot inspect {entry}: {exc}") from exc
    if not stat.S_ISREG(status.st_mode):
        raise DashError(f"dash entry does not resolve to a regular file: {entry} -> {target}")

    try:
        target = entry.resolve()
        data = entry.read_bytes()
    except OSError as exc:
        raise DashError(f"cannot read {target}: {exc}") from exc

    _require_lock_directory(layout)
    lock = layout.lock_for(name)
    if lock.is_symlink():
        raise DashError(f"lock path is a symlink: {lock}")
    if lock.exists() and not lock.is_file():
        raise DashError(f"lock path is not a regular file: {lock}")
    updated = lock.exists()

    try:
        atomic_write(lock, data, LOCK_MODE)
    except OSError as exc:
        raise DashError(f"cannot write {lock}: {exc}") from exc

    return LockResult(
        name=name,
        target=target,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        updated=updated,
    )


def unlock_entry(layout: ShlibLayout, name: str) -> bool:
    """Remove only the lock copy of a dash entry.

    The dash link and its target stay untouched. A missing lock directory or
    lock file is a no-op. A symlink at the lock path is removed as well.

    Args:
        layout: Paths below the selected home directory.
        name: Direct, non-hidden dash entry name.

    Returns:
        Whether a lock entry was removed.

    Raises:
        DashError: If the lock path is a directory or cannot be removed.
    """
    validate_name(name)
    locks = layout.locks
    if locks.is_symlink():
        raise DashError(f"lock directory is a symlink: {locks}")
    if not locks.exists():
        return False
    if not locks.is_dir():
        raise DashError(f"lock path is not a directory: {locks}")

    lock = layout.lock_for(name)
    if lock.is_symlink():
        try:
            lock.unlink()
        except OSError as exc:
            raise DashError(f"cannot remove {lock}: {exc}") from exc
        return True
    if not lock.exists():
        return False
    if lock.is_dir():
        raise DashError(f"lock path is a directory: {lock}")

    try:
        lock.unlink()
    except OSError as exc:
        raise DashError(f"cannot remove {lock}: {exc}") from exc
    return True


def validate_name(name: str) -> None:
    """Validate a direct, non-hidden dash entry name.

    Args:
        name: Candidate name from the command line.

    Raises:
        DashError: If the name is empty, hidden, contains a slash or control
            characters.
    """
    if not name:
        raise DashError("dash entry name must not be empty")
    if name.startswith("."):
        raise DashError(f"dash entry name must not be hidden: {name}")
    if "/" in name:
        raise DashError(f"dash entry name must be a direct child name: {name}")
    if any(unicodedata.category(character).startswith("C") for character in name):
        raise DashError(f"dash entry name must not contain control characters: {name!r}")


def _require_directory(path: Path, *, hint: str) -> None:
    """Require an existing, real directory."""
    if path.is_symlink():
        raise DashError(f"{path} is a symlink; shlib does not support symlinked directories")
    if not path.exists():
        raise DashError(f"{path} does not exist; {hint}")
    if not path.is_dir():
        raise DashError(f"{path} is not a directory")


def _require_lock_directory(layout: ShlibLayout) -> None:
    """Require the real lock directory used by dash locks."""
    _require_directory(layout.locks, hint="run `lion shlib install`")


def _classify(path: Path) -> tuple[str, Path | None]:
    """Return the dash kind and link target for one child."""
    if not path.is_symlink():
        return KIND_NOT_A_LINK, None

    target = _link_target(path)
    try:
        status = path.stat()
    except FileNotFoundError:
        return KIND_BROKEN, target
    except OSError:
        return KIND_OTHER, target
    if stat.S_ISREG(status.st_mode):
        return KIND_FILE, target
    if stat.S_ISDIR(status.st_mode):
        return KIND_DIR, target
    return KIND_OTHER, target


def _link_target(path: Path) -> Path:
    """Return the immediate link target, made absolute for relative links."""
    try:
        raw = path.readlink()
    except OSError as exc:
        raise DashError(f"cannot read link {path}: {exc}") from exc
    if raw.is_absolute():
        return raw
    return path.parent / raw


def _exists(path: Path) -> bool:
    """Return whether a path exists, including a dangling symlink."""
    return path.exists() or path.is_symlink()
