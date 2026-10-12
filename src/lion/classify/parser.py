"""Bounded shell parsing for the classifier.

The parser decides whether a recorded input text stays inside a small,
unambiguous subset of shell syntax and, if so, splits it into simple command
segments. Anything outside that subset is reported as unsupported with a stable
reason code, so the classifier reports no matches instead of guessing.
Balanced quotes, plain redirections and the top-level separators ``&&``,
``||``, ``;``, ``|`` and the end of line are supported because they are
deterministic and common; escaping, subshells and grouping, command and process
substitution, here-documents and here-strings, control flow, function
definitions and background jobs are not.

Segments anchor segment-scope rules after :func:`strip_prefixes` removes
leading ``sudo`` prefixes and ``NAME=value`` assignments, so printed text such
as ``echo "sudo nala install"`` never matches an anchored pattern.
"""

import re
from dataclasses import dataclass

__all__ = ["PARSER_VERSION", "ParsedCommand", "find_closing_quote", "parse_command", "strip_prefixes"]

#: Stable identifier of the parser and matching contract, stored with every
#: classification.
PARSER_VERSION = "classify/1"

_CONTROL_KEYWORDS = frozenset(
    {
        "if",
        "then",
        "elif",
        "else",
        "fi",
        "for",
        "while",
        "until",
        "do",
        "done",
        "case",
        "esac",
        "select",
        "coproc",
        "function",
    }
)
_FUNCTION_DEF = re.compile(r"^(?:function\s+)?[A-Za-z_][A-Za-z0-9_]*\s*\(\s*\)")
_SUDO_PREFIX = re.compile(r"^sudo(?:\s+-\S+)*\s+")
_ASSIGNMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


@dataclass(frozen=True)
class ParsedCommand:
    """The supported/unsupported verdict plus the simple segments of a text.

    Attributes:
        supported: Whether the text is inside the supported syntax subset.
        segments: Top-level simple command segments (empty when unsupported).
        reason: Stable machine code describing why the text is unsupported.
    """

    supported: bool
    segments: tuple[str, ...] = ()
    reason: str | None = None


def _unsupported(reason: str) -> ParsedCommand:
    return ParsedCommand(supported=False, segments=(), reason=reason)


def _flush(segments: list[str], buffer: list[str]) -> ParsedCommand | None:
    """Flush one pending segment; an operator without an operand is ambiguous."""
    text = "".join(buffer).strip()
    if not text:
        return _unsupported("ambiguous_separator")
    segments.append(text)
    buffer.clear()
    return None


def parse_command(command: str) -> ParsedCommand:
    """Parse one input text into conservative simple segments.

    Supported syntax keeps balanced quotes (single and double) and top-level
    ``&&``/``||``/``;``/``|``/end-of-line separators. A newline with no pending
    segment is insignificant, so an operator carried across a pasted line
    (``… &&`` + newline), a blank line and a trailing ``;`` stay supported,
    while a genuinely missing operand (``a && && b``) is
    ``ambiguous_separator``. Every construct that could change how the input is
    interpreted - unbalanced quotes, escaping, subshells, grouping, command and
    process substitution, here-documents and here-strings, control flow,
    function definitions and background jobs - yields ``supported=False`` with
    a stable reason code.
    """
    text = command.strip()
    if not text:
        return _unsupported("empty")

    segments: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if quote is not None:
            buffer.append(char)
            if quote == "'":
                if char == "'":
                    quote = None
            elif char == "\\" and index + 1 < length:
                buffer.append(text[index + 1])
                index += 2
                continue
            elif char == "`" or (char == "$" and index + 1 < length and text[index + 1] == "("):
                return _unsupported("command_substitution")
            elif char == '"':
                quote = None
            index += 1
            continue

        nxt = text[index + 1] if index + 1 < length else ""
        if char == "\\":
            return _unsupported("escaping")
        if char in ("'", '"'):
            quote = char
            buffer.append(char)
            index += 1
            continue
        if char == "`":
            return _unsupported("command_substitution")
        if char == "$" and nxt == "(":
            return _unsupported("command_substitution")
        if char in "<>" and nxt == "(":
            return _unsupported("process_substitution")
        if char == "<" and nxt == "<":
            return _unsupported("here_document")
        if char in "()":
            return _unsupported("subshell")
        if char == "{" and (index == 0 or text[index - 1].isspace()) and (nxt == "" or nxt.isspace()):
            return _unsupported("group")
        if char == "}" and index > 0 and text[index - 1].isspace():
            return _unsupported("group")
        if char == "&":
            if nxt == "&":
                flushed = _flush(segments, buffer)
                if flushed is not None:
                    return flushed
                index += 2
                continue
            return _unsupported("background_job")
        if char == "|":
            flushed = _flush(segments, buffer)
            if flushed is not None:
                return flushed
            index += 2 if nxt == "|" else 1
            continue
        if char in ";\n":
            if char == "\n" and not "".join(buffer).strip():
                buffer.clear()
                index += 1
                continue
            flushed = _flush(segments, buffer)
            if flushed is not None:
                return flushed
            index += 1
            continue
        buffer.append(char)
        index += 1

    if quote is not None:
        return _unsupported("unbalanced_quote")
    if "".join(buffer).strip():
        flushed = _flush(segments, buffer)
        if flushed is not None:
            return flushed
    if not segments:
        return _unsupported("empty")

    for segment in segments:
        first_token = segment.split(maxsplit=1)[0]
        if first_token in _CONTROL_KEYWORDS or _FUNCTION_DEF.match(segment):
            return _unsupported("control_flow")
    return ParsedCommand(supported=True, segments=tuple(segments), reason=None)


def find_closing_quote(text: str, start: int, quote: str, escapes: bool | None = None) -> int:
    """Return the index of the closing quote, or ``-1``.

    Args:
        text: The text to scan.
        start: The index to start scanning at.
        quote: The quote character to find.
        escapes: Whether backslash escapes apply inside the quote; defaults to
            true for a double quote and false otherwise.
    """
    if escapes is None:
        escapes = quote == '"'
    index = start
    while index < len(text):
        char = text[index]
        if escapes and char == "\\":
            index += 2
            continue
        if char == quote:
            return index
        index += 1
    return -1


def _word_end(text: str, start: int) -> int | None:
    """Return the index after the shell word starting at ``start``.

    Quotes and backslash escapes inside the word are consumed; ``None`` marks a
    word that cannot be resolved statically (an open quote or trailing escape).
    """
    index = start
    while index < len(text):
        char = text[index]
        if char.isspace():
            return index
        if char == "\\":
            index += 2
            continue
        if char in ("'", '"'):
            end = find_closing_quote(text, index + 1, char)
            if end < 0:
                return None
            index = end + 1
            continue
        index += 1
    return index


def _assignment_end(text: str) -> int | None:
    """Return the index after one leading ``NAME=value`` word, or ``None``."""
    match = _ASSIGNMENT_NAME.match(text)
    if match is None:
        return None
    return _word_end(text, match.end())


def strip_prefixes(segment: str) -> str:
    """Strip leading ``sudo`` prefixes and ``NAME=value`` assignments.

    Repeated prefixes are removed until the command word itself is at the
    start, so segment-scope rules anchor on the command and never on a wrapper.
    A prefix that cannot be resolved statically is left in place.
    """
    text = segment.strip()
    while text:
        sudo = _SUDO_PREFIX.match(text)
        if sudo is not None:
            text = text[sudo.end() :].lstrip()
            continue
        end = _assignment_end(text)
        if end is None:
            return text
        text = text[end:].lstrip()
    return text
