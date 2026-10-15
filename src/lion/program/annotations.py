"""ADR annotations: the flat ``#-LION:`` / ``#+LION:`` / ``#.LION:`` grammar.

A recording keeps raw shell input as events; annotations are derived from those
events on every read, so raw events stay untouched and there is no second event
writer. This module owns the bounded zsh mini-parser, the fold that turns marker
lines into at most one :class:`Adr` per record, and the resolution of
``uses``/``supersedes`` references.

The grammar is deliberately small (ADR 0013): a marker line is a full comment
line outside strings and here-documents; its payload is either a typed
``adr.<name>`` line or, for ``#.LION:``, continuation prose. Parsing never
raises: unclear input becomes a visible :class:`AdrIssue`, never a guessed
annotation.
"""

import getpass
import os
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import tomli_w

from lion.program import recording
from lion.program.checks import CheckStatus, DoctorContext, Finding
from lion.program.recording import Recording, SpoolEvent

__all__ = [
    "CATALOG",
    "OVERLAY_FILENAME",
    "OVERLAY_SCHEMA_VERSION",
    "STATUS_ACCEPTED",
    "STATUS_PROPOSED",
    "STATUS_SUPERSEDED",
    "STATUS_VALUES",
    "Adr",
    "AdrAction",
    "AdrAnchor",
    "AdrIssue",
    "AdrOverlay",
    "AnnotationError",
    "ParsedEvent",
    "RecordAnnotations",
    "ResolvedRef",
    "adr_for",
    "adrs_for",
    "annotations_checks",
    "annotations_for",
    "fold_events",
    "is_complete",
    "is_known_status",
    "load_overlay",
    "order_fields",
    "overlay_template",
    "parse_event_text",
    "parse_overlay_document",
    "parse_ref_list",
    "resolve_ref_list",
    "write_overlay",
]

#: The three annotation markers; the key is the marker character used in actions.
MARKER_PREVIOUS = "#-LION:"
MARKER_NEXT = "#+LION:"
MARKER_DITTO = "#.LION:"

#: The ADR field catalog; additional fields are allowed after these.
CATALOG: tuple[str, ...] = (
    "title",
    "decision",
    "component",
    "scope",
    "context",
    "consequences",
    "alternatives",
    "revisit_when",
    "uses",
    "supersedes",
    "status",
)

STATUS_PROPOSED = "proposed"
STATUS_ACCEPTED = "accepted"
STATUS_SUPERSEDED = "superseded"

#: The recognized ``adr.status`` values; any other value stays verbatim and is
#: reported as an issue.
STATUS_VALUES: tuple[str, ...] = (STATUS_PROPOSED, STATUS_ACCEPTED, STATUS_SUPERSEDED)

REASON_EMPTY_LINE = "empty annotation line"
REASON_UNKNOWN_FORM = "unknown annotation form"
REASON_ADR_VALUE = "adr takes no value"
REASON_EMPTY_FIELD = "empty field name"
REASON_NO_TARGET = "continuation without a field"
REASON_UNKNOWN_STATUS = "unknown status value"
REASON_UNRESOLVED = "unresolved construct"

#: The overlay file that ``records edit`` writes inside a record directory.
OVERLAY_FILENAME = "adr.toml"
#: The only overlay schema version the reader accepts.
OVERLAY_SCHEMA_VERSION = 1

_MODE_NORMAL = "normal"
_MODE_SINGLE = "single"
_MODE_DOUBLE = "double"
_MODE_DOLLAR = "dollar"
_MODE_UNKNOWN = "unknown"

#: The opening quote and escape rule of every open-quote mode.
_QUOTE_MODES: dict[str, tuple[str, bool]] = {
    _MODE_SINGLE: ("'", False),
    _MODE_DOUBLE: ('"', True),
    _MODE_DOLLAR: ("'", True),
}


class AnnotationError(ValueError):
    """Annotations cannot be read, validated or mutated."""


@dataclass(frozen=True)
class AdrIssue:
    """One unresolved annotation line, visible but never fatal."""

    reason: str
    marker: str
    segment: str
    line: int
    time: str
    text: str


