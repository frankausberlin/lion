"""Shared list identities for history equality and displayed differences."""

from collections.abc import Mapping
from typing import cast

PACKAGE_SELECTIONS = frozenset({"packages.manual", "packages.auto", "packages.held"})


def list_items(path: str, values: list[object]) -> dict[str, object] | None:
    """Index a known unordered list only when every identity is unambiguous.

    Package selections contain unique strings; GPU entries contain unique,
    nonempty PCI slot ids. Other lists, duplicates and malformed identities
    retain their complete, ordered value for both equality and diffing.

    Args:
        path: Full collector field path.
        values: The list to index.

    Returns:
        Items keyed by identity, or ``None`` for an atomic list comparison.
    """
    if path not in PACKAGE_SELECTIONS and path != "hardware.gpu":
        return None
    indexed: dict[str, object] = {}
    for value in values:
        if path in PACKAGE_SELECTIONS:
            identity = value
        elif isinstance(value, dict):
            identity = cast("Mapping[str, object]", value).get("pci_id")
        else:
            return None
        if not isinstance(identity, str) or not identity or identity in indexed:
            return None
        indexed[identity] = value
    return indexed
