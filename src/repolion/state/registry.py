"""Ordered registry of the collectors LION runs for every scan."""

from repolion.state import hardware, host, packages
from repolion.state.collector import Collector

COLLECTORS: tuple[Collector, ...] = (
    host.COLLECTOR,
    hardware.COLLECTOR,
    packages.COLLECTOR,
)
