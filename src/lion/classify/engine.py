"""Rule matching over recorded shell input, derived at read time.

Blocks are the non-empty ``cmd`` events that are not the silent in-session
reads (``lion watch R``/``R-``); comments, results, exits and segment headers
are never classified. The block text is parsed with the bounded parser; when it
leaves the supported syntax subset, the block yields no matches. Segment-scope
rules match anchored after stripping ``sudo``/``NAME=value`` prefixes;
block-scope rules search masked lines so quotes and here-document bodies (and
with them printed text) never match. Every match is metadata, never a claim
about success.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from lion.classify.model import Block, Classification, Match, RuleSetInfo
from lion.classify.parser import PARSER_VERSION, ParsedCommand, parse_command, strip_prefixes
from lion.classify.rules import SCOPE_SEGMENT, Rule, RuleSet, compile_pattern
from lion.program.recording import Recording, SpoolEvent, load_events

__all__ = ["classify_events", "classify_recording", "classify_text", "is_silent_command"]

_MODE_NORMAL = "normal"
_MODE_SINGLE = "single"
_MODE_DOUBLE = "double"
_MODE_DOLLAR = "dollar"
_MODE_UNKNOWN = "unknown"

_QUOTES: Mapping[str, tuple[str, bool]] = {
    _MODE_SINGLE: ("'", False),
    _MODE_DOUBLE: ('"', True),
    _MODE_DOLLAR: ("'", True),
}

_SILENT_TAILS = (("watch", "R"), ("watch", "R-"))
_SILENT_HEADS = (("lion",), ("uv", "run", "lion"))

_HEREDOC_OPERATOR = re.compile(r"^<<(-?)\s*(\\?)([A-Za-z0-9_]+|'[^']*'|\"[^\"]*\")")


@dataclass
class _MaskState:
    """Mutable masking state carried across the lines of one block."""

    mode: str = _MODE_NORMAL
    heredocs: list[tuple[str, bool]] = field(default_factory=list)


def is_silent_command(text: str) -> bool:
    """Return whether the first segment is a silent ``lion watch R``/``R-`` read.

    The terse in-session reads should not appear in their own numbering. The
    best-effort detection looks at the first segment after stripping ``sudo``
    prefixes and ``NAME=value`` assignments and accepts ``lion watch R``,
    ``lion watch R-``, ``uv run lion watch R`` and ``uv run lion watch R-``;
    aliases and other wrappers are not recognized (a cosmetic limit).
    """
    parsed = parse_command(text)
    if not parsed.supported or not parsed.segments:
        return False
    tokens = strip_prefixes(parsed.segments[0]).split()
    if len(tokens) < 3 or tuple(tokens[-2:]) not in _SILENT_TAILS:
        return False
    return tuple(tokens[:-2]) in _SILENT_HEADS


def _instant(value: float) -> str:
    return datetime.fromtimestamp(value, tz=UTC).isoformat()


def _compile_rules(ruleset: RuleSet) -> dict[str, re.Pattern[str]]:
    return {rule.id: compile_pattern(rule.match) for rule in ruleset.rules}


def _resolve_emit(rule: Rule, match: re.Match[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for name, value in rule.emit.items():
        if value.startswith("$"):
            captured = match.group(int(value[1:]))
            fields[name] = captured if captured is not None else ""
        else:
            fields[name] = value
    return fields


def _match_order(match: Match) -> tuple[str, str, tuple[tuple[str, str], ...]]:
    return (match.group, match.rule_id, tuple(match.fields.items()))


def _record_hit(
    hits: dict[tuple[str, str, tuple[tuple[str, str], ...]], Match],
    rule: Rule,
    found: re.Match[str],
) -> None:
    fields = _resolve_emit(rule, found)
    key = (rule.group, rule.id, tuple(fields.items()))
    if key not in hits:
        hits[key] = Match(
            rule_id=rule.id,
            source=rule.source,
            group=rule.group,
            fields=fields,
            guard=rule.guard,
        )


def _matches_for(
    text: str,
    parsed: ParsedCommand,
    ruleset: RuleSet,
    compiled: Mapping[str, re.Pattern[str]],
) -> tuple[Match, ...]:
    """Return the deduplicated, deterministically ordered matches of one block."""
    hits: dict[tuple[str, str, tuple[tuple[str, str], ...]], Match] = {}
    masked: list[str | None] | None = None
    for rule in ruleset.rules:
        pattern = compiled[rule.id]
        if rule.scope == SCOPE_SEGMENT:
            for segment in parsed.segments:
                found = pattern.match(strip_prefixes(segment))
                if found is not None:
                    _record_hit(hits, rule, found)
        else:
            if masked is None:
                masked = _masked_lines(text)
            for line in masked:
                if line is None:
                    continue
                found = pattern.search(line)
                if found is not None:
                    _record_hit(hits, rule, found)
    return tuple(sorted(hits.values(), key=_match_order))


def classify_text(text: str, ruleset: RuleSet) -> tuple[Match, ...]:
    """Classify one shell text (a single block) against the rule set.

    Unsupported syntax yields no matches, never a guess.
    """
    parsed = parse_command(text)
    if not parsed.supported:
        return ()
    return _matches_for(text, parsed, ruleset, _compile_rules(ruleset))


def classify_events(events: Sequence[SpoolEvent], ruleset: RuleSet) -> tuple[Block, ...]:
    """Build the classified blocks of chronologically ordered events.

    ``index`` numbers the blocks in event order, 1-based, counting only the
    non-empty ``cmd`` events that are not silent reads.
    """
    compiled = _compile_rules(ruleset)
    blocks: list[Block] = []
    index = 0
    for event in events:
        if event.kind != "cmd" or not event.text.strip() or is_silent_command(event.text):
            continue
        parsed = parse_command(event.text)
        matches = () if not parsed.supported else _matches_for(event.text, parsed, ruleset, compiled)
        index += 1
        blocks.append(
            Block(
                index=index,
                segment=event.segment,
                time=_instant(event.time),
                text=event.text,
                matches=matches,
            )
        )
    return tuple(blocks)


def classify_recording(record: Recording, ruleset: RuleSet) -> Classification:
    """Classify one recording's blocks at read time (read-only)."""
    blocks = classify_events(load_events(record), ruleset)
    return Classification(
        ref=record.ref,
        ruleset=RuleSetInfo(
            schema_version=ruleset.schema_version,
            version=ruleset.version,
            hash=ruleset.hash,
        ),
        parser_version=PARSER_VERSION,
        blocks=blocks,
    )


