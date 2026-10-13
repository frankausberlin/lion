"""Tests for the shared ``scan``/``status`` collector index.

The non-TTY format is pinned exactly; the TTY branch is exercised through the
testable ``_is_tty`` indirection plus ``NO_COLOR`` (including its empty-value
semantics).
"""

from pathlib import Path

import pytest

from lion.command import overview
from lion.program.diff import CollectorDiff
from lion.state.registry import COLLECTORS

NAMES = [collector.name for collector in COLLECTORS]


def _state() -> dict[str, dict[str, object]]:
    return {name: {"status": "ok", "error": ""} for name in NAMES}


def _non_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(overview, "_is_tty", lambda: False)


def _tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(overview, "_is_tty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)


def test_non_tty_format_is_exact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The plain index matches the documented line format and order."""
    _non_tty(monkeypatch)

    text = overview.render_index(_state(), None, tmp_path)

    assert text == "\n".join(f"  \u25cf {name.ljust(10)}  {'ok'.ljust(11)}  new" for name in NAMES)
    assert text.splitlines()[0] == "  \u25cf host        ok           new"
    assert "\x1b" not in text


def test_unchanged_missing_and_unavailable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An empty diff is ``unchanged``; a missing section stays visible."""
    _non_tty(monkeypatch)
    state = _state()
    del state["hardware"]
    state["network"] = {"status": "unavailable", "error": "sysfs missing"}

    lines = overview.render_index(state, {}, tmp_path).splitlines()

    assert lines[0] == "  \u25cf host        ok           unchanged"
    assert lines[1] == "  \u25cf hardware    unavailable  unchanged"
    assert lines[2] == "  \u25cf network     unavailable  unchanged"


def test_structural_counters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Structural changes carry the marker and only nonzero counters."""
    _non_tty(monkeypatch)
    diff: CollectorDiff = {
        "hardware": {"added": {"gpu[a]": 1, "gpu[b]": 2}, "removed": {"cpu_model": "old"}},
        "packages": {"removed": {"manual[a]": "a"}},
        "services": {"added": {"units[x]": {}}},
        "dash": {"changed": {"files[LIONSTATE_x].hash": {"old": "a", "new": "b"}}},
    }

    text = overview.render_index(_state(), diff, tmp_path)

    assert "  \u25cf host        ok           unchanged" in text
    assert "  \u25cf hardware    ok           changed \u25b2 +2 \u22121" in text
    assert "  \u25cf packages    ok           changed \u25b2 \u22121" in text
    assert "  \u25cf services    ok           changed \u25b2 +1" in text
    assert "  \u25cf dash        ok           changed" in text


def test_tty_colors_and_hyperlinks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """In a TTY the semantic colors and OSC-8 page links are present."""
    _tty(monkeypatch)
    state = _state()
    state["host"] = {"status": "error", "error": "broken"}
    state["hardware"] = {"status": "unavailable", "error": "missing"}

    lines = overview.render_index(state, {}, tmp_path).splitlines()

    host_uri = (tmp_path / "host.md").as_uri()
    assert lines[0] == (
        f"  \x1b[31m\u25cf\x1b[0m \x1b]8;;{host_uri}\x1b\\host      \x1b]8;;\x1b\\  "
        f"\x1b[31merror      \x1b[0m  unchanged"
    )
    hardware_uri = (tmp_path / "hardware.md").as_uri()
    assert lines[1] == (
        f"  \x1b[33m\u25cf\x1b[0m \x1b]8;;{hardware_uri}\x1b\\hardware  \x1b]8;;\x1b\\  "
        f"\x1b[33munavailable\x1b[0m  unchanged"
    )
    assert "\x1b]8;;" in lines[2]
    assert "\x1b[32m" in lines[2]


def test_tty_change_colors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Changed and new lines are blue; unchanged lines are green."""
    _tty(monkeypatch)
    diff: CollectorDiff = {"host": {"changed": {"hostname": {"old": "a", "new": "b"}}}}

    lines = overview.render_index(_state(), diff, tmp_path).splitlines()

    assert "\x1b[34m" in lines[0]
    assert "\x1b[34m" in lines[0].split("host")[0]  # the bullet carries the color
    assert "\x1b[32m" in lines[1]
    assert "\x1b[34m" not in lines[1]

    fresh = overview.render_index(_state(), None, tmp_path).splitlines()
    assert "\x1b[34m" in fresh[0]
    assert "\x1b[32m" not in fresh[0]


def test_no_color_disables_escapes_in_tty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A non-empty NO_COLOR yields the plain format even on a TTY."""
    monkeypatch.setattr(overview, "_is_tty", lambda: True)
    monkeypatch.setenv("NO_COLOR", "1")

    text = overview.render_index(_state(), None, tmp_path)

    assert "\x1b" not in text
    assert "  \u25cf host        ok           new" in text


def test_empty_no_color_keeps_escapes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An empty NO_COLOR counts as unset, per the documented rule."""
    monkeypatch.setattr(overview, "_is_tty", lambda: True)
    monkeypatch.setenv("NO_COLOR", "")

    text = overview.render_index(_state(), None, tmp_path)

    assert "\x1b[34m" in text


def test_unknown_status_gets_no_semantic_color(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A status outside the semantic set renders without a color."""
    _tty(monkeypatch)
    state = _state()
    state["host"] = {"status": "strange", "error": ""}

    lines = overview.render_index(state, {}, tmp_path).splitlines()

    assert "\x1b[3" not in lines[0]
    assert "strange" in lines[0]
