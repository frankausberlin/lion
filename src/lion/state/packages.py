"""Collector for installed Debian packages and apt-mark selections."""

from dataclasses import asdict, dataclass
from pathlib import Path

from lion.state.collector import Collector, CollectorResult, CollectorStatus
from lion.state.tools import run_tool

DPKG_STATUS = Path("/var/lib/dpkg/status")
TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class PackagesState:
    """Installed package versions plus apt-mark selections."""

    installed: dict[str, str]
    manual: list[str]
    auto: list[str]
    held: list[str]


def _read_installed() -> dict[str, str] | None:
    """Parse the Dpkg database, or ``None`` when it cannot be read."""
    try:
        text = DPKG_STATUS.read_text(encoding="utf-8")
    except OSError:
        return None
    installed: dict[str, str] = {}
    for block in text.split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            # Indented lines continue the preceding field (usually Description).
            if line.startswith((" ", "\t")):
                continue
            key, separator, value = line.partition(":")
            if separator:
                fields[key.strip()] = value.strip()
        status = fields.get("Status", "").split()
        if len(status) != 3 or status[2] != "installed":
            continue
        name = fields.get("Package")
        version = fields.get("Version")
        if name and version:
            architecture = fields.get("Architecture")
            identity = f"{name}:{architecture}" if architecture else name
            installed[identity] = version
    return installed


def _apt_mark(flag: str) -> list[str] | None:
    """Return a sorted selection from ``apt-mark``, or ``None`` if absent."""
    output = run_tool(["apt-mark", flag], timeout=TIMEOUT_SECONDS)
    if output is None:
        return None
    return sorted({line.strip() for line in output.splitlines() if line.strip()})


def _collect() -> CollectorResult:
    """Collect package state without aborting when Dpkg or apt-mark is missing."""
    installed = _read_installed()
    manual = _apt_mark("showmanual")
    auto = _apt_mark("showauto")
    held = _apt_mark("showhold")
    if installed is None or manual is None or auto is None or held is None:
        empty = PackagesState(installed={}, manual=[], auto=[], held=[])
        return CollectorResult(
            status=CollectorStatus.UNAVAILABLE,
            data=asdict(empty),
            error="dpkg status or apt-mark is unavailable",
        )
    state = PackagesState(installed=installed, manual=manual, auto=auto, held=held)
    return CollectorResult(status=CollectorStatus.OK, data=asdict(state))


COLLECTOR = Collector(name="packages", collect=_collect)
