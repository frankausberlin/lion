"""Ordered registry of the collectors LION runs for every scan."""

from lion.state import containers, hardware, host, network, packages, services, tooling
from lion.state.collector import Collector

COLLECTORS: tuple[Collector, ...] = (
    host.COLLECTOR,
    hardware.COLLECTOR,
    network.COLLECTOR,
    packages.COLLECTOR,
    services.COLLECTOR,
    containers.COLLECTOR,
    tooling.COLLECTOR,
)
