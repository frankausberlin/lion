"""Recursive diffing and terminal rendering of two collector states."""

import json
from collections.abc import Mapping
from typing import cast

CollectorDiff = dict[str, dict[str, dict[str, object]]]


def _as_mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, dict):
        return cast("Mapping[str, object]", value)
    return None


def _diff_mapping(
    old: Mapping[str, object],
    new: Mapping[str, object],
    prefix: str,
) -> dict[str, dict[str, object]]:
    added: dict[str, object] = {}
    removed: dict[str, object] = {}
    changed: dict[str, object] = {}
    for key in sorted(set(old) | set(new)):
        path = f"{prefix}.{key}" if prefix else key
        if key not in old:
            added[path] = new[key]
        elif key not in new:
            removed[path] = old[key]
        else:
            old_value = old[key]
            new_value = new[key]
            old_map = _as_mapping(old_value)
            new_map = _as_mapping(new_value)
            if old_map is not None and new_map is not None:
                for category, entries in _diff_mapping(old_map, new_map, path).items():
                    {"added": added, "removed": removed, "changed": changed}[category].update(entries)
            elif old_value != new_value:
                changed[path] = {"old": old_value, "new": new_value}
    result: dict[str, dict[str, object]] = {}
    if added:
        result["added"] = added
    if removed:
        result["removed"] = removed
    if changed:
        result["changed"] = changed
    return result


def diff_collectors(
    old: Mapping[str, Mapping[str, object]],
    new: Mapping[str, Mapping[str, object]],
) -> CollectorDiff:
    """Compare two collector sections, grouped by collector.

    Args:
        old: The previously stored collector sections.
        new: The freshly collected collector sections.

    Returns:
        Only collectors that differ, each with non-empty ``added``,
        ``removed``, and/or ``changed`` categories keyed by dotted path.
    """
    result: CollectorDiff = {}
    for name in sorted(set(old) | set(new)):
        if name not in old:
            result[name] = {"added": {"(collector)": dict(new[name])}}
        elif name not in new:
            result[name] = {"removed": {"(collector)": dict(old[name])}}
        else:
            delta = _diff_mapping(old[name], new[name], "")
            if delta:
                result[name] = delta
    return result


def _format(value: object) -> str:
    if isinstance(value, str):
        return value or '""'
    return json.dumps(value, sort_keys=True, ensure_ascii=True)


def _category(section: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = section.get(key)
    if isinstance(value, dict):
        return cast("Mapping[str, object]", value)
    return {}


def render(diff: Mapping[str, Mapping[str, object]]) -> str:
    """Render a grouped ``+``/``-``/``~`` diff for the terminal.

    Args:
        diff: A diff produced by :func:`diff_collectors`.

    Returns:
        The rendered text, or an empty string when there is no difference.
    """
    lines: list[str] = []
    for name in sorted(diff):
        lines.append(f"{name}:")
        section = diff[name]
        for path, value in sorted(_category(section, "added").items()):
            lines.append(f"  + {path} = {_format(value)}")
        for path, value in sorted(_category(section, "removed").items()):
            lines.append(f"  - {path} = {_format(value)}")
        for path, value in sorted(_category(section, "changed").items()):
            change = _as_mapping(value)
            old_value = change.get("old") if change is not None else None
            new_value = change.get("new") if change is not None else None
            lines.append(f"  ~ {path}: {_format(old_value)} -> {_format(new_value)}")
    return "\n".join(lines)
