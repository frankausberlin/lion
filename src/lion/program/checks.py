"""Shared types for the read-only ``doctor`` check framework.

The types live in this leaf module so the owning modules (``program.storage``,
``program.shlib``, ``state.diagnosis``) can build findings without importing the
aggregator :mod:`lion.program.doctor`, which would create an import cycle. The
aggregator re-exports them, so ``lion.program.doctor.Finding`` stays the public
name.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class CheckStatus(StrEnum):
    """Outcome of a single doctor check.

    ``SKIP`` means "not applicable / optional and not needed"; it is neutral and
    never affects the exit code.
    """

    OK = "ok"
    WARN = "warn"
    ERROR = "error"
    SKIP = "skip"


@dataclass(frozen=True)
class Finding:
    """One diagnostic finding produced by a check function."""

    topic: str
    name: str
    status: CheckStatus
    message: str
    hint: str = ""
    commands: tuple[str, ...] = ()


@dataclass(frozen=True)
class DoctorContext:
    """Everything the read-only checks may inspect."""

    state: dict[str, dict[str, object]]
    home: Path


Check = Callable[[DoctorContext], Sequence[Finding]]
