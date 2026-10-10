"""Tests for the snapshot model and its strict validation."""

from datetime import datetime

import pytest

from lion.state import model
from lion.state.model import Snapshot, canonical_collectors, collectors_equal

RAM_OLD = 99005419520
RAM_NEW = 99005415424
RAM_TOLERANCE = model.MEMORY_TOTAL_TOLERANCE_BYTES


def _memory(value: int) -> dict[str, dict[str, object]]:
    return {"hardware": {"status": "ok", "error": "", "memory_total_bytes": value}}


def _data() -> dict[str, object]:
    return {
        "schema_version": model.SCHEMA_VERSION,
        "created_at": "2026-10-05T20:00:00+00:00",
        "confirmed_at": "2026-10-05T20:30:00.123456+00:00",
        "collectors": {
            "host": {"status": "ok", "error": "", "hostname": "lion"},
            "hardware": {
                "status": "ok",
                "error": "",
                "gpu_vendor": "nvidia",
                "compute_platform": "cuda",
                "gpu": [
                    {
                        "pci_id": "0000:01:00.0",
                        "name": "GPU",
                        "vendor": "nvidia",
                        "driver": "nvidia",
                        "driver_version": "1.0",
                        "memory_total_bytes": 1024,
                    }
                ],
            },
        },
    }


def test_round_trip() -> None:
    """A valid mapping builds a snapshot that serializes back unchanged."""
    snapshot = Snapshot.from_toml_dict(_data())
    assert snapshot.schema_version == model.SCHEMA_VERSION
    assert snapshot.to_toml_dict() == _data()


def test_old_format_snapshot_remains_valid() -> None:
    """A snapshot predating the profile fields and ``tools`` collector loads."""
    data: dict[str, object] = {
        "schema_version": model.SCHEMA_VERSION,
        "created_at": "2026-10-05T20:00:00+00:00",
        "confirmed_at": "2026-10-05T20:30:00+00:00",
        "collectors": {
            "host": {"status": "ok", "error": "", "hostname": "lion"},
            "hardware": {
                "status": "ok",
                "error": "",
                "cpu_model": "CPU",
                "cpu_logical_cores": 8,
                "memory_total_bytes": 1024,
                "cuda_version": "",
                "gpu": [{"name": "GPU", "driver_version": "1.0", "memory_total_bytes": 512}],
            },
        },
    }
    snapshot = Snapshot.from_toml_dict(data)
    assert "tools" not in snapshot.collectors
    assert "gpu_vendor" not in snapshot.collectors["hardware"]


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


def test_collectors_equal_hides_memory_wobble() -> None:
    """The RAM tolerance makes a 4 KiB deviation equal in both directions."""
    assert collectors_equal(_memory(RAM_OLD), _memory(RAM_NEW))
    assert collectors_equal(_memory(RAM_NEW), _memory(RAM_OLD))


def test_collectors_equal_memory_tolerance_boundary() -> None:
    """The tolerance is inclusive; one byte beyond it is a change."""
    assert collectors_equal(_memory(RAM_OLD), _memory(RAM_OLD + RAM_TOLERANCE))
    assert not collectors_equal(_memory(RAM_OLD), _memory(RAM_OLD + RAM_TOLERANCE + 1))


def test_collectors_equal_memory_valid_versus_zero() -> None:
    """A valid reading never equals a missing/invalid one, but zero equals zero."""
    assert not collectors_equal(_memory(RAM_OLD), _memory(0))
    assert not collectors_equal(_memory(0), _memory(RAM_NEW))
    assert collectors_equal(_memory(0), _memory(0))


def test_collectors_equal_other_fields_stay_exact() -> None:
    """No other numeric field gets the tolerance (here a 4 KiB GPU change)."""
    old = {"hardware": {"status": "ok", "error": "", "gpu_memory": 1024}}
    new = {"hardware": {"status": "ok", "error": "", "gpu_memory": 1024 + 4096}}
    assert not collectors_equal(old, new)


def test_collectors_equal_detects_added_or_removed_sections() -> None:
    """A missing collector section is never equal to a present one."""
    assert not collectors_equal(_memory(RAM_OLD), {})
    assert not collectors_equal({}, _memory(RAM_OLD))


@pytest.mark.parametrize("version", [3, 0, "1", True, None])
def test_invalid_schema_version(version: object) -> None:
    """Reject missing, unknown, or non-integer schema versions."""
    data = _data()
    data["schema_version"] = version
    with pytest.raises(ValueError, match="schema_version"):
        Snapshot.from_toml_dict(data)


def test_legacy_german_keys_are_rejected() -> None:
    """Schema-1 German field names fail loudly with a migration hint."""
    data = _data()
    data["schema_version"] = 1
    data["erstscan"] = data.pop("created_at")
    data["zuletzt_bestaetigt"] = data.pop("confirmed_at")
    with pytest.raises(ValueError, match="legacy German snapshot keys"):
        Snapshot.from_toml_dict(data)


@pytest.mark.parametrize("key", ["created_at", "confirmed_at"])
def test_non_string_timestamp(key: str) -> None:
    """Timestamps must be strings before they can be parsed."""
    data = _data()
    data[key] = 5
    with pytest.raises(ValueError, match=f"{key} must be a string"):
        Snapshot.from_toml_dict(data)


@pytest.mark.parametrize("key", ["created_at", "confirmed_at"])
def test_naive_timestamp(key: str) -> None:
    """Every timestamp must carry a UTC offset."""
    data = _data()
    data[key] = "2026-10-05T20:00:00"
    with pytest.raises(ValueError, match="timezone"):
        Snapshot.from_toml_dict(data)


@pytest.mark.parametrize("key", ["created_at", "confirmed_at"])
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


@pytest.mark.parametrize(("old", "new"), [(1, True), (1, 1.0), ([{"x": 1}], [{"x": True}])])
def test_type_changes_are_differences(old: object, new: object) -> None:
    """Nested and scalar type changes must agree across storage and diff."""
    from lion.program.diff import diff_collectors

    before = {"host": {"status": "ok", "value": old}}
    after = {"host": {"status": "ok", "value": new}}
    assert not collectors_equal(before, after)
    assert diff_collectors(before, after)