@dataclass(frozen=True)
class AdrAction:
    """One resolved annotation line: a fold instruction with its source."""

    kind: str
    marker: str
    name: str
    value: str
    line: int
    time: str
    text: str


@dataclass(frozen=True)
class ParsedEvent:
    """The annotation lines of one event text, in line order."""

    actions: tuple[AdrAction, ...]
    issues: tuple[AdrIssue, ...]


@dataclass(frozen=True)
class AdrAnchor:
    """The shell block an ADR is attached to: a ``cmd`` event reference."""

    kind: str
    segment: str
    line: int
    time: str
    text: str


@dataclass(frozen=True)
class ResolvedRef:
    """One ``uses``/``supersedes`` token with its resolved recording ref."""

    raw: str
    resolved: str | None


@dataclass(frozen=True)
class Adr:
    """One folded ADR: effective fields plus derived metadata."""

    ref: str
    fields: Mapping[str, str]
    complete: bool
    status: str
    anchor: AdrAnchor | None
    issues: tuple[AdrIssue, ...]
    first_at: str
    last_at: str
    user: str
    host: str
    cwd: str
    segments: tuple[str, ...]
    edited: bool = False


@dataclass(frozen=True)
class RecordAnnotations:
    """Everything annotation reading yields for one record.

    ``adr`` is ``None`` when the fold ends without any field (including after
    ``adr`` clears); ``issues`` keeps the findings for visibility even then.
    """

    adr: Adr | None
    issues: tuple[AdrIssue, ...]


def _instant(value: float) -> str:
    """Return an event epoch time as an ISO 8601 instant."""
    return datetime.fromtimestamp(value, tz=UTC).isoformat()


def _current_user() -> str:
    """Return the user of the reading process, or ``Unknown``."""
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return "Unknown"


def _marker_line(line: str) -> tuple[str, str] | None:
    """Return ``(marker, rest)`` for a marker line, else ``None``.

    The markers are case-sensitive and must start the line after optional
    whitespace; a marker in the middle of a line is never recognized.
    """
    stripped = line.lstrip()
    if stripped.startswith(MARKER_PREVIOUS):
        return "-", stripped[len(MARKER_PREVIOUS) :]
    if stripped.startswith(MARKER_NEXT):
        return "+", stripped[len(MARKER_NEXT) :]
    if stripped.startswith(MARKER_DITTO):
        return ".", stripped[len(MARKER_DITTO) :]
    return None


def _issue(reason: str, marker: str, line: str, number: int, event: SpoolEvent) -> AdrIssue:
    return AdrIssue(
        reason=reason,
        marker=marker,
        segment=event.segment,
        line=number,
        time=_instant(event.time),
        text=line,
    )


def _annotation_line(
    marker: str,
    rest: str,
    line: str,
    number: int,
    event: SpoolEvent,
) -> AdrAction | AdrIssue:
    """Parse the payload after a marker into an action or an issue."""
    if not rest.strip():
        return _issue(REASON_EMPTY_LINE, marker, line, number, event)
    if not rest[0].isspace():
        return _issue(REASON_UNKNOWN_FORM, marker, line, number, event)
    payload = rest.strip()
    parts = payload.split(maxsplit=1)
    token = parts[0]
    remainder = parts[1] if len(parts) == 2 else ""

    def action(kind: str, name: str = "", value: str = "") -> AdrAction:
        return AdrAction(
            kind=kind,
            marker=marker,
            name=name,
            value=value,
            line=number,
            time=_instant(event.time),
            text=line,
        )

    if token == "adr":
        if remainder:
            return _issue(REASON_ADR_VALUE, marker, line, number, event)
        return action("clear")
    if token.startswith("adr."):
        name = token[len("adr.") :]
        if not name:
            return _issue(REASON_EMPTY_FIELD, marker, line, number, event)
        if not remainder:
            return action("delete", name=name)
        return action("set", name=name, value=remainder)
    if marker == ".":
        return action("continue", value=payload)
    return _issue(REASON_UNKNOWN_FORM, marker, line, number, event)


