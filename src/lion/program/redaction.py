"""Best-effort secret redaction for the ``serve`` read surface.

The zsh integration already masks likely secrets before they reach a recording
(see :mod:`lion.program.shell`); this module mirrors that mask in Python for
everything the MCP layer hands out: recorded command and comment texts, notes,
ADR field values, search hits and classifier previews. The recorded bytes stay
untouched - this is a display filter, never a security boundary.

The trigger gate mirrors the zsh mask exactly: a text is only processed when
its lowercased form contains one of :data:`KEYWORDS`. Then ``NAME=VALUE``
assignments whose name carries a secret keyword and ``Authorization:`` headers
are replaced with ``***``. A mapped field (an ADR field) whose *name* carries a
keyword is masked as a whole, the mapping form of the same rule. Set
``LION_SERVE_REDACT=0`` to disable the filter.
"""

import os
import re
from collections.abc import Mapping

__all__ = [
    "DISABLE_ENV",
    "KEYWORDS",
    "REDACTED",
    "enabled",
    "redact",
    "redact_field",
    "redact_fields",
]

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

# The lookbehind keeps a failed match from restarting the greedy name run at
# every offset of a long alphanumeric token (that made the scan quadratic).
_ASSIGNMENT = re.compile(rf"(?i)(?<![A-Za-z0-9_])({_NAME})=({_VALUE})")
_AUTHORIZATION = re.compile(r"(?i)(Authorization:)[ \t]*(?:(?:Basic|Bearer|Token)[ \t]*)?\S+")


def enabled() -> bool:
    """Return whether redaction is active; the default is on."""
    return os.environ.get(DISABLE_ENV, "").strip() != "0"


def _has_keyword(text: str) -> bool:
    """Return whether the lowercased text carries a trigger keyword."""
    lowered = text.lower()
    return any(keyword in lowered for keyword in KEYWORDS)


def redact(text: str) -> str:
    """Return ``text`` with likely secrets masked, best-effort.

    Without a trigger keyword the text is returned unchanged, exactly like the
    zsh mask; with one, ``NAME=VALUE`` assignments (bare, single- or
    double-quoted) and ``Authorization:`` header values become ``***``.
    """
    if not enabled():
        return text
    if not _has_keyword(text):
        return text
    masked = _ASSIGNMENT.sub(rf"\1={REDACTED}", text)
    return _AUTHORIZATION.sub(rf"\1 {REDACTED}", masked)


def redact_field(name: str, value: str) -> str:
    """Return one mapped value masked: a keyword-bearing name masks it whole.

    This is the mapping form of the ``NAME=VALUE`` rule: an ADR field named
    ``api_key``, ``password``, ... must not depend on its value carrying a
    keyword itself.
    """
    if enabled() and _has_keyword(name):
        return REDACTED
    return redact(value)


def redact_fields(fields: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of a field mapping with every value redacted."""
    return {name: redact_field(name, value) for name, value in fields.items()}
