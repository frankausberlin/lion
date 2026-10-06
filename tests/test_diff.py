"""Tests for the recursive collector diff and its rendering."""

from repolion.program.diff import diff_collectors, render


def _host(hostname: str = "lion", status: str = "ok") -> dict[str, object]:
    return {"status": status, "error": "", "hostname": hostname}


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
