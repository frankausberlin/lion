"""Small output helpers shared by the lion commands."""

from __future__ import annotations

import hashlib
from pathlib import Path


def display_path(path: Path, home: Path) -> str:
    """Return ``path`` abbreviated with ``~`` when it is below ``home``.

    Args:
        path: Path to display.
        home: Home directory used for abbreviation.

    Returns:
        A user-facing path string.
    """
    try:
        relative = path.relative_to(home)
    except ValueError:
        return str(path)
    if str(relative) == ".":
        return "~"
    return f"~/{relative}"


def display_text(text: str, home: Path) -> str:
    """Abbreviate occurrences of ``home`` inside a message.

    Args:
        text: Message to abbreviate.
        home: Home directory used for abbreviation.

    Returns:
        The message with ``home`` replaced by ``~``.
    """
    return text.replace(str(home), "~")


def short_hash(data: bytes) -> str:
    """Return the short SHA-256 form used in user-facing output.

    Args:
        data: Bytes to hash.

    Returns:
        The first 12 hexadecimal characters of the SHA-256 digest.
    """
    return hashlib.sha256(data).hexdigest()[:12]
