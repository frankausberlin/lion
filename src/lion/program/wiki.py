"""The derived OKF v0.2 wiki that every ``scan`` regenerates.

The wiki lives under ``$XDG_DATA_HOME/lion/wiki``: one Markdown page per
registered collector plus ``index.md``. It is a pure derived artifact — never
edited by hand, never merged — and ``scan`` is the only writer. Rendering is a
pure function of the validated snapshot and its reference, so the same snapshot
always yields byte-identical files.
"""

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from lion.program.checks import CheckStatus, DoctorContext, Finding
from lion.program.storage import HistoryError, get_data_dir, list_entries
from lion.state.model import Snapshot
from lion.state.registry import COLLECTORS

#: OKF format version written into every page's frontmatter.
OKF_VERSION = "0.2"

#: The identity palette (token -> hex) used for wiki keys and UI accents.
PALETTE: dict[str, str] = {
    "slate": "#64748B",
    "blue": "#3B82F6",
    "violet": "#8B5CF6",
    "teal": "#14B8A6",
    "amber": "#F59E0B",
    "green": "#22C55E",
    "orange": "#F97316",
    "cyan": "#06B6D4",
    "rose": "#F43F5E",
}

#: Stable identity (index and collectors) -> palette token.
IDENTITY_COLORS: dict[str, str] = {
    "index": "slate",
    "host": "blue",
    "hardware": "violet",
    "network": "teal",
    "packages": "amber",
    "services": "green",
    "containers": "orange",
    "tools": "cyan",
    "dash": "rose",
}

#: The wiki entry page; the OKF navigation root.
INDEX_PAGE = "index.md"


def get_wiki_dir() -> Path:
    """Return the derived wiki directory."""
    return get_data_dir() / "wiki"


def _one_line(value: str) -> str:
    """Collapse a string to one line so no value can break the fact format."""
    text = " ".join(value.split())
    return text if text else '""'


