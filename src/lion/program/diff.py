"""Recursive diffing and terminal rendering of two collector states."""

import json
from collections.abc import Mapping
from typing import cast

from lion.state.model import value_equal

CollectorDiff = dict[str, dict[str, dict[str, object]]]

# Tables inside a list are matched element-wise when they share this identity
# key, so swapping a GPU (a new ``pci_id``) reads as removed + added rather than
# one opaque value change.
_LIST_IDENTITY_KEY = "pci_id"
_VALUE_IDENTITY = "value"


def _as_mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, dict):
        return cast("Mapping[str, object]", value)
    return None


def _as_list(value: object) -> list[object] | None:
    if isinstance(value, list):
        return cast("list[object]", value)
    return None


def _is_scalar(value: object) -> bool:
    return value is None or isinstance(value, bool | int | float | str)


def _identity_of(value: object) -> str:
    mapping = _as_mapping(value)
    if mapping is None:
        return ""
    identity = mapping.get(_LIST_IDENTITY_KEY)
    return identity if isinstance(identity, str) else ""


def _is_identity_mapping(value: object) -> bool:
    return bool(_identity_of(value))


def _list_identity(old: list[object], new: list[object]) -> str | None:
    """Classify a pair of lists for element-wise comparison.

    Returns the identity key for table lists, ``"value"`` for scalar lists, and
    ``None`` when the lists must be compared atomically.
    """
    items = old + new
    if not items:
        return None
    if all(_is_identity_mapping(item) for item in items):
        return _LIST_IDENTITY_KEY
    if all(_is_scalar(item) for item in items):
        return _VALUE_IDENTITY
    return None


def _contains(values: list[object], candidate: object) -> bool:
    """Membership that keeps scalar types distinct (``True`` is not ``1``)."""
    return any(type(value) is type(candidate) and value == candidate for value in values)


def _unique(values: list[object]) -> list[object]:
    """Deduplicate scalars while preserving order and exact types."""
    unique: list[object] = []
    for value in values:
        if not _contains(unique, value):
            unique.append(value)
    return unique


def _tidy(
    added: dict[str, object],
    removed: dict[str, object],
    changed: dict[str, object],
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    if added:
        result["added"] = added
    if removed:
        result["removed"] = removed
    if changed:
        result["changed"] = changed
    return result


def _absorb(
    fragment: Mapping[str, Mapping[str, object]],
    added: dict[str, object],
    removed: dict[str, object],
    changed: dict[str, object],
) -> None:
    added.update(fragment.get("added", {}))
    removed.update(fragment.get("removed", {}))
    changed.update(fragment.get("changed", {}))


def _diff_list(
    old: list[object],
    new: list[object],
    path: str,
    collector: str,
) -> dict[str, dict[str, object]]:
    """Compare two lists, element-wise when they carry a usable identity.

    Table lists sharing ``pci_id`` are matched per element (keys like
    ``gpu[0000:01:00.0]``); scalar lists are compared per value (keys like
    ``manual[zsh]``); every other list stays one atomic leaf value.
    """
    added: dict[str, object] = {}
    removed: dict[str, object] = {}
    changed: dict[str, object] = {}
    identity = _list_identity(old, new)
    if identity == _LIST_IDENTITY_KEY:
        old_by = {_identity_of(item): item for item in old}
        new_by = {_identity_of(item): item for item in new}
        for key in sorted(set(old_by) | set(new_by)):
            item_path = f"{path}[{key}]"
            if key not in old_by:
                added[item_path] = new_by[key]
            elif key not in new_by:
                removed[item_path] = old_by[key]
            else:
                old_item = _as_mapping(old_by[key]) or {}
                new_item = _as_mapping(new_by[key]) or {}
                _absorb(_diff_mapping(old_item, new_item, item_path, collector), added, removed, changed)
    elif identity == _VALUE_IDENTITY:
        old_values = _unique(old)
        new_values = _unique(new)
        for value in new_values:
            if not _contains(old_values, value):
                added[f"{path}[{value}]"] = value
        for value in old_values:
            if not _contains(new_values, value):
                removed[f"{path}[{value}]"] = value
    elif not value_equal(f"{collector}.{path}", old, new):
        changed[path] = {"old": old, "new": new}
    return _tidy(added, removed, changed)


def _diff_mapping(
    old: Mapping[str, object],
    new: Mapping[str, object],
    prefix: str,
    collector: str,
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
            old_list = _as_list(old_value)
            new_list = _as_list(new_value)
            if old_list is not None and new_list is not None:
                _absorb(_diff_list(old_list, new_list, path, collector), added, removed, changed)
                continue
            old_map = _as_mapping(old_value)
            new_map = _as_mapping(new_value)
            if old_map is not None and new_map is not None:
                _absorb(_diff_mapping(old_map, new_map, path, collector), added, removed, changed)
            elif not value_equal(f"{collector}.{path}", old_value, new_value):
                changed[path] = {"old": old_value, "new": new_value}
    return _tidy(added, removed, changed)


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
            delta = _diff_mapping(old[name], new[name], "", name)
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


def has_structural_change(diff: Mapping[str, Mapping[str, object]]) -> bool:
    """Return whether a diff contains a structural change.

    A structural change adds or removes a collector, key or list entry; a pure
    value change only appears under ``changed``.
    """
    return any(_category(section, "added") or _category(section, "removed") for section in diff.values())


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
