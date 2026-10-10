"""Collector for the availability of the external tools LION relies on.

The module is deliberately named ``tooling`` and kept next to the runner
``lion.state.tools``: the runner executes tools, while this collector records
whether they exist. Tool availability is a cross-cutting fact of the machine,
so ``doctor`` can later check it on its own.
"""

import shutil
from dataclasses import asdict, dataclass

from lion.state.collector import Collector, CollectorResult, CollectorStatus

# Normalized tool name -> executable searched on ``PATH``.
TOOLS: tuple[tuple[str, str], ...] = (
    ("lspci", "lspci"),
    ("nvidia_smi", "nvidia-smi"),
    ("rocm_smi", "rocm-smi"),
    ("apt_mark", "apt-mark"),
    ("systemctl", "systemctl"),
    ("docker", "docker"),
    ("podman", "podman"),
    ("zsh", "zsh"),
)


@dataclass(frozen=True)
class ToolsState:
    """Availability of the external tools, keyed by normalized name."""

    available: dict[str, bool]


def _collect() -> CollectorResult:
    """Record which external tools exist.

    A missing tool is data, not a warning: the collector always succeeds,
    because it measures availability successfully.
    """
    available = {name: shutil.which(command) is not None for name, command in TOOLS}
    return CollectorResult(status=CollectorStatus.OK, data=asdict(ToolsState(available=available)))


COLLECTOR = Collector(name="tools", collect=_collect)
