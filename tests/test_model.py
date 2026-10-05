"""Tests for the snapshot model and its strict validation."""

from datetime import datetime

import pytest

from repolion.state import model
from repolion.state.model import Snapshot, canonical_collectors


def _data() -> dict[str, object]:
    return {
        "schema_version": 1,
        "erstscan": "2026-10-05T20:00:00+00:00",
        "zuletzt_bestaetigt": "2026-10-05T20:30:00.123456+00:00",
        "collectors": {
            "host": {"status": "ok", "error": "", "hostname": "lion"},
            "hardware": {
                "status": "ok",
                "error": "",
                "gpu": [{"name": "GPU", "driver_version": "1.0", "memory_total_bytes": 1024}],
            },
        },
    }


def test_round_trip() -> None:
    """A valid mapping builds a snapshot that serializes back unchanged."""
    snapshot = Snapshot.from_toml_dict(_data())
    assert snapshot.schema_version == model.SCHEMA_VERSION
    assert snapshot.to_toml_dict() == _data()


def test_canonical_order_independent() -> None:
    """Key order does not change the canonical comparison string."""
    first = {"host": {"status": "ok", "hostname": "lion"}}
    second = {"host": {"hostname": "lion", "status": "ok"}}
    assert canonical_collectors(first) == canonical_collectors(second)


def test_canonical_status_counts() -> None:
    """A collector status change is a data change."""
    ok = {"host": {"status": "ok", "hostname": "lion"}}
    error = {"host": {"status": "error", "hostname": "lion"}}
    assert canonical_collectors(ok) != canonical_collectors(error)


@pytest.mark.parametrize("version", [2, 0, "1", True, None])
def test_invalid_schema_version(version: object) -> None:
    """Reject missing, unknown, or non-integer schema versions."""
    data = _data()
    data["schema_version"] = version
    with pytest.raises(ValueError, match="schema_version"):
        Snapshot.from_toml_dict(data)


@pytest.mark.parametrize("key", ["erstscan", "zuletzt_bestaetigt"])
def test_non_string_timestamp(key: str) -> None:
    """Timestamps must be strings before they can be parsed."""
    data = _data()
    data[key] = 5
    with pytest.raises(ValueError, match=f"{key} must be a string"):
        Snapshot.from_toml_dict(data)


@pytest.mark.parametrize("key", ["erstscan", "zuletzt_bestaetigt"])
def test_naive_timestamp(key: str) -> None:
    """Every timestamp must carry a UTC offset."""
    data = _data()
    data[key] = "2026-10-05T20:00:00"
    with pytest.raises(ValueError, match="timezone"):
        Snapshot.from_toml_dict(data)


@pytest.mark.parametrize("key", ["erstscan", "zuletzt_bestaetigt"])
def test_malformed_timestamp(key: str) -> None:
    """A timestamp that cannot be parsed is rejected."""
    data = _data()
    data[key] = "not-a-timestamp"
    with pytest.raises(ValueError, match="not a valid timestamp"):
        Snapshot.from_toml_dict(data)


def test_missing_collectors() -> None:
    """The collectors table is mandatory."""
    data = _data()
    del data["collectors"]
    with pytest.raises(ValueError, match="collectors must be a table"):
        Snapshot.from_toml_dict(data)


def test_section_must_be_table() -> None:
    """Every collector section must be a table."""
    data = _data()
    data["collectors"] = {"host": "nope"}
    with pytest.raises(ValueError, match=r"collectors\.host must be a table"):
        Snapshot.from_toml_dict(data)


def test_unknown_status() -> None:
    """Only the documented status values are accepted."""
    data = _data()
    data["collectors"] = {"host": {"status": "maybe", "error": ""}}
    with pytest.raises(ValueError, match="unknown value"):
        Snapshot.from_toml_dict(data)


def test_error_must_be_string() -> None:
    """The error field must be a string when present."""
    data = _data()
    data["collectors"] = {"host": {"status": "ok", "error": 5}}
    with pytest.raises(ValueError, match=r"collectors\.host\.error must be a string"):
        Snapshot.from_toml_dict(data)


def test_unsupported_value_type() -> None:
    """TOML datetime values are outside the supported state schema."""
    data = _data()
    data["collectors"] = {"host": {"status": "ok", "error": "", "when": datetime(2026, 10, 5)}}
    with pytest.raises(ValueError, match="unsupported value type"):
        Snapshot.from_toml_dict(data)
