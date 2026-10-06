"""Tests for the recursive collector diff and its rendering."""

from repolion.program.diff import diff_collectors, render

RAM_OLD = 99005419520
RAM_NEW = 99005415424
RAM_TOLERANCE = 1024 * 1024


def _host(hostname: str = "lion", status: str = "ok") -> dict[str, object]:
    return {"status": status, "error": "", "hostname": hostname}


def _hardware(memory_total_bytes: int, gpu: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "status": "ok",
        "error": "",
        "cpu_model": "Example CPU",
        "cpu_logical_cores": 8,
        "memory_total_bytes": memory_total_bytes,
        "cuda_version": "",
        "gpu": gpu if gpu is not None else [],
    }


def test_no_difference() -> None:
    """Identical sections produce an empty diff."""
    state = {"host": _host()}
    assert diff_collectors(state, {"host": _host()}) == {}


def test_scalar_change() -> None:
    """A changed scalar is reported under ``changed`` with old and new values."""
    diff = diff_collectors({"host": _host()}, {"host": _host(hostname="server")})
    assert diff == {"host": {"changed": {"hostname": {"old": "lion", "new": "server"}}}}


def test_nested_leaf_mapping() -> None:
    """Leaf mappings such as packages.installed yield per-entry added/changed entries."""
    old = {"packages": {"status": "ok", "error": "", "installed": {"bash": "1.0", "gone": "2.0"}}}
    new = {"packages": {"status": "ok", "error": "", "installed": {"bash": "1.1", "new": "3.0"}}}
    diff = diff_collectors(old, new)
    assert diff == {
        "packages": {
            "added": {"installed.new": "3.0"},
            "removed": {"installed.gone": "2.0"},
            "changed": {"installed.bash": {"old": "1.0", "new": "1.1"}},
        }
    }


def test_list_change_is_leaf() -> None:
    """Lists are compared as whole leaf values."""
    old = {"packages": {"status": "ok", "error": "", "manual": ["bash"]}}
    new = {"packages": {"status": "ok", "error": "", "manual": ["bash", "zsh"]}}
    diff = diff_collectors(old, new)
    assert diff == {"packages": {"changed": {"manual": {"old": ["bash"], "new": ["bash", "zsh"]}}}}


def test_collector_added_and_removed() -> None:
    """Whole collectors appearing or disappearing are reported."""
    assert diff_collectors({}, {"host": _host()}) == {"host": {"added": {"(collector)": _host()}}}
    assert diff_collectors({"host": _host()}, {}) == {"host": {"removed": {"(collector)": _host()}}}


def test_render_empty() -> None:
    """Rendering an empty diff yields an empty string."""
    assert render({}) == ""


def test_render_all_categories() -> None:
    """Rendering shows added, removed and changed lines per collector."""
    diff = {
        "packages": {
            "added": {"installed.new": "3.0"},
            "removed": {"installed.gone": "2.0"},
            "changed": {"installed.bash": {"old": "1.0", "new": "1.1"}},
        }
    }
    rendered = render(diff)
    assert rendered.splitlines() == [
        "packages:",
        "  + installed.new = 3.0",
        "  - installed.gone = 2.0",
        "  ~ installed.bash: 1.0 -> 1.1",
    ]


def test_render_non_string_values() -> None:
    """Non-string values are rendered as JSON."""
    diff = {"host": {"changed": {"count": {"old": 1, "new": 2}}}}
    assert render(diff).splitlines()[-1] == "  ~ count: 1 -> 2"


def test_memory_wobble_is_not_a_hardware_change() -> None:
    """The reported 4 KiB MemTotal deviation alone yields no hardware diff."""
    assert diff_collectors({"hardware": _hardware(RAM_OLD)}, {"hardware": _hardware(RAM_NEW)}) == {}


def test_memory_tolerance_boundary() -> None:
    """A deviation exactly at the tolerance is hidden; one byte more is reported."""
    assert (
        diff_collectors(
            {"hardware": _hardware(RAM_OLD)},
            {"hardware": _hardware(RAM_OLD + RAM_TOLERANCE)},
        )
        == {}
    )
    larger = RAM_OLD + RAM_TOLERANCE + 1
    assert diff_collectors({"hardware": _hardware(RAM_OLD)}, {"hardware": _hardware(larger)}) == {
        "hardware": {"changed": {"memory_total_bytes": {"old": RAM_OLD, "new": larger}}}
    }


def test_memory_valid_versus_zero_is_visible() -> None:
    """Switching between a valid reading and ``0`` stays a change in both directions."""
    assert diff_collectors({"hardware": _hardware(RAM_OLD)}, {"hardware": _hardware(0)}) == {
        "hardware": {"changed": {"memory_total_bytes": {"old": RAM_OLD, "new": 0}}}
    }
    assert diff_collectors({"hardware": _hardware(0)}, {"hardware": _hardware(RAM_NEW)}) == {
        "hardware": {"changed": {"memory_total_bytes": {"old": 0, "new": RAM_NEW}}}
    }


def test_memory_wobble_keeps_other_differences() -> None:
    """A hidden RAM wobble must not suppress an independent package change."""
    old = {
        "hardware": _hardware(RAM_OLD),
        "packages": {"status": "ok", "error": "", "installed": {"libunbound8:amd64": "1.24.2-1ubuntu2.2"}},
    }
    new = {
        "hardware": _hardware(RAM_NEW),
        "packages": {"status": "ok", "error": "", "installed": {"libunbound8:amd64": "1.24.2-1ubuntu2.3"}},
    }
    assert diff_collectors(old, new) == {
        "packages": {
            "changed": {"installed.libunbound8:amd64": {"old": "1.24.2-1ubuntu2.2", "new": "1.24.2-1ubuntu2.3"}}
        }
    }


def test_memory_tolerance_does_not_apply_to_gpu() -> None:
    """GPU memory has no tolerance: a 4 KiB deviation is reported exactly."""
    old_gpu: list[dict[str, object]] = [{"name": "GPU", "driver_version": "1.0", "memory_total_bytes": 8573157376}]
    new_gpu: list[dict[str, object]] = [
        {"name": "GPU", "driver_version": "1.0", "memory_total_bytes": 8573157376 + 4096}
    ]
    assert diff_collectors(
        {"hardware": _hardware(RAM_OLD, gpu=old_gpu)},
        {"hardware": _hardware(RAM_OLD, gpu=new_gpu)},
    ) == {"hardware": {"changed": {"gpu": {"old": old_gpu, "new": new_gpu}}}}
