"""The persisted LION state snapshot and its validation."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from repolion.state.collector import CollectorStatus

SCHEMA_VERSION = 1
VALID_STATUSES = frozenset(status.value for status in CollectorStatus)


def canonical_collectors(collectors: Mapping[str, Mapping[str, object]]) -> str:
    """Return the canonical comparison form of a collector section.

    Keys are sorted and the encoding is compact so that two states are equal
    exactly when their canonical strings match. ``erstscan`` and
    ``zuletzt_bestaetigt`` are not part of this value; per-collector
    ``status``/``error`` are.
    """
    return json.dumps(collectors, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


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

    def canonical_collectors(self) -> str:
        """Return the canonical form used to compare two collector sections.

        Only the collector data counts; ``erstscan`` and ``zuletzt_bestaetigt``
        are excluded, while per-collector ``status``/``error`` participate.
        """
        return canonical_collectors(self.collectors)

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
