"""Rule matching over recorded shell input, derived at read time.

Blocks are the non-empty ``cmd`` events that are not the silent in-session
reads (``lion watch R``/``R-``). Silent means every segment is such a read, so
a mixed block (``lion watch R; sudo apt install foo``) stays classified.
Comments, results, exits and segment headers are never classified. The block
text is parsed once with the bounded parser; when it leaves the supported
syntax subset, the block yields no matches.
Segment-scope rules match anchored after stripping ``sudo``/``NAME=value``
prefixes.

Block-scope rules search masked lines. The parser gate already rejects
substitutions, here-documents, here-strings and unquoted escaping, so the
masker only removes quoted spans and comments; every quoted span becomes a
sentinel character that is neither whitespace nor a word or boundary
character, so a removed quote can neither join two words nor fake a segment
start at a line that merely continues a quoted string. A match is metadata,
never a claim about success.

With a loaded policy file (ADR 0019) every supported block also carries one
command per parsed segment, each with its own segment-scope matches and its
policy results; block-scope matches stay block-level and are never policy
evidence. The block matches remain the deduplicated union exactly as before.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from lion.classify.model import Block, Classification, Command, Match, Policy, RuleSetInfo
from lion.classify.parser import (
    PARSER_VERSION,
    ParsedCommand,
    find_closing_quote,
    parse_command,
    strip_prefixes,
)
from lion.classify.policies import evaluate_command
from lion.classify.rules import SCOPE_SEGMENT, Rule, RuleSet, compile_pattern
from lion.program.recording import Recording, SpoolEvent, load_events

__all__ = ["classify_events", "classify_recording", "classify_text", "is_silent_command"]

_MODE_NORMAL = "normal"
_MODE_SINGLE = "single"
_MODE_DOUBLE = "double"
_MODE_DOLLAR = "dollar"

_QUOTES: Mapping[str, tuple[str, bool]] = {
    _MODE_SINGLE: ("'", False),
    _MODE_DOUBLE: ('"', True),
    _MODE_DOLLAR: ("'", True),
}

#: Placeholder for a masked quoted span: not whitespace, not a word character
#: and no boundary for block-scope anchors, so masking never fabricates one.
_MASK_SENTINEL = "\x00"

_SILENT_TAILS = (("watch", "R"), ("watch", "R-"))
_SILENT_HEADS = (("lion",), ("uv", "run", "lion"))


@dataclass
class _MaskState:
    """Mutable masking state carried across the lines of one block."""

    mode: str = _MODE_NORMAL


def _segment_is_silent(segment: str) -> bool:
    tokens = strip_prefixes(segment).split()
    if len(tokens) < 3 or tuple(tokens[-2:]) not in _SILENT_TAILS:
        return False
    return tuple(tokens[:-2]) in _SILENT_HEADS


def _is_silent(parsed: ParsedCommand) -> bool:
    if not parsed.supported or not parsed.segments:
        return False
    return all(_segment_is_silent(segment) for segment in parsed.segments)


def is_silent_command(text: str) -> bool:
    """Return whether every segment is a silent ``lion watch R``/``R-`` read.

    The terse in-session reads should not appear in their own numbering. A block
    is silent only when *all* of its segments are such reads, so a chain such as
    ``lion watch R; lion watch R-`` stays silent while a mixed block (for
    example ``lion watch R; sudo apt install foo``) is classified. The
    best-effort detection looks at each segment after stripping ``sudo``
    prefixes and ``NAME=value`` assignments and accepts ``lion watch R``,
    ``lion watch R-``, ``uv run lion watch R`` and ``uv run lion watch R-``;
    aliases and other wrappers are not recognized (a cosmetic limit).
    """
    return _is_silent(parse_command(text))


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


def _collect_matches(
    text: str,
    parsed: ParsedCommand,
    ruleset: RuleSet,
    compiled: Mapping[str, re.Pattern[str]],
    with_segments: bool = False,
) -> tuple[tuple[Match, ...], tuple[tuple[Match, ...], ...]]:
    """Return the block's matches and the segment-scope matches per segment.

    The block matches are the deduplicated, deterministically ordered union of
    all segment-scope and block-scope hits, exactly as before. The per-segment
    tuples are only collected (and returned) when ``with_segments`` asks for
    them, so the default path without policies pays nothing for the command
    view.
    """
    hits: dict[tuple[str, str, tuple[tuple[str, str], ...]], Match] = {}
    segment_hits: list[dict[tuple[str, str, tuple[tuple[str, str], ...]], Match]] | None = (
        [{} for _ in parsed.segments] if with_segments else None
    )
    stripped = [strip_prefixes(segment) for segment in parsed.segments]
    masked: list[str] | None = None
    for rule in ruleset.rules:
        pattern = compiled[rule.id]
        if rule.scope == SCOPE_SEGMENT:
            for position, segment in enumerate(stripped):
                found = pattern.match(segment)
                if found is not None:
                    _record_hit(hits if segment_hits is None else segment_hits[position], rule, found)
        else:
            if masked is None:
                masked = _masked_lines(text)
            for line in masked:
                found = pattern.search(line)
                if found is not None:
                    _record_hit(hits, rule, found)
    if segment_hits is None:
        return tuple(sorted(hits.values(), key=_match_order)), ()
    for local in segment_hits:
        for key, match in local.items():
            hits.setdefault(key, match)
    return (
        tuple(sorted(hits.values(), key=_match_order)),
        tuple(tuple(sorted(local.values(), key=_match_order)) for local in segment_hits),
    )


def classify_text(text: str, ruleset: RuleSet) -> tuple[Match, ...]:
    """Classify one shell text (a single block) against the rule set.

    Unsupported syntax yields no matches, never a guess.
    """
    parsed = parse_command(text)
    if not parsed.supported:
        return ()
    matches, _ = _collect_matches(text, parsed, ruleset, _compile_rules(ruleset))
    return matches


def classify_events(
    events: Sequence[SpoolEvent],
    ruleset: RuleSet,
    policies: tuple[Policy, ...] = (),
) -> tuple[Block, ...]:
    """Build the classified blocks of chronologically ordered events.

    ``index`` numbers the blocks in event order, 1-based, counting only the
    non-empty ``cmd`` events that are not silent reads. With loaded policies
    every supported block also carries one command per parsed segment with its
    own segment-scope matches and policy results.
    """
    compiled = _compile_rules(ruleset)
    blocks: list[Block] = []
    index = 0
    for event in events:
        if event.kind != "cmd" or not event.text.strip():
            continue
        parsed = parse_command(event.text)
        if _is_silent(parsed):
            continue
        commands: tuple[Command, ...] = ()
        matches: tuple[Match, ...] = ()
        if parsed.supported:
            matches, segment_matches = _collect_matches(
                event.text, parsed, ruleset, compiled, with_segments=bool(policies)
            )
            if policies:
                commands = tuple(
                    Command(
                        index=position,
                        text=segment,
                        matches=segment_matches[position - 1],
                        policy_results=evaluate_command(policies, segment_matches[position - 1]),
                    )
                    for position, segment in enumerate(parsed.segments, start=1)
                )
        index += 1
        blocks.append(
            Block(
                index=index,
                segment=event.segment,
                time=_instant(event.time),
                text=event.text,
                matches=matches,
                commands=commands,
            )
        )
    return tuple(blocks)


def classify_recording(
    record: Recording,
    ruleset: RuleSet,
    events: Sequence[SpoolEvent] | None = None,
    policies: tuple[Policy, ...] = (),
) -> Classification:
    """Classify one recording's blocks at read time (read-only).

    Args:
        record: The recording whose events are classified.
        ruleset: The merged rule set to match with.
        events: Optional pre-loaded events; loaded from the recording when
            omitted, so callers that already hold the events read the
            recording only once.
        policies: Optional loaded policies; when non-empty every block also
            carries per-command matches and policy results.
    """
    block_events = load_events(record) if events is None else events
    blocks = classify_events(block_events, ruleset, policies)
    return Classification(
        ref=record.ref,
        ruleset=RuleSetInfo(
            schema_version=ruleset.schema_version,
            version=ruleset.version,
            hash=ruleset.hash,
        ),
        parser_version=PARSER_VERSION,
        blocks=blocks,
        policies=policies,
    )


def _mask_line(state: _MaskState, line: str, start: int = 0) -> str:
    """Mask one line from ``start``; the parser gate rejects everything else."""
    out: list[str] = []
    index = start
    length = len(line)
    while index < length:
        char = line[index]
        if char == "#" and (index == 0 or line[index - 1].isspace() or line[index - 1] in ";|&()<>"):
            break
        if char == "'":
            end = line.find("'", index + 1)
            if end < 0:
                state.mode = _MODE_SINGLE
                out.append(_MASK_SENTINEL)
                return "".join(out)
            out.append(_MASK_SENTINEL)
            index = end + 1
            continue
        if char == '"':
            end = find_closing_quote(line, index + 1, '"')
            if end < 0:
                state.mode = _MODE_DOUBLE
                out.append(_MASK_SENTINEL)
                return "".join(out)
            out.append(_MASK_SENTINEL)
            index = end + 1
            continue
        if char == "$" and line[index + 1 : index + 2] == "'":
            end = find_closing_quote(line, index + 2, "'", escapes=True)
            if end < 0:
                state.mode = _MODE_DOLLAR
                out.append(_MASK_SENTINEL)
                return "".join(out)
            out.append(_MASK_SENTINEL)
            index = end + 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _mask_one(state: _MaskState, line: str) -> str:
    """Mask one line given the quote state left by the previous lines."""
    if state.mode in _QUOTES:
        quote, escapes = _QUOTES[state.mode]
        end = find_closing_quote(line, 0, quote, escapes)
        if end < 0:
            return _MASK_SENTINEL
        state.mode = _MODE_NORMAL
        return _MASK_SENTINEL + _mask_line(state, line, end + 1)
    return _mask_line(state, line, 0)


def _masked_lines(text: str) -> list[str]:
    """Return one masked line per input line."""
    state = _MaskState()
    return [_mask_one(state, line) for line in text.split("\n")]
