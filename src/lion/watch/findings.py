"""Watch sources and their findings."""

from __future__ import annotations

from lion.shlib.layout import ShlibLayout
from lion.watch.dash import collect_dash_findings
from lion.watch.model import WatchFinding

__all__ = ["WatchFinding", "collect_findings"]


def collect_findings(
    layout: ShlibLayout,
    *,
    render_diffs: bool = True,
    max_bytes: int | None = None,
) -> tuple[WatchFinding, ...]:
    """Collect findings from all watch sources.

    Args:
        layout: Paths below the selected home directory.
        render_diffs: Whether changed entries include their diff body.
        max_bytes: Optional size limit for content that is compared.

    Returns:
        Findings sorted by source and name.

    Raises:
        WatchError: If a configured source directory cannot be scanned.
    """
    return collect_dash_findings(layout, render_diffs=render_diffs, max_bytes=max_bytes)