def _find_closing(line: str, start: int, quote: str, *, escapes: bool) -> int:
    """Return the index of the closing quote on this line, or ``-1``."""
    index = start
    while index < len(line):
        char = line[index]
        if escapes and char == "\\":
            index += 2
            continue
        if char == quote:
            return index
        index += 1
    return -1


def _find_substitution_end(line: str, start: int) -> int:
    """Return the index of the ``)`` closing a ``$(`` opened before ``start``.

    Quotes are tracked inside the substitution so a ``)`` inside a quoted
    string does not close it early. A quote or substitution left open at the
    end of the line returns ``-1`` (unresolved).
    """
    depth = 1
    index = start
    while index < len(line):
        char = line[index]
        if char == "\\":
            index += 2
            continue
        if char == "'":
            end = _find_closing(line, index + 1, "'", escapes=False)
            if end < 0:
                return -1
            index = end + 1
            continue
        if char == '"':
            end = _find_closing(line, index + 1, '"', escapes=True)
            if end < 0:
                return -1
            index = end + 1
            continue
        if char == "$" and index + 1 < len(line) and line[index + 1] == "(":
            depth += 1
            index += 2
            continue
        if char == "(":
            depth += 1
            index += 1
            continue
        if char == ")":
            depth -= 1
            if depth == 0:
                return index
            index += 1
            continue
        index += 1
    return -1


def _parse_heredoc(line: str, start: int) -> tuple[int, tuple[str, bool] | None] | None:
    """Parse a ``<<``/``<<<`` operator at ``start``.

    Returns ``(consumed, pending)`` where ``pending`` is the ``(delimiter,
    strip_tabs)`` pair of a here-document (``None`` for a here-string), or
    ``None`` when the construct cannot be resolved statically.
    """
    if line.startswith("<<<", start):
        return start + 3, None
    index = start + 2
    strip_tabs = index < len(line) and line[index] == "-"
    if strip_tabs:
        index += 1
    while index < len(line) and line[index].isspace():
        index += 1
    if index >= len(line):
        return None
    if line[index] in ";&|<>()#":
        return None
    parsed = _read_delimiter_word(line, index)
    if parsed is None:
        return None
    end, delimiter = parsed
    if not delimiter:
        return None
    return end, (delimiter, strip_tabs)


def _read_delimiter_word(line: str, start: int) -> tuple[int, str] | None:
    """Read a here-document delimiter word with full quote removal.

    The delimiter word undergoes quote removal but no expansion: quotes
    concatenate into one word (``<<'EO'F`` closes on ``EOF``, not ``EO``),
    backslashes escape the next character, and an unquoted ``$`` or backtick
    stays literal. Returns ``(index_after_word, word)``, or ``None`` when a
    quote or a trailing backslash is left open on the line.
    """
    index = start
    parts: list[str] = []
    while index < len(line):
        char = line[index]
        if char.isspace() or char in ";&|<>()":
            break
        if char == "'":
            end = _find_closing(line, index + 1, "'", escapes=False)
            if end < 0:
                return None
            parts.append(line[index + 1 : end])
            index = end + 1
            continue
        if char == '"':
            end = _find_closing(line, index + 1, '"', escapes=True)
            if end < 0:
                return None
            parts.append(_unquote_double(line[index + 1 : end]))
            index = end + 1
            continue
        if char == "\\":
            if index + 1 >= len(line):
                return None
            parts.append(line[index + 1])
            index += 2
            continue
        parts.append(char)
        index += 1
    if index == start:
        return None
    return index, "".join(parts)


def _unquote_double(content: str) -> str:
    r"""Apply double-quote removal to the text between the quotes.

    Inside double quotes a backslash only escapes ``"``, ``\``, ``$`` and a
    backtick; before any other character it stays literal.
    """
    parts: list[str] = []
    index = 0
    while index < len(content):
        char = content[index]
        if char == "\\" and index + 1 < len(content) and content[index + 1] in '"\\$`':
            parts.append(content[index + 1])
            index += 2
            continue
        parts.append(char)
        index += 1
    return "".join(parts)


