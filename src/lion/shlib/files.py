"""Small filesystem helpers for the shlib commands."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


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
