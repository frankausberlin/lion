"""Collector for physical network interfaces from sysfs.

Only ``/sys/class/net`` is read (no external tools, no root). Interfaces
without a ``device`` symlink are virtual or ephemeral (``lo``, ``docker0``,
``veth*``, bridges) and churn between scans, so they are excluded. Only
permanent hardware addresses are recorded: ``addr_assign_type`` must report the
address as permanent, otherwise the MAC stays empty. Random or software-assigned
addresses (MAC randomization) change between scans and would churn the state.
No IP addresses, link state or MTU are recorded: those are runtime facts.
"""

import os
from dataclasses import asdict, dataclass

from lion.state.collector import Collector, CollectorResult, CollectorStatus

SYSFS_NET = "/sys/class/net"

#: ``addr_assign_type`` value for a permanent hardware address (``NET_ADDR_PERM``
#: in ``linux/netdevice.h``). Any other value, or a missing file, means the
#: address is dynamic or unproven, so it is not recorded.
PERMANENT_ADDRESS = "0"


@dataclass(frozen=True)
class InterfaceState:
    """One physical network interface and its stable link facts."""

    name: str
    mac: str
    type: int
    driver: str


@dataclass(frozen=True)
class NetworkState:
    """The physical network interfaces of the host."""

    interfaces: list[InterfaceState]


def _read_text(path: str) -> str:
    """Return stripped file contents, or an empty string when unreadable."""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _read_type(entry_path: str) -> int:
    """Return the ARPHRD interface type, or ``0`` when unreadable."""
    try:
        return int(_read_text(os.path.join(entry_path, "type")))
    except ValueError:
        return 0


def _read_driver(entry_path: str) -> str:
    """Return the bound kernel driver module name, or ``""`` when unbound."""
    driver_path = os.path.join(entry_path, "device", "driver")
    if not os.path.exists(driver_path):
        return ""
    return os.path.basename(os.path.realpath(driver_path))


def _read_mac(entry_path: str) -> str:
    """Return the permanent hardware address, or ``""`` for a dynamic one."""
    if _read_text(os.path.join(entry_path, "addr_assign_type")) != PERMANENT_ADDRESS:
        return ""
    return _read_text(os.path.join(entry_path, "address"))


def _read_interfaces() -> list[InterfaceState] | None:
    """Read every physical interface, sorted by name.

    Returns:
        The interfaces, or ``None`` when ``/sys/class/net`` is unavailable.
    """
    try:
        entries = os.scandir(SYSFS_NET)
    except OSError:
        return None
    interfaces: list[InterfaceState] = []
    with entries:
        for entry in entries:
            device_path = os.path.join(entry.path, "device")
            if not os.path.exists(device_path):
                continue
            interfaces.append(
                InterfaceState(
                    name=entry.name,
                    mac=_read_mac(entry.path),
                    type=_read_type(entry.path),
                    driver=_read_driver(entry.path),
                )
            )
    return sorted(interfaces, key=lambda interface: interface.name)


def _collect() -> CollectorResult:
    """Collect physical network interfaces without failing the capture."""
    interfaces = _read_interfaces()
    if interfaces is None:
        return CollectorResult(
            status=CollectorStatus.UNAVAILABLE,
            data=asdict(NetworkState(interfaces=[])),
            error="network: /sys/class/net is unavailable",
        )
    return CollectorResult(status=CollectorStatus.OK, data=asdict(NetworkState(interfaces=interfaces)))


COLLECTOR = Collector(name="network", collect=_collect)
