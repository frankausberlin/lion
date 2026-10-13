"""The shared collector index rendered by ``scan`` and ``status``.

Both commands show the same one-line-per-collector overview in text mode: a
semantic bullet (red for ``error``, yellow for ``unavailable``, blue for
changed/new, green for unchanged), the collector name — in a TTY an OSC-8
hyperlink to its wiki page — and the change against the stored baseline.
Non-TTY output and ``NO_COLOR`` carry no escape sequences at all;
``scan --json``/``status --json`` never use this module.
"""

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from lion.program.diff import CollectorDiff
from lion.state.registry import COLLECTORS

#: Semantic ANSI colors; the wiki identity palette is a separate concern.
_RED = "\x1b[31m"
_YELLOW = "\x1b[33m"
_BLUE = "\x1b[34m"
_GREEN = "\x1b[32m"
_RESET = "\x1b[0m"

_BULLET = "\u25cf"
_UP = "\u25b2"
_MINUS = "\u2212"


def _is_tty() -> bool:
    """Return whether stdout is an interactive terminal.

    Tests monkeypatch this single indirection instead of the environment.
    """
    return sys.stdout.isatty()


def _escapes_enabled() -> bool:
    """Return whether colors and hyperlinks are allowed: TTY and no ``NO_COLOR``."""
    return _is_tty() and not os.environ.get("NO_COLOR")


def _paint(text: str, color: str) -> str:
    """Wrap text in an ANSI color."""
    return f"{color}{text}{_RESET}"


def _hyperlink(text: str, target: Path) -> str:
    """Wrap text in an OSC-8 hyperlink to the absolute page path."""
    uri = Path(os.path.abspath(target)).as_uri()
    return f"\x1b]8;;{uri}\x1b\\{text}\x1b]8;;\x1b\\"


def _status(section: Mapping[str, object] | None) -> str:
    """Return the collector status; a missing section is ``unavailable``."""
    if section is not None:
        raw = section.get("status")
        if isinstance(raw, str) and raw:
            return raw
    return "unavailable"


def _change(delta: Mapping[str, Mapping[str, object]] | None, is_new: bool) -> str:
    """Return the change label, with counter markers for structural changes."""
    if is_new:
        return "new"
    if not delta:
        return "unchanged"
    label = "changed"
    added = delta.get("added")
    removed = delta.get("removed")
    if added or removed:
        label = f"{label} {_UP}"
        if added:
            label = f"{label} +{len(added)}"
        if removed:
            label = f"{label} {_MINUS}{len(removed)}"
    return label


def _color(status: str, change: str) -> str | None:
    """Return the semantic color: error > unavailable > changed/new > unchanged.

    Only the four documented states carry a color; anything else stays plain.
    """
    if status == "error":
        return _RED
    if status == "unavailable":
        return _YELLOW
    if status != "ok":
        return None
    if change == "new" or change.startswith("changed"):
        return _BLUE
    return _GREEN


def _line(name: str, status: str, change: str, wiki_dir: Path) -> str:
    """Render one index line, with escapes only in TTY text mode."""
    name_cell = name.ljust(10)
    status_cell = status.ljust(11)
    if not _escapes_enabled():
        return f"  {_BULLET} {name_cell}  {status_cell}  {change}"
    linked_name = _hyperlink(name_cell, wiki_dir / f"{name}.md")
    color = _color(status, change)
    if color is None:
        return f"  {_BULLET} {linked_name}  {status_cell}  {change}"
    return f"  {_paint(_BULLET, color)} {linked_name}  {_paint(status_cell, color)}  {change}"


def render_index(
    state: Mapping[str, Mapping[str, object]],
    diff: CollectorDiff | None,
    wiki_dir: Path,
) -> str:
    """Render the shared collector index for ``scan`` and ``status``.

    Args:
        state: The freshly collected collector sections.
        diff: The difference between the stored baseline and ``state``; ``None``
            means there is no baseline yet, so every collector is ``new``.
        wiki_dir: The absolute wiki directory used for the page hyperlinks.

    Returns:
        One line per registered collector in registry order.
    """
    lines = [
        _line(
            collector.name,
            _status(state.get(collector.name)),
            _change(None if diff is None else diff.get(collector.name), diff is None),
            wiki_dir,
        )
        for collector in COLLECTORS
    ]
    return "\n".join(lines)
