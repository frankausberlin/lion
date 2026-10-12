r"""Text hygiene for terminal-facing classifier output.

Recorded shell text and user rule files can carry control characters (``ESC``,
``BEL``, ``\x9b``), and ``lion classify``/``watch R`` print excerpts of them.
Every text surface that reaches a terminal passes through
:func:`sanitize_controls` first, so a recording from an untrusted source cannot
move the cursor, retitle the terminal or write the clipboard.
"""

import re

__all__ = ["sanitize_controls"]

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def sanitize_controls(text: str) -> str:
    """Return ``text`` without C0/C1 control characters."""
    return _CONTROL_CHARS.sub("", text)


def has_controls(text: str) -> bool:
    """Return whether ``text`` contains C0/C1 control characters."""
    return _CONTROL_CHARS.search(text) is not None
