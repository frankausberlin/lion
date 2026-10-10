"""Shared list identities for history equality and displayed differences."""

from collections.abc import Mapping
from typing import cast

#: Unordered lists of unique, nonempty strings, keyed by full field path.
STRING_LISTS = frozenset({"packages.manual", "packages.auto", "packages.held"})

#: Unordered lists of mapping entries, keyed by full field path to identity field.
IDENTITY_KEYS = {
    "hardware.gpu": "pci_id",
    "network.interfaces": "name",
    "services.units": "name",
    "containers.containers": "name",
    "containers.volumes": "name",
    "containers.networks": "name",
}

#: Unordered lists whose identity is computed from their fields instead of a
#: single identity key (an image is ``repository:tag`` or ``repository@digest``).
COMPUTED_IDENTITIES = frozenset({"containers.images"})

#: Docker/Podman mark a missing tag or digest with this placeholder.
NONE_PLACEHOLDER = "<none>"


def _image_ref(value: object) -> str | None:
    """Return the stable reference that identifies one container image.

    A tagged image is ``repository:tag``; an untagged image (empty or ``<none>``
    tag) is identified by its digest as ``repository@digest``. A bare repository
    with neither is the last fallback.
    """
    if not isinstance(value, dict):
        return None
    entry = cast("Mapping[str, object]", value)
    repository = entry.get("repository")
    if not isinstance(repository, str) or not repository:
        return None
    tag = entry.get("tag")
    if isinstance(tag, str) and tag and tag != NONE_PLACEHOLDER:
        return f"{repository}:{tag}"
    digest = entry.get("digest")
    if isinstance(digest, str) and digest:
        return f"{repository}@{digest}"
    return repository


def _identity(path: str, value: object) -> str | None:
    """Return the list identity for one value, or ``None`` when unavailable."""
    if path in STRING_LISTS:
        return value if isinstance(value, str) else None
    if path in COMPUTED_IDENTITIES:
        return _image_ref(value)
    if not isinstance(value, dict):
        return None
    key = IDENTITY_KEYS[path]
    identity = cast("Mapping[str, object]", value).get(key)
    return identity if isinstance(identity, str) else None


def list_items(path: str, values: list[object]) -> dict[str, object] | None:
    """Index a known unordered list only when every identity is unambiguous.

    Package selections contain unique strings; GPU, network, service and
    container lists contain unique, nonempty identity values. Other lists,
    duplicates and malformed identities retain their complete, ordered value
    for both equality and diffing.

    Args:
        path: Full collector field path.
        values: The list to index.

    Returns:
        Items keyed by identity, or ``None`` for an atomic list comparison.
    """
    if path not in STRING_LISTS and path not in IDENTITY_KEYS and path not in COMPUTED_IDENTITIES:
        return None
    indexed: dict[str, object] = {}
    for value in values:
        identity = _identity(path, value)
        if not identity or identity in indexed:
            return None
        indexed[identity] = value
    return indexed