@dataclass
class _ScanState:
    """The line-by-line scanner state of one event text."""

    mode: str = _MODE_NORMAL
    heredocs: list[tuple[str, bool]] = field(default_factory=list)

    def feed(self, line: str, number: int, event: SpoolEvent) -> AdrAction | AdrIssue | None:
        """Consume one line and return a marker result when there is one."""
        if self.heredocs:
            delimiter, strip_tabs = self.heredocs[0]
            candidate = line.lstrip("\t") if strip_tabs else line
            if candidate == delimiter:
                self.heredocs.pop(0)
                if not self.heredocs:
                    self.mode = _MODE_NORMAL
            return None
        if self.mode in (_MODE_SINGLE, _MODE_DOUBLE, _MODE_DOLLAR):
            self._advance_quote(line)
            return None
        if self.mode == _MODE_UNKNOWN:
            hit = _marker_line(line)
            if hit is not None:
                return _issue(REASON_UNRESOLVED, hit[0], line, number, event)
            return None
        hit = _marker_line(line)
        if hit is not None:
            return _annotation_line(hit[0], hit[1], line, number, event)
        self._scan_normal(line)
        return None

    def _advance_quote(self, line: str) -> None:
        """Leave an open quote when it closes on this line."""
        index = self._skip_quote(line, 0, self.mode)
        if index < 0:
            return
        self.mode = _MODE_NORMAL
        self._scan_normal(line, index)

    def _scan_normal(self, line: str, start: int = 0) -> None:
        """Scan a line outside quotes and update the scanner state."""
        index = start
        while index < len(line):
            char = line[index]
            if char == "\\":
                index += 2
                continue
            if char == "#" and (index == 0 or line[index - 1].isspace() or line[index - 1] in ";|&()<>"):
                return
            if char in ("'", '"', "`", "$", "<"):
                index = self._consume_construct(line, index)
                if index < 0:
                    return
                continue
            index += 1

    def _consume_construct(self, line: str, index: int) -> int:
        """Consume the construct at ``index``; ``-1`` marks an unresolved one."""
        char = line[index]
        if char == "'":
            return self._skip_quote(line, index + 1, _MODE_SINGLE)
        if char == '"':
            return self._skip_quote(line, index + 1, _MODE_DOUBLE)
        if char == "$":
            return self._skip_dollar(line, index)
        if char == "`":
            return self._skip_backtick(line, index + 1)
        if line.startswith("<<", index):
            return self._consume_heredoc(line, index)
        return index + 1

    def _skip_dollar(self, line: str, index: int) -> int:
        """Consume a ``$'...'`` or ``$(...)`` construct, or an ordinary ``$``."""
        if index + 1 < len(line) and line[index + 1] == "'":
            return self._skip_quote(line, index + 2, _MODE_DOLLAR)
        if index + 1 < len(line) and line[index + 1] == "(":
            return self._skip_substitution(line, index + 2)
        return index + 1

    def _skip_quote(self, line: str, start: int, mode: str) -> int:
        """Return the index after the closing quote, or set ``mode`` and return ``-1``."""
        quote, escapes = _QUOTE_MODES[mode]
        end = _find_closing(line, start, quote, escapes=escapes)
        if end < 0:
            self.mode = mode
            return -1
        return end + 1

    def _skip_substitution(self, line: str, start: int) -> int:
        """Return the index after a closed ``$(...)``, or mark the state unknown."""
        end = _find_substitution_end(line, start)
        if end < 0:
            self.mode = _MODE_UNKNOWN
            return -1
        return end + 1

    def _skip_backtick(self, line: str, start: int) -> int:
        """Return the index after a closing backtick on this line, or mark unknown."""
        end = line.find("`", start)
        if end < 0:
            self.mode = _MODE_UNKNOWN
            return -1
        return end + 1

    def _consume_heredoc(self, line: str, index: int) -> int:
        """Consume a ``<<`` operator; ``-1`` when it cannot be resolved statically."""
        parsed = _parse_heredoc(line, index)
        if parsed is None:
            self.mode = _MODE_UNKNOWN
            return -1
        end, pending = parsed
        if pending is not None:
            self.heredocs.append(pending)
        return end


