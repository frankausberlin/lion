"""The read-time classification model: matches, blocks and the classification.

The model is a pure value tree derived from raw recording events on every read
(ADR 0015); nothing here is persisted. JSON shapes are built by the model
itself so the CLI contract and the tests cannot drift apart.
"""

from collections.abc import Mapping
from dataclasses import dataclass

__all__ = ["Block", "Classification", "Match", "RuleSetInfo"]


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
class Block:
    """One numbered, non-silent shell input block with its matches."""

    index: int
    segment: str
    time: str
    text: str
    matches: tuple[Match, ...]

    def to_dict(self) -> dict[str, object]:
        """Return the block as the ``lion classify --json`` shape."""
        return {
            "index": self.index,
            "segment": self.segment,
            "time": self.time,
            "text": self.text,
            "matches": [match.to_dict() for match in self.matches],
        }


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

    def to_dict(self) -> dict[str, object]:
        """Return the classification as the ``lion classify --json`` shape."""
        return {
            "ref": self.ref,
            "ruleset": self.ruleset.to_dict(),
            "parser_version": self.parser_version,
            "blocks": [block.to_dict() for block in self.blocks],
        }
