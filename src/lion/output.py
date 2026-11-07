"""Small output helpers shared by the lion commands."""

from __future__ import annotations

import hashlib
import os
import sys
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


def short_digest(digest: str) -> str:
    """Return the short hex form used in user-facing output.

    Args:
        digest: Full hexadecimal digest.

    Returns:
        The first 12 hexadecimal characters, without an ellipsis.
    """
    return digest[:12]


def short_hash(data: bytes) -> str:
    """Return the short SHA-256 form used in user-facing output.

    Args:
        data: Bytes to hash.

    Returns:
        The first 12 hexadecimal characters of the SHA-256 digest.
    """
    return short_digest(hashlib.sha256(data).hexdigest())


def color_enabled(no_color: bool) -> bool:
    """Return whether ANSI colors should be used.

    Args:
        no_color: Value of the ``--no-color`` option.

    Returns:
        ``True`` only for a TTY without ``NO_COLOR`` and without ``--no-color``.
    """
    return not no_color and "NO_COLOR" not in os.environ and sys.stdout.isatty()