def parse_event_text(event: SpoolEvent) -> ParsedEvent:
    """Parse the annotation lines of one ``cmd`` or ``comment`` event text.

    Marker-like lines inside strings or here-document bodies are text; in an
    unresolved construct (an open ``$(``/backtick or an unparsable operator)
    they are reported as issues instead of being guessed. Other event kinds
    carry no annotation lines.
    """
    actions: list[AdrAction] = []
    issues: list[AdrIssue] = []
    if event.kind not in ("cmd", "comment"):
        return ParsedEvent(actions=(), issues=())
    lines = event.text.split("\n")
    if event.kind == "comment":
        for number, line in enumerate(lines, start=1):
            hit = _marker_line(line)
            if hit is None:
                continue
            result = _annotation_line(hit[0], hit[1], line, number, event)
            if isinstance(result, AdrIssue):
                issues.append(result)
            else:
                actions.append(result)
        return ParsedEvent(actions=tuple(actions), issues=tuple(issues))
    state = _ScanState()
    for number, line in enumerate(lines, start=1):
        result = state.feed(line, number, event)
        if isinstance(result, AdrIssue):
            issues.append(result)
        elif result is not None:
            actions.append(result)
    return ParsedEvent(actions=tuple(actions), issues=tuple(issues))


def order_fields(fields: Mapping[str, str]) -> dict[str, str]:
    """Return the fields in catalog order, then extras in first-set order.

    This is the single source of the display and serialization order shared by
    the recording overlay and the standalone ADRs, so the two cannot drift.
    """
    ordered: dict[str, str] = {}
    for name in CATALOG:
        if name in fields:
            ordered[name] = fields[name]
    for name, value in fields.items():
        if name not in ordered:
            ordered[name] = value
    return ordered


def is_known_status(value: str) -> bool:
    """Return whether a status value is one of the recognized values.

    This is the single source of the status rule shared by the recording
    overlay and the standalone ADRs; an unknown value stays verbatim and is
    reported as an :class:`AdrIssue`, never a fatal error.
    """
    return value in STATUS_VALUES


def is_complete(fields: Mapping[str, str]) -> bool:
    """Return whether the fields satisfy the completeness rule (``title`` + ``decision``)."""
    return bool(fields.get("title")) and bool(fields.get("decision"))


_ordered_fields = order_fields


def _anchor_for(
    events: Sequence[SpoolEvent],
    cmd_indices: Sequence[int],
    index: int,
    marker: str,
) -> AdrAnchor | None:
    """Return the block reference of one ``#-``/``#+`` marker line."""
    if marker == "-":
        candidates = [position for position in cmd_indices if position < index]
        chosen = candidates[-1] if candidates else None
        kind = "previous"
    else:
        candidates = [position for position in cmd_indices if position > index]
        chosen = candidates[0] if candidates else None
        kind = "next"
    if chosen is None:
        return None
    event = events[chosen]
    return AdrAnchor(
        kind=kind,
        segment=event.segment,
        line=event.line,
        time=_instant(event.time),
        text=event.text,
    )


@dataclass
class _FoldMeta:
    """The metadata derived while folding: times, cwd, segments and anchor."""

    first_at: str = ""
    last_at: str = ""
    cwd: str = ""
    segments: list[str] = field(default_factory=list)
    anchor: AdrAnchor | None = None
    anchor_seen: bool = False

    def observe(
        self,
        item: AdrAction | AdrIssue,
        *,
        event: SpoolEvent,
        index: int,
        events: Sequence[SpoolEvent],
        cmd_indices: Sequence[int],
    ) -> None:
        """Track the first/last marker time, the touched segments and the anchor."""
        if not self.first_at:
            self.first_at = item.time
            self.cwd = event.cwd
        self.last_at = item.time
        if event.segment not in self.segments:
            self.segments.append(event.segment)
        if not self.anchor_seen and item.marker in ("-", "+"):
            self.anchor_seen = True
            self.anchor = _anchor_for(events, cmd_indices, index, item.marker)