def _json(value: object) -> str:
    """Render a nested value as compact, single-line JSON."""
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _scalar(value: object) -> str:
    """Render a scalar fact value: booleans as ``true``/``false``, strings one line."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _one_line(value)
    if isinstance(value, int | float):
        return str(value)
    return _json(value)


def _cell(value: object) -> str:
    """Render one ``key=value`` cell; nested lists and mappings become JSON."""
    if isinstance(value, list):
        return _json(cast("list[object]", value))
    if isinstance(value, dict):
        return _json(cast("Mapping[str, object]", value))
    return _scalar(value)


def _table_item(item: Mapping[str, object]) -> str:
    """Render one list-of-tables entry as ``key=value, …``."""
    return ", ".join(f"{key}={_cell(value)}" for key, value in item.items())


def _fact_lines(section: Mapping[str, object]) -> list[str]:
    """Render one collector section as ``- **field**: value`` lines.

    ``status`` is skipped (it already lives in the frontmatter) and ``error``
    only appears when it is non-empty. Lists render as sub-bullets: table
    entries as ``key=value`` pairs, plain entries as their value; an empty list
    is ``(none)``.
    """
    lines: list[str] = []
    for key, value in section.items():
        if key == "status" or (key == "error" and value == ""):
            continue
        if isinstance(value, list) and value:
            lines.append(f"- **{key}**:")
            for item in cast("list[object]", value):
                if isinstance(item, dict):
                    lines.append(f"  - {_table_item(cast('Mapping[str, object]', item))}")
                else:
                    lines.append(f"  - {_scalar(item)}")
        elif isinstance(value, list):
            lines.append(f"- **{key}**: (none)")
        else:
            lines.append(f"- **{key}**: {_scalar(value)}")
    return lines


def _section_status(section: Mapping[str, object] | None) -> str:
    """Return the collector status; a missing section is ``unavailable``."""
    if section is not None:
        raw = section.get("status")
        if isinstance(raw, str) and raw:
            return raw
    return "unavailable"


def _collector_page(name: str, ref: str, section: Mapping[str, object] | None) -> str:
    """Render one collector page with its fixed frontmatter key order."""
    title = name.capitalize()
    lines = [
        "---",
        f'okf_version: "{OKF_VERSION}"',
        f"snapshot_ref: {ref}",
        f"collector: {name}",
        f"status: {_section_status(section)}",
        f"color: {IDENTITY_COLORS[name]}",
        f"title: {title}",
        "---",
        "",
        f"# {title}",
        "",
        "## Facts",
    ]
    if section is not None:
        lines.extend(_fact_lines(section))
    return "\n".join(lines) + "\n"


def _index_page(snapshot: Snapshot, ref: str) -> str:
    """Render the wiki index: snapshot facts and relative collector links."""
    lines = [
        "---",
        f'okf_version: "{OKF_VERSION}"',
        f"snapshot_ref: {ref}",
        f"color: {IDENTITY_COLORS['index']}",
        "title: LION system wiki",
        "---",
        "",
        "# LION system wiki",
        "",
        f"- **created_at**: {_one_line(snapshot.created_at)}",
        f"- **confirmed_at**: {_one_line(snapshot.confirmed_at)}",
        "",
        "## Collectors",
        "",
    ]
    lines.extend(f"- [{collector.name}]({collector.name}.md)" for collector in COLLECTORS)
    return "\n".join(lines) + "\n"


def render_pages(snapshot: Snapshot, ref: str) -> dict[str, str]:
    """Render every wiki page from a validated snapshot.

    This is a pure function: the same snapshot and reference always produce
    byte-identical pages. Collector pages come first in registry order,
    ``index.md`` last; pages are keyed by their file name.

    Args:
        snapshot: The validated state that was just stored.
        ref: The snapshot reference (the history file name without ``.toml``).

    Returns:
        Mapping of file name to complete page text.
    """
    pages: dict[str, str] = {}
    for collector in COLLECTORS:
        section = snapshot.collectors.get(collector.name)
        pages[f"{collector.name}.md"] = _collector_page(collector.name, ref, section)
    pages[INDEX_PAGE] = _index_page(snapshot, ref)
    return pages


def _replace(path: Path, content: str) -> Path:
    """Write one page via a temporary file, ``fsync`` and atomic ``os.replace``."""
    try:
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                _ = stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    except OSError as exc:
        raise OSError(f"Cannot write wiki page '{path}': {exc}") from exc
    return path


def write_pages(pages: Mapping[str, str]) -> list[Path]:
    """Write the rendered pages, replacing each file atomically.

    The wiki directory is created on demand. Pages are published in the given
    order (collector pages first, ``index.md`` last), so an interrupted run can
    leave a mixed state at worst, never a half-written file; the next ``scan``
    heals it.

    Args:
        pages: Mapping of file name to complete page text.

    Returns:
        The written page paths in publication order.

    Raises:
        OSError: If the directory or one page cannot be written, naming the path.
    """
    wiki_dir = get_wiki_dir()
    try:
        wiki_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(f"Cannot create wiki directory '{wiki_dir}': {exc}") from exc
    return [_replace(wiki_dir / name, content) for name, content in pages.items()]


def _read_snapshot_ref(index: Path) -> str | None:
    """Read ``snapshot_ref`` from the index frontmatter, or ``None``."""
    try:
        text = index.read_text(encoding="utf-8")
    except OSError:
        return None
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None
    for line in lines[1:]:
        if line == "---":
            break
        key, separator, value = line.partition(":")
        if separator and key.strip() == "snapshot_ref":
            return value.strip() or None
    return None


def wiki_checks(_ctx: DoctorContext) -> list[Finding]:
    """Check that the derived wiki matches the latest stored state (read-only).

    Without a history (or with an unreadable one) the check is a neutral
    ``skip``, like ``dash`` without its directory. Warn findings carry the
    ``'lion scan' regenerates it.`` hint; the check itself never writes.
    """
    try:
        entries = list_entries()
    except HistoryError:
        return [
            Finding(
                topic="wiki",
                name="wiki.freshness",
                status=CheckStatus.SKIP,
                message="History unreadable; see the history topic.",
            )
        ]
    if not entries:
        return [
            Finding(
                topic="wiki",
                name="wiki.freshness",
                status=CheckStatus.SKIP,
                message="No history present.",
            )
        ]
    latest = entries[-1]
    index = get_wiki_dir() / INDEX_PAGE
    if not index.exists():
        return [
            Finding(
                topic="wiki",
                name="wiki.freshness",
                status=CheckStatus.WARN,
                message=f"Wiki is missing for the latest state ({latest.ref}).",
                hint="'lion scan' regenerates it.",
            )
        ]
    ref = _read_snapshot_ref(index)
    if ref is None:
        return [
            Finding(
                topic="wiki",
                name="wiki.freshness",
                status=CheckStatus.WARN,
                message=f"Wiki index is unreadable: {index}",
                hint="'lion scan' regenerates it.",
            )
        ]
    if ref != latest.ref:
        return [
            Finding(
                topic="wiki",
                name="wiki.freshness",
                status=CheckStatus.WARN,
                message=f"Wiki is stale (ref {ref}, latest {latest.ref}).",
                hint="'lion scan' regenerates it.",
            )
        ]
    return [
        Finding(
            topic="wiki",
            name="wiki.freshness",
            status=CheckStatus.OK,
            message=f"Wiki matches the latest state ({latest.ref}).",
        )
    ]
