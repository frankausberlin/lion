"""The read-time classification model: matches, commands, blocks and policies.

The model is a pure value tree derived from raw recording events on every read
(ADR 0015); nothing here is persisted. JSON shapes are built by the model
itself so the CLI contract and the tests cannot drift apart. Blocks carry
per-command detail and policy results only when a policy file is active
(ADR 0019); without one the JSON shape stays byte-identical.
"""

from collections.abc import Mapping
from dataclasses import dataclass

__all__ = [
    "Block",
    "Classification",
    "Command",
    "Match",
    "Policy",
    "PolicyEvidence",
    "PolicyPattern",
    "PolicyResult",
    "RuleSetInfo",
]


@dataclass(frozen=True)
class Match:
    """One rule hit: metadata derived from a matched input block.

    A match never claims that something happened or succeeded; it records which
    rule matched and which fields it emitted.
    """

    rule_id: str
    source: str
    group: str
    fields: Mapping[str, str]
    guard: str

    def to_dict(self) -> dict[str, object]:
        """Return the match as the ``lion classify --json`` shape."""
        return {
            "rule_id": self.rule_id,
            "source": self.source,
            "group": self.group,
            "fields": dict(self.fields),
            "guard": self.guard,
        }


@dataclass(frozen=True)
class PolicyPattern:
    """One policy condition over a classifier group and its fields.

    A pattern matches a classifier match of the same group when every named
    field both exists and equals the single value or is one of the listed
    values; an empty field table matches every match of the group. This is the
    whole condition language: a criterion is a list of patterns (OR), a pattern
    is a conjunction of field conditions (AND) and a list value is a
    disjunction over values. A missing field never matches; there is no
    negation.
    """

    group: str
    fields: Mapping[str, str | tuple[str, ...]]

    def to_dict(self) -> dict[str, object]:
        """Return the pattern as the ``lion classify --json`` shape."""
        return {
            "group": self.group,
            "fields": {name: value if isinstance(value, str) else list(value) for name, value in self.fields.items()},
        }


@dataclass(frozen=True)
class Policy:
    """One loaded policy definition with its optional criteria."""

    id: str
    text: str
    success: tuple[PolicyPattern, ...] = ()
    fail: tuple[PolicyPattern, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return the policy as the ``lion classify --json`` shape."""
        payload: dict[str, object] = {"id": self.id, "text": self.text}
        if self.success:
            payload["success"] = [pattern.to_dict() for pattern in self.success]
        if self.fail:
            payload["fail"] = [pattern.to_dict() for pattern in self.fail]
        return payload


@dataclass(frozen=True)
class PolicyEvidence:
    """One contributing match of a policy result (1-based ``match_index``)."""

    criterion: str
    match_index: int

    def to_dict(self) -> dict[str, object]:
        """Return the evidence as the ``lion classify --json`` shape."""
        return {"criterion": self.criterion, "match_index": self.match_index}


@dataclass(frozen=True)
class PolicyResult:
    """The outcome of one policy for one command: success, fail or conflict."""

    policy_id: str
    status: str
    evidence: tuple[PolicyEvidence, ...]

    def to_dict(self) -> dict[str, object]:
        """Return the result as the ``lion classify --json`` shape."""
        return {
            "policy_id": self.policy_id,
            "status": self.status,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class Command:
    """One simple command segment with its own matches and policy results.

    The matches are the segment-scope hits of that segment only; block-scope
    matches stay block-level and are never policy evidence. ``commands`` are
    populated only while policies are active.
    """

    index: int
    text: str
    matches: tuple[Match, ...]
    policy_results: tuple[PolicyResult, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return the command as the ``lion classify --json`` shape."""
        return {
            "index": self.index,
            "text": self.text,
            "matches": [match.to_dict() for match in self.matches],
            "policy_results": [result.to_dict() for result in self.policy_results],
        }


@dataclass(frozen=True)
class Block:
    """One numbered, non-silent shell input block with its matches.

    The block matches are the deduplicated union of the segment-scope and
    block-scope hits, exactly as before; ``commands`` repeats the
    segment-scope matches per command and carries the policy results.
    """

    index: int
    segment: str
    time: str
    text: str
    matches: tuple[Match, ...]
    commands: tuple[Command, ...] = ()

    def to_dict(self, include_commands: bool = False) -> dict[str, object]:
        """Return the block as the ``lion classify --json`` shape.

        Args:
            include_commands: Include the ``commands`` key; the caller sets it
                when policies are active, so the shape without policies stays
                unchanged.
        """
        payload: dict[str, object] = {
            "index": self.index,
            "segment": self.segment,
            "time": self.time,
            "text": self.text,
            "matches": [match.to_dict() for match in self.matches],
        }
        if include_commands:
            payload["commands"] = [command.to_dict() for command in self.commands]
        return payload


@dataclass(frozen=True)
class RuleSetInfo:
    """Provenance of the rule set a classification was derived with."""

    schema_version: int
    version: str
    hash: str

    def to_dict(self) -> dict[str, object]:
        """Return the provenance as the ``lion classify --json`` shape."""
        return {
            "schema_version": self.schema_version,
            "version": self.version,
            "hash": self.hash,
        }


@dataclass(frozen=True)
class Classification:
    """The classified blocks of one recording, derived at read time."""

    ref: str
    ruleset: RuleSetInfo
    parser_version: str
    blocks: tuple[Block, ...]
    policies: tuple[Policy, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return the classification as the ``lion classify --json`` shape."""
        payload: dict[str, object] = {
            "ref": self.ref,
            "ruleset": self.ruleset.to_dict(),
            "parser_version": self.parser_version,
        }
        if self.policies:
            payload["policies"] = [policy.to_dict() for policy in self.policies]
        payload["blocks"] = [block.to_dict(include_commands=bool(self.policies)) for block in self.blocks]
        return payload