def fold_events(
    events: Sequence[SpoolEvent],
    *,
    ref: str,
    host: str,
    user: str | None = None,
) -> RecordAnnotations:
    """Fold the annotation lines of chronologically ordered events into an ADR.

    Fields are set, overwritten and deleted in event order; the last state
    wins. Continuation text appends to the most recently set field. With no
    field left the result carries ``adr=None`` and only issues.

    Args:
        events: The events as returned by ``recording.load_events``.
        ref: The recording reference (also the ADR id).
        host: The host label from the recording's start state.
        user: The reader label; defaults to the current user.

    Returns:
        The folded ADR (or ``None``) and every visible issue.
    """
    reader = user if user is not None else _current_user()
    cmd_indices = [position for position, event in enumerate(events) if event.kind == "cmd"]
    fields: dict[str, str] = {}
    last_set: str | None = None
    issues: list[AdrIssue] = []
    meta = _FoldMeta()
    for index, event in enumerate(events):
        parsed = parse_event_text(event)
        items: list[AdrAction | AdrIssue] = [*parsed.actions, *parsed.issues]
        items.sort(key=lambda item: item.line)
        for item in items:
            meta.observe(item, event=event, index=index, events=events, cmd_indices=cmd_indices)
            if isinstance(item, AdrIssue):
                issues.append(item)
                continue
            last_set = _apply_item(item, fields=fields, last_set=last_set, issues=issues, event=event)
    return _make_adr(
        ref,
        fields,
        anchor=meta.anchor,
        issues=issues,
        first_at=meta.first_at,
        last_at=meta.last_at,
        user=reader,
        host=host,
        cwd=meta.cwd,
        segments=tuple(meta.segments),
        edited=False,
    )


def _apply_item(
    item: AdrAction,
    *,
    fields: dict[str, str],
    last_set: str | None,
    issues: list[AdrIssue],
    event: SpoolEvent,
) -> str | None:
    """Apply one resolved action to the folded fields; return the new ``last_set``."""
    if item.kind == "set":
        fields[item.name] = item.value
        last_set = item.name
        if item.name == "status" and not _known_status(item.value):
            issues.append(_issue(REASON_UNKNOWN_STATUS, item.marker, item.text, item.line, event))
    elif item.kind == "delete":
        _ = fields.pop(item.name, None)
        if last_set == item.name:
            last_set = None
    elif item.kind == "clear":
        fields.clear()
        last_set = None
    elif item.kind == "continue":
        if last_set is None:
            issues.append(_issue(REASON_NO_TARGET, item.marker, item.text, item.line, event))
        else:
            fields[last_set] = fields[last_set] + "\n" + item.value
    return last_set


def _known_status(value: str) -> bool:
    """Return whether a status value is one of the recognized values."""
    return is_known_status(value)


def _status_is_unknown(fields: Mapping[str, str]) -> bool:
    """Return whether a status value is set but not recognized."""
    status = fields.get("status")
    return bool(status) and not is_known_status(status)


def _make_adr(
    ref: str,
    fields: Mapping[str, str],
    *,
    anchor: AdrAnchor | None,
    issues: list[AdrIssue],
    first_at: str,
    last_at: str,
    user: str,
    host: str,
    cwd: str,
    segments: tuple[str, ...],
    edited: bool,
) -> RecordAnnotations:
    """Build the ADR result (or ``None``) from the effective fields.

    The completeness rule (``title`` plus ``decision``) and the status default
    live here once, so the event fold and an overlay cannot drift apart.
    """
    ordered = _ordered_fields(fields)
    if not ordered:
        return RecordAnnotations(adr=None, issues=tuple(issues))
    adr = Adr(
        ref=ref,
        fields=ordered,
        complete=is_complete(ordered),
        status=ordered.get("status") or STATUS_PROPOSED,
        anchor=anchor,
        issues=tuple(issues),
        first_at=first_at,
        last_at=last_at,
        user=user,
        host=host,
        cwd=cwd,
        segments=segments,
        edited=edited,
    )
    return RecordAnnotations(adr=adr, issues=tuple(issues))


def _host_of(record: Recording) -> str:
    """Return the host label from the recording's start state."""
    section = record.state_start.collectors.get("host")
    if isinstance(section, Mapping):
        hostname = section.get("hostname")
        if isinstance(hostname, str) and hostname:
            return hostname
    return "Unknown"


