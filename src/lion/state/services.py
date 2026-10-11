"""Collector for systemd service unit-file states.

Only the unit-file enablement state is recorded via read-only ``systemctl``
queries. Runtime facts such as failed units or ``is-system-running`` are
deliberately excluded: they churn and belong to a future runtime collector.
Units in the ``transient`` state are skipped as well: they are created through
the runtime API, have no unit file and cannot be enabled. Missing or failing
``systemctl`` marks the collector ``unavailable``.
"""

from dataclasses import asdict, dataclass

from lion.state.collector import Collector, CollectorResult, CollectorStatus
from lion.state.tools import run_tool

TIMEOUT_SECONDS = 10

#: Unit-file states systemd reports; anything else normalizes to ``unknown``.
#: ``transient`` is not listed: such units are runtime-only (no unit file) and
#: are skipped in ``_parse_units`` instead of being recorded.
KNOWN_STATES = frozenset(
    {
        "enabled",
        "enabled-runtime",
        "disabled",
        "masked",
        "masked-runtime",
        "static",
        "indirect",
        "generated",
        "alias",
        "linked",
        "linked-runtime",
        "bad",
    }
)


@dataclass(frozen=True)
class UnitState:
    """One ``*.service`` unit file and its normalized enablement state."""

    name: str
    state: str


@dataclass(frozen=True)
class ServicesState:
    """The service unit files known to systemd."""

    units: list[UnitState]


def _parse_units(output: str) -> list[UnitState]:
    """Parse ``systemctl list-unit-files`` output into sorted service units.

    Units in the ``transient`` state are skipped: they were created through the
    runtime API, have no unit file and cannot be enabled, so they are not part
    of the recorded configuration and would only churn the history. The
    runtime-scoped variants (``enabled-runtime``, ``masked-runtime``,
    ``linked-runtime``) are kept as reported: generators produce the same
    runtime scope (e.g. ``systemd-fsck-root.service``), and no ``systemctl``
    query ignores the runtime scope.
    """
    units: list[UnitState] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[0].endswith(".service"):
            continue
        if parts[1] == "transient":
            continue
        state = parts[1] if parts[1] in KNOWN_STATES else "unknown"
        units.append(UnitState(name=parts[0], state=state))
    return sorted(units, key=lambda unit: unit.name)


def _collect() -> CollectorResult:
    """Collect service unit-file states without aborting the capture."""
    failures: list[str] = []
    output = run_tool(
        ["systemctl", "list-unit-files", "--type=service", "--no-pager", "--no-legend"],
        timeout=TIMEOUT_SECONDS,
        failures=failures,
    )
    if output is None:
        return CollectorResult(
            status=CollectorStatus.UNAVAILABLE,
            data=asdict(ServicesState(units=[])),
            error="; ".join(dict.fromkeys(failures)) or "systemctl is unavailable",
        )
    return CollectorResult(status=CollectorStatus.OK, data=asdict(ServicesState(units=_parse_units(output))))


COLLECTOR = Collector(name="services", collect=_collect)
