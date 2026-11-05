"""Shared watch finding model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WatchFinding:
    """One rendered finding from a watch source.

    Attributes:
        source: Source name, currently ``dash``.
        name: Entry name inside the source.
        lines: Complete output lines for the finding.
        is_error: Whether the finding is a read or scan error.
    """

    source: str
    name: str
    lines: tuple[str, ...]
    is_error: bool = False