def annotations_for(record: Recording, events: Sequence[SpoolEvent] | None = None) -> RecordAnnotations:
    """Read and fold the annotations of one recording.

    A valid ``adr.toml`` overlay replaces the event-derived field set
    completely (an explicit edit beats observation); the event findings stay
    visible and the ADR is marked as edited. Anchor and auto fields still
    derive from the events when there are annotation lines.

    Args:
        record: The recording whose annotations are folded.
        events: Optional pre-loaded events; loaded from the recording when
            omitted, so callers that already hold the events read the
            recording only once.
    """
    folded_events = recording.load_events(record) if events is None else events
    folded = fold_events(folded_events, ref=record.ref, host=_host_of(record))
    overlay = load_overlay(record.path)
    if overlay is None:
        return folded
    issues = list(folded.issues)
    if _status_is_unknown(overlay.fields):
        issues.append(
            AdrIssue(
                reason=REASON_UNKNOWN_STATUS,
                marker=".",
                segment=OVERLAY_FILENAME,
                line=0,
                time=overlay.edited_at,
                text=f"adr.status {overlay.fields['status']}",
            )
        )
    base = folded.adr
    return _make_adr(
        record.ref,
        overlay.fields,
        anchor=None if base is None else base.anchor,
        issues=issues,
        first_at="" if base is None else base.first_at,
        last_at="" if base is None else base.last_at,
        user=base.user if base is not None else _current_user(),
        host=base.host if base is not None else _host_of(record),
        cwd="" if base is None else base.cwd,
        segments=() if base is None else base.segments,
        edited=True,
    )


def adr_for(record: Recording, events: Sequence[SpoolEvent] | None = None) -> Adr | None:
    """Return the recording's ADR, or ``None`` when it carries no field.

    Args:
        record: The recording whose ADR is returned.
        events: Optional pre-loaded events (see :func:`annotations_for`).
    """
    return annotations_for(record, events).adr


def adrs_for(recordings: Sequence[Recording]) -> list[Adr]:
    """Return the ADRs of the given recordings, in their order."""
    adrs: list[Adr] = []
    for record in recordings:
        adr = adr_for(record)
        if adr is not None:
            adrs.append(adr)
    return adrs


def parse_ref_list(value: str) -> tuple[str, ...]:
    """Split a ``uses``/``supersedes`` value into whitespace/comma tokens."""
    return tuple(token for token in re.split(r"[\s,]+", value) if token)


def resolve_ref_list(value: str, refs: Sequence[str]) -> tuple[ResolvedRef, ...]:
    """Resolve ``uses``/``supersedes`` tokens against known recording refs.

    Matching is exact or unique-prefix, case-insensitively. An unknown or
    ambiguous token resolves to ``None`` — display shows ``(unknown)``; it is
    never an error and never an issue.
    """
    resolved: list[ResolvedRef] = []
    for token in parse_ref_list(value):
        lowered = token.lower()
        exact = [ref for ref in refs if ref.lower() == lowered]
        if len(exact) == 1:
            resolved.append(ResolvedRef(raw=token, resolved=exact[0]))
            continue
        matches = [ref for ref in refs if ref.lower().startswith(lowered)]
        resolved.append(ResolvedRef(raw=token, resolved=matches[0] if len(matches) == 1 else None))
    return tuple(resolved)


@dataclass(frozen=True)
class AdrOverlay:
    """The parsed ``adr.toml`` overlay of one recording."""

    edited_at: str
    fields: Mapping[str, str]


def _invalid_overlay(source: Path, message: str) -> AnnotationError:
    return AnnotationError(f"Invalid ADR overlay: {source}: {message}")


