"""Atomic replacement writes shared by the config and the guard artifact.

One implementation keeps mode, fsync and temporary-file handling in a single
place; the link-based publishing flows (recordings, ADRs) stay with their own
``stage_text`` because they never replace an existing file.
"""

import os
import tempfile
from pathlib import Path

__all__ = ["write_text_atomic"]


def write_text_atomic(path: Path, text: str) -> Path:
    """Write ``text`` to ``path`` atomically (mode ``600``), replacing it.

    The parent directories are created on demand; the text is written and
    fsynced to a temporary file in the same directory and then moved into
    place with :func:`os.replace`, so a reader never sees a partial file and no
    temporary file remains.

    Args:
        path: The destination file.
        text: The complete UTF-8 content.

    Returns:
        The path of the written file.

    Raises:
        OSError: If the directory or the file cannot be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            _ = stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
