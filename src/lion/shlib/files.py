"""Filesystem helpers shared by the lion commands."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def exists(path: Path) -> bool:
    """Return whether a path exists, including a dangling symlink.

    Args:
        path: Path to inspect.

    Returns:
        ``True`` for an existing path or a symlink, even when its target is gone.
    """
    return path.exists() or path.is_symlink()


def link_target(path: Path) -> Path:
    """Return the immediate link target, made absolute for relative links.

    Args:
        path: Symlink to read.

    Returns:
        The immediate target, with a relative target resolved against
        ``path.parent``.

    Raises:
        OSError: If the link cannot be read.
    """
    raw = path.readlink()
    if raw.is_absolute():
        return raw
    return path.parent / raw


def line_count(data: bytes) -> int:
    """Return the number of lines in ``data``.

    Args:
        data: Bytes to count.

    Returns:
        The line count, including a final line without a trailing newline.
    """
    count = data.count(b"\n")
    if data and not data.endswith(b"\n"):
        count += 1
    return count


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    """Write ``data`` to ``path`` atomically with an explicit mode.

    The data is written to a temporary file in the target directory, flushed,
    given ``mode`` and then moved into place with :func:`os.replace`. A failed
    write removes the temporary file and leaves an existing target untouched.

    Args:
        path: Target file.
        data: Exact bytes to write.
        mode: Permission bits for the finished file.

    Raises:
        OSError: If the temporary file or the replacement cannot be written.
    """
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
