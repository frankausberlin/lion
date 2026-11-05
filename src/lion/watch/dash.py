"""Dash source for ``lion watch``."""

from __future__ import annotations

import difflib
import stat
from pathlib import Path

from lion.output import display_path, short_hash
from lion.shlib.layout import ShlibLayout
from lion.watch.errors import WatchError
from lion.watch.model import WatchFinding

SOURCE = "dash"


def collect_dash_findings(layout: ShlibLayout) -> tuple[WatchFinding, ...]:
    """Compare locked dash entries against their lock copies.

    A missing dash or lock directory is silent. Symlinked or unreadable
    directories raise an error. A lock copy that is not a regular file is
    reported as an error finding and never followed.

    Args:
        layout: Paths below the selected home directory.

    Returns:
        Findings sorted by dash entry name.

    Raises:
        WatchError: If the dash directory is symlinked, not a directory or
            cannot be listed, or if the lock directory is symlinked or not a
            directory.
    """
    dash = layout.dash
    locks = layout.locks
    if not _ensure_directory(dash) or not _ensure_directory(locks):
        return ()

    try:
        children = list(dash.iterdir())
    except OSError as exc:
        raise WatchError(f"cannot list {dash}: {exc}") from exc

    findings: list[WatchFinding] = []
    for child in sorted(children, key=lambda path: path.name):
        if child.name.startswith("."):
            continue
        lock = layout.lock_for(child.name)
        if not _exists(lock):
            continue
        finding = _check_entry(layout, child.name, child, lock)
        if finding is not None:
            findings.append(finding)
    return tuple(findings)


def _ensure_directory(path: Path) -> bool:
    """Return whether a real directory exists, raising for a broken setup."""
    if path.is_symlink():
        raise WatchError(f"{path} is a symlink; lion watch does not follow it")
    if not path.exists():
        return False
    if not path.is_dir():
        raise WatchError(f"{path} is not a directory")
    return True


def _check_entry(layout: ShlibLayout, name: str, entry: Path, lock: Path) -> WatchFinding | None:
    """Return the finding for one locked dash entry, if any."""
    home = layout.home
    if lock.is_symlink() or not lock.is_file():
        return WatchFinding(
            source=SOURCE,
            name=name,
            lines=(f"dash: {name}: lock is not a regular file: {display_path(lock, home)}",),
            is_error=True,
        )

    if not entry.is_symlink():
        return WatchFinding(source=SOURCE, name=name, lines=(f"dash: {name}: not a symlink",))

    target = _link_target(entry)
    if target is None:
        return _read_error(name, f"cannot read link {_show(entry, home)}")
    try:
        status = entry.stat()
    except FileNotFoundError:
        return WatchFinding(source=SOURCE, name=name, lines=(f"dash: {name}: broken link: {_show(target, home)}",))
    except OSError as exc:
        return _read_error(name, f"cannot inspect {_show(target, home)}: {exc}")

    if not stat.S_ISREG(status.st_mode):
        return WatchFinding(
            source=SOURCE,
            name=name,
            lines=(f"dash: {name}: target is not a regular file: {_show(target, home)}",),
        )

    try:
        lock_data = lock.read_bytes()
    except OSError as exc:
        return _read_error(name, f"cannot read lock {_show(lock, home)}: {exc}")
    try:
        target_data = entry.read_bytes()
    except FileNotFoundError:
        return WatchFinding(
            source=SOURCE,
            name=name,
            lines=(f"dash: {name}: target is missing: {_show(target, home)}",),
        )
    except OSError as exc:
        return _read_error(name, f"cannot read target {_show(target, home)}: {exc}")

    if lock_data == target_data:
        return None

    try:
        lock_text = _decode(lock_data)
        target_text = _decode(target_data)
    except UnicodeDecodeError:
        return WatchFinding(
            source=SOURCE,
            name=name,
            lines=(
                f"dash: {name}: binary content differs "
                f"(sha256 lock={short_hash(lock_data)} target={short_hash(target_data)})",
            ),
        )

    diff = difflib.unified_diff(
        lock_text.splitlines(keepends=True),
        target_text.splitlines(keepends=True),
        fromfile=f"lock: {display_path(lock, home)}",
        tofile=f"target: {display_path(entry.resolve(), home)}",
    )
    lines = [f"dash: {name}: changed"]
    lines.extend(_without_newline(line) for line in diff)
    return WatchFinding(source=SOURCE, name=name, lines=tuple(lines))


def _read_error(name: str, reason: str) -> WatchFinding:
    """Return an error finding for a read problem."""
    return WatchFinding(
        source=SOURCE,
        name=name,
        lines=(f"dash: {name}: cannot read lock/target: {reason}",),
        is_error=True,
    )


def _decode(data: bytes) -> str:
    """Decode text content, treating NUL bytes as binary."""
    if b"\x00" in data:
        raise UnicodeDecodeError("utf-8", data, 0, 1, "NUL byte")
    return data.decode("utf-8")


def _without_newline(line: str) -> str:
    """Remove only a trailing newline from a diff line."""
    return line[:-1] if line.endswith("\n") else line


def _show(path: Path, home: Path) -> str:
    """Return a display path for a resolved or raw target."""
    return display_path(path, home)


def _link_target(path: Path) -> Path | None:
    """Return the immediate link target, or ``None`` if the link is unreadable."""
    try:
        raw = path.readlink()
    except OSError:
        return None
    if raw.is_absolute():
        return raw
    return path.parent / raw


def _exists(path: Path) -> bool:
    """Return whether a path exists, including a dangling symlink."""
    return path.exists() or path.is_symlink()
