"""Watch sources and their findings."""

from __future__ import annotations

from lion.shlib.layout import ShlibLayout
from lion.watch.dash import collect_dash_findings
from lion.watch.model import WatchFinding

__all__ = ["WatchFinding", "collect_findings"]


def collect_findings(layout: ShlibLayout) -> tuple[WatchFinding, ...]:
    """Collect findings from all watch sources.

    Args:
        layout: Paths below the selected home directory.

    Returns:
        Findings sorted by source and name.

    Raises:
        WatchError: If a configured source directory cannot be scanned.
    """
    return collect_dash_findings(layout)
