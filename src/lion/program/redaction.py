"""Best-effort secret redaction for the ``serve`` read surface.

The zsh integration already masks likely secrets before they reach a recording
(see :mod:`lion.program.shell`); this module mirrors that mask in Python for
everything the MCP layer hands out: recorded command and comment texts, notes,
ADR field values, search hits and classifier previews. The recorded bytes stay
untouched - this is a display filter, never a security boundary.

The trigger gate mirrors the zsh mask exactly: a text is only processed when
its lowercased form contains one of :data:`KEYWORDS`. Then ``NAME=VALUE``
assignments whose name carries a secret keyword and ``Authorization:`` headers
are replaced with ``***``. Set ``LION_SERVE_REDACT=0`` to disable the filter.
"""

import os
import re
from collections.abc import Mapping

__all__ = ["DISABLE_ENV", "KEYWORDS", "REDACTED", "enabled", "redact", "redact_fields"]

#: The replacement written over a masked secret.
REDACTED = "***"

#: The environment variable that turns the filter off (``0``).
DISABLE_ENV = "LION_SERVE_REDACT"

#: The lowercase trigger keywords, mirroring the zsh mask gate.
KEYWORDS: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "credential",
    "authorization",
)

_NAME = r"[A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|CREDENTIAL)[A-Za-z0-9_]*"
_VALUE = r"""(?:"(?:[^"\\]|\\.)*"|'[^']*'|[^\s;&|]*)"""

_ASSIGNMENT = re.compile(rf"(?i)({_NAME})=({_VALUE})")
_AUTHORIZATION = re.compile(r"(?i)(Authorization:)[ \t]*(?:(?:Basic|Bearer|Token)[ \t]*)?\S+")


def enabled() -> bool:
    """Return whether redaction is active; the default is on."""
    return os.environ.get(DISABLE_ENV, "").strip() != "0"


def redact(text: str) -> str:
    """Return ``text`` with likely secrets masked, best-effort.

    Without a trigger keyword the text is returned unchanged, exactly like the
    zsh mask; with one, ``NAME=VALUE`` assignments (bare, single- or
    double-quoted) and ``Authorization:`` header values become ``***``.
    """
    if not enabled():
        return text
    lowered = text.lower()
    if not any(keyword in lowered for keyword in KEYWORDS):
        return text
    masked = _ASSIGNMENT.sub(rf"\1={REDACTED}", text)
    return _AUTHORIZATION.sub(rf"\1 {REDACTED}", masked)


def redact_fields(fields: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of a field mapping with every value redacted."""
    return {name: redact(value) for name, value in fields.items()}
