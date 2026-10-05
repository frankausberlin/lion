"""Common types shared by every LION state collector.

This module intentionally imports no collector module so that collectors can
depend on it without creating import cycles.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum


class CollectorStatus(StrEnum):
    """Outcome of a single collector run."""

    OK = "ok"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


@dataclass(frozen=True)
class CollectorResult:
    """Data and status returned by a collector function."""

    status: CollectorStatus
    data: dict[str, object]
    error: str = ""


@dataclass(frozen=True)
class Collector:
    """A named collector that gathers one section of the machine state."""

    name: str
    collect: Callable[[], CollectorResult]


def collect_state(collectors: Sequence[Collector]) -> dict[str, dict[str, object]]:
    """Run every collector and return a JSON/TOML-ready state mapping.

    A failing collector must never abort the whole capture, so unexpected
    exceptions are turned into an ``error`` section carrying the message.

    Args:
        collectors: Ordered collectors to run.

    Returns:
        Mapping of collector name to its serialized section.
    """
    state: dict[str, dict[str, object]] = {}
    for collector in collectors:
        try:
            result = collector.collect()
        except Exception as exc:
            section: dict[str, object] = {"status": CollectorStatus.ERROR.value, "error": str(exc)}
        else:
            section = {"status": result.status.value, "error": result.error}
            section.update(result.data)
        state[collector.name] = section
    return state
