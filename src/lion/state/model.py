"""The persisted LION state snapshot and its validation."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TypeGuard, cast

from lion.state.collector import CollectorStatus

SCHEMA_VERSION = 1
VALID_STATUSES = frozenset(status.value for status in CollectorStatus)

# ``/proc/meminfo`` MemTotal can wobble by a few KiB between scans for purely
# technical reasons (firmware reservations, driver allocations). That wobble is
# not a hardware change. Only this one field gets a tolerance; every other
# number, including GPU memory, is compared exactly.
MEMORY_TOTAL_PATH = "hardware.memory_total_bytes"
MEMORY_TOTAL_TOLERANCE_BYTES = 1024 * 1024


def canonical_collectors(collectors: Mapping[str, Mapping[str, object]]) -> str:
    """Return the canonical comparison form of a collector section.

    Keys are sorted and the encoding is compact so that two states are equal
    exactly when their canonical strings match. ``erstscan`` and
    ``zuletzt_bestaetigt`` are not part of this value; per-collector
    ``status``/``error`` are.
    """
    return json.dumps(collectors, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def memory_total_equal(old: object, new: object) -> bool:
    """Compare two ``MemTotal`` readings with the RAM-only tolerance.

    Two valid (positive) integer readings are equal when they differ by at most
    :data:`MEMORY_TOTAL_TOLERANCE_BYTES`. Anything else (a missing or invalid
    reading such as ``0``, or a non-integer) is only equal to an identical
    value, so the switch between a valid reading and ``0`` stays visible.

    Args:
        old: The previously stored memory total.
        new: The freshly read memory total.

    Returns:
        Whether the two readings count as the same hardware fact.
    """
    if _is_int(old) and _is_int(new) and old > 0 and new > 0:
        return abs(old - new) <= MEMORY_TOTAL_TOLERANCE_BYTES
    return type(old) is type(new) and old == new


def value_equal(path: str, old: object, new: object) -> bool:
    """Return whether two state values are equal under the comparison rules.

    Only :data:`MEMORY_TOTAL_PATH` uses a tolerance; every other value is
    compared exactly. ``status`` and ``scan`` share this rule.

    Args:
        path: The full dotted path of the value, e.g. ``hardware.memory_total_bytes``.
        old: The previously stored value.
        new: The freshly collected value.

    Returns:
        Whether the two values count as equal.
    """
    if path == MEMORY_TOTAL_PATH:
        return memory_total_equal(old, new)
    return json.dumps(old, sort_keys=True) == json.dumps(new, sort_keys=True)


def _as_mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, dict):
        return cast("Mapping[str, object]", value)
    return None


def _mapping_equal(old: Mapping[str, object], new: Mapping[str, object], prefix: str) -> bool:
    if set(old) != set(new):
        return False
    for key, old_value in old.items():
        new_value = new[key]
        path = f"{prefix}.{key}" if prefix else key
        old_map = _as_mapping(old_value)
        new_map = _as_mapping(new_value)
        if old_map is not None and new_map is not None:
            if not _mapping_equal(old_map, new_map, path):
                return False
        elif not value_equal(path, old_value, new_value):
            return False
    return True


def collectors_equal(
    old: Mapping[str, Mapping[str, object]],
    new: Mapping[str, Mapping[str, object]],
) -> bool:
    """Return whether two collector sections describe the same machine state.

    Equality is the exact canonical match, or a difference confined to
    ``hardware.memory_total_bytes`` within the RAM tolerance. This is the single
    rule shared by ``status`` and the ``scan`` history decision, so both agree.

    Args:
        old: The previously stored collector sections.
        new: The freshly collected collector sections.

    Returns:
        Whether the two sections count as the same state.
    """
    if canonical_collectors(old) == canonical_collectors(new):
        return True
    return _mapping_equal(old, new, "")


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a table")
    return cast("Mapping[str, object]", value)


def _require_str(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _validate_timestamp(value: str, key: str) -> None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{key} is not a valid timestamp: {value!r}") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"{key} must include a timezone offset")


def _validate_value(value: object, label: str) -> None:
    if isinstance(value, bool | int | float | str):
        return
    if isinstance(value, list):
        for index, item in enumerate(cast("list[object]", value)):
            _validate_value(item, f"{label}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in cast("Mapping[str, object]", value).items():
            _validate_value(item, f"{label}.{key}")
        return
    raise ValueError(f"{label} has an unsupported value type: {type(value).__name__}")


def _validate_section(name: str, value: object) -> dict[str, object]:
    section = _require_mapping(value, f"collectors.{name}")
    status = _require_str(section, "status")
    if status not in VALID_STATUSES:
        raise ValueError(f"collectors.{name}.status has unknown value: {status!r}")
    error = section.get("error", "")
    if not isinstance(error, str):
        raise ValueError(f"collectors.{name}.error must be a string")
    for key, item in section.items():
        _validate_value(item, f"collectors.{name}.{key}")
    return dict(section)


@dataclass(frozen=True)
class Snapshot:
    """A complete, distinct machine state stored in the LION history."""

    erstscan: str
    zuletzt_bestaetigt: str
    collectors: dict[str, dict[str, object]]
    schema_version: int = SCHEMA_VERSION

    def to_toml_dict(self) -> dict[str, object]:
        """Return the TOML/JSON representation with scalars before tables."""
        return {
            "schema_version": self.schema_version,
            "erstscan": self.erstscan,
            "zuletzt_bestaetigt": self.zuletzt_bestaetigt,
            "collectors": self.collectors,
        }

    def matches(self, collectors: Mapping[str, Mapping[str, object]]) -> bool:
        """Return whether these stored collectors match a fresh collection.

        Only the collector data counts; ``erstscan`` and ``zuletzt_bestaetigt``
        are excluded, while per-collector ``status``/``error`` participate. The
        comparison follows :func:`collectors_equal`, including the RAM tolerance.
        """
        return collectors_equal(self.collectors, collectors)

    @classmethod
    def from_toml_dict(cls, data: Mapping[str, object]) -> "Snapshot":
        """Validate and build a snapshot from parsed TOML data.

        Args:
            data: Parsed TOML mapping.

        Returns:
            The validated snapshot.

        Raises:
            ValueError: If the schema version, timestamps, or collector
                sections are invalid.
        """
        version = data.get("schema_version")
        if not isinstance(version, int) or isinstance(version, bool) or version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {version!r}")
        erstscan = _require_str(data, "erstscan")
        zuletzt_bestaetigt = _require_str(data, "zuletzt_bestaetigt")
        _validate_timestamp(erstscan, "erstscan")
        _validate_timestamp(zuletzt_bestaetigt, "zuletzt_bestaetigt")
        raw_collectors = _require_mapping(data.get("collectors"), "collectors")
        collectors = {name: _validate_section(name, section) for name, section in raw_collectors.items()}
        return cls(
            erstscan=erstscan,
            zuletzt_bestaetigt=zuletzt_bestaetigt,
            collectors=collectors,
            schema_version=version,
        )
