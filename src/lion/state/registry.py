"""Ordered registry of the collectors LION runs for every scan."""

from lion.state import hardware, host, packages
from lion.state.collector import Collector

COLLECTORS: tuple[Collector, ...] = (
    host.COLLECTOR,
    hardware.COLLECTOR,
    packages.COLLECTOR,
)