def parse_overlay_document(raw: bytes, source: Path) -> AdrOverlay:
    """Parse and strictly validate an overlay document.

    The document must be a TOML table with ``schema_version = 1`` and a
    ``[fields]`` table of non-empty strings; the field names are arbitrary
    (catalog or extra). An empty ``[fields]`` table is valid and means
    "deliberately no ADR". Any violation is a loud :class:`AnnotationError`
    naming ``source``.
    """
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise _invalid_overlay(source, str(exc)) from exc
    version = data.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version != OVERLAY_SCHEMA_VERSION:
        raise _invalid_overlay(source, f"schema_version must be {OVERLAY_SCHEMA_VERSION}")
    fields_raw = data.get("fields")
    if not isinstance(fields_raw, dict):
        raise _invalid_overlay(source, "missing [fields] table")
    fields: dict[str, str] = {}
    typed_fields = cast("dict[str, object]", fields_raw)
    for name, value in typed_fields.items():
        if not isinstance(value, str) or not value.strip():
            raise _invalid_overlay(source, f"field '{name}' must be a non-empty string")
        fields[name] = value
    edited_at = data.get("edited_at", "")
    if not isinstance(edited_at, str):
        raise _invalid_overlay(source, "edited_at must be a string")
    return AdrOverlay(edited_at=edited_at, fields=fields)


def load_overlay(record_dir: Path) -> AdrOverlay | None:
    """Return the record's overlay, or ``None`` when there is none.

    Only a genuinely missing path means "no overlay". A present but unreadable
    or invalid overlay fails loudly with its path; a directory or a broken
    symlink named ``adr.toml`` is such a defect and is never skipped.
    """
    path = record_dir / OVERLAY_FILENAME
    if path.is_symlink() or path.exists():
        if not path.is_file():
            raise AnnotationError(f"ADR overlay is not a regular file: {path}")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise AnnotationError(f"Cannot read ADR overlay: {path}: {exc}") from exc
        return parse_overlay_document(raw, path)
    return None


def write_overlay(record_dir: Path, fields: Mapping[str, str]) -> Path:
    """Write the overlay atomically (mode ``600``) and return its path.

    The document is staged via :func:`lion.program.recording.stage_text` and
    published with ``os.replace``; a failure leaves no partial file behind.
    """
    payload: dict[str, object] = {
        "schema_version": OVERLAY_SCHEMA_VERSION,
        "edited_at": datetime.now(UTC).isoformat(),
        "fields": dict(fields),
    }
    target = record_dir / OVERLAY_FILENAME
    temporary = recording.stage_text(record_dir, tomli_w.dumps(payload))
    try:
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def _toml_field(name: str, value: str) -> str:
    """Render one field as a TOML line with proper key/value escaping."""
    return tomli_w.dumps({name: value}).strip()


def overlay_template(adr: Adr | None) -> str:
    """Return the editable overlay template shown by ``records edit``.

    The current effective fields appear as active lines in catalog order
    (extras after them); unset catalog fields appear as comments so the shape
    is visible without inventing values.
    """
    lines = [
        "# LION ADR overlay — edit the [fields] table and save.",
        "# An empty [fields] table deliberately removes the ADR.",
        "# Catalog: " + ", ".join(CATALOG) + ".",
        "# status: " + " | ".join(STATUS_VALUES) + ".",
        f"schema_version = {OVERLAY_SCHEMA_VERSION}",
        "",
        "[fields]",
    ]
    current = dict(adr.fields) if adr is not None else {}
    for name in CATALOG:
        if name in current:
            lines.append(_toml_field(name, current[name]))
        else:
            lines.append(f'# {name} = ""')
    extras = {name: value for name, value in current.items() if name not in CATALOG}
    if extras:
        lines.append("")
        lines.append("# Additional fields:")
        for name, value in extras.items():
            lines.append(_toml_field(name, value))
    return "\n".join(lines) + "\n"


def annotations_checks(_ctx: DoctorContext) -> list[Finding]:
    """Report invalid ADR overlays without writing (doctor topic ``watch``).

    Damaged records are already reported by ``watch_checks``; this check only
    inspects the readable ones. Incomplete or open ADRs are never a finding.
    """
    space = recording.inspect_space()
    findings: list[Finding] = []
    for record in space.recordings:
        if record is None:
            continue
        try:
            _ = load_overlay(record.path)
        except AnnotationError as exc:
            findings.append(
                Finding(
                    topic="watch",
                    name=f"watch.adr.{record.ref}",
                    status=CheckStatus.ERROR,
                    message=str(exc),
                    hint="Fix or remove adr.toml in the record, or rewrite it with 'lion records edit <ref>'.",
                )
            )
    return findings