def _closing_quote(text: str, start: int, quote: str, escapes: bool) -> int:
    """Return the index of the closing quote, or ``-1``."""
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


def _substitution_end(line: str, start: int) -> int:
    """Return the index of the ``)`` closing a ``$(`` substitution, or ``-1``."""
    depth = 1
    index = start
    while index < len(line):
        char = line[index]
        if char == "\\":
            index += 2
            continue
        if char == "'":
            end = line.find("'", index + 1)
            if end < 0:
                return -1
            index = end + 1
            continue
        if char == '"':
            end = _closing_quote(line, index + 1, '"', escapes=True)
            if end < 0:
                return -1
            index = end + 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def _backtick_end(line: str, start: int) -> int:
    """Return the index of the closing backtick, or ``-1``."""
    index = start
    while index < len(line):
        char = line[index]
        if char == "\\":
            index += 2
            continue
        if char == "`":
            return index
        index += 1
    return -1


def _heredoc_at(line: str, index: int) -> tuple[tuple[str, bool] | None, int] | None:
    """Parse the here-document operator at ``index``.

    Returns:
        The pending heredoc (delimiter, strip-tabs) - or ``None`` when the
        body was already consumed - plus the index after the operator, or
        ``None`` when the operator cannot be resolved statically.
    """
    match = _HEREDOC_OPERATOR.match(line, index)
    if match is None:
        return None
    word = match.group(3)
    delimiter = word[1:-1] if word.startswith(("'", '"')) else word
    if not delimiter:
        return None
    pending = (delimiter, match.group(1) == "-")
    return pending, match.end()


def _mask_line(state: _MaskState, line: str, start: int = 0) -> str | None:
    """Mask one line from ``start``; ``None`` marks an unresolvable line."""
    out: list[str] = []
    index = start
    length = len(line)
    while index < length:
        char = line[index]
        if char == "\\":
            return None
        if char == "#" and (index == 0 or line[index - 1].isspace() or line[index - 1] in ";|&()<>"):
            break
        if char == "'":
            end = line.find("'", index + 1)
            if end < 0:
                state.mode = _MODE_SINGLE
                return "".join(out)
            index = end + 1
            continue
        if char == '"':
            end = _closing_quote(line, index + 1, '"', escapes=True)
            if end < 0:
                state.mode = _MODE_DOUBLE
                return "".join(out)
            index = end + 1
            continue
        if char == "$" and line[index + 1 : index + 2] == "'":
            end = _closing_quote(line, index + 2, "'", escapes=True)
            if end < 0:
                state.mode = _MODE_DOLLAR
                return "".join(out)
            index = end + 1
            continue
        if char == "$" and line[index + 1 : index + 2] == "(":
            end = _substitution_end(line, index + 2)
            if end < 0:
                state.mode = _MODE_UNKNOWN
                return None
            index = end + 1
            continue
        if char == "`":
            end = _backtick_end(line, index + 1)
            if end < 0:
                state.mode = _MODE_UNKNOWN
                return None
            index = end + 1
            continue
        if line.startswith("<<<", index):
            state.mode = _MODE_UNKNOWN
            return None
        if line.startswith("<<", index):
            parsed = _heredoc_at(line, index)
            if parsed is None:
                state.mode = _MODE_UNKNOWN
                return None
            pending, _ = parsed
            if pending is not None:
                state.heredocs.append(pending)
            break
        if char in "<>" and line[index + 1 : index + 2] == "(":
            state.mode = _MODE_UNKNOWN
            return None
        out.append(char)
        index += 1
    return "".join(out)


def _mask_one(state: _MaskState, line: str) -> str | None:
    """Mask one line given the state left by the previous lines."""
    if state.heredocs:
        delimiter, strip_tabs = state.heredocs[0]
        candidate = line.lstrip("\t") if strip_tabs else line
        if candidate == delimiter:
            state.heredocs.pop(0)
        return ""
    if state.mode in _QUOTES:
        quote, escapes = _QUOTES[state.mode]
        end = _closing_quote(line, 0, quote, escapes)
        if end < 0:
            return ""
        state.mode = _MODE_NORMAL
        return _mask_line(state, line, end + 1)
    if state.mode == _MODE_UNKNOWN:
        return None
    return _mask_line(state, line, 0)


def _masked_lines(text: str) -> list[str | None]:
    """Return one masked line per input line; ``None`` marks unresolvable lines."""
    state = _MaskState()
    return [_mask_one(state, line) for line in text.split("\n")]
