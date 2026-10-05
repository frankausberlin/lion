"""Collector for host identity and operating system details."""

import platform
from dataclasses import asdict, dataclass

from repolion.state.collector import Collector, CollectorResult, CollectorStatus

UNKNOWN = "Unknown"


@dataclass(frozen=True)
class HostState:
    """Static identity of the host and its operating system."""

    hostname: str
    distribution: str
    distribution_version: str
    kernel: str
    architecture: str


def _read_os_release() -> dict[str, str]:
    """Read distribution metadata using the standard library."""
    try:
        return platform.freedesktop_os_release()
    except OSError:
        return {}


def _collect() -> CollectorResult:
    """Collect host identity without ever failing the whole capture."""
    os_release = _read_os_release()
    state = HostState(
        hostname=platform.node() or UNKNOWN,
        distribution=os_release.get("NAME", UNKNOWN),
        distribution_version=os_release.get("VERSION_ID", UNKNOWN),
        kernel=platform.release() or UNKNOWN,
        architecture=platform.machine() or UNKNOWN,
    )
    return CollectorResult(status=CollectorStatus.OK, data=asdict(state))


COLLECTOR = Collector(name="host", collect=_collect)
