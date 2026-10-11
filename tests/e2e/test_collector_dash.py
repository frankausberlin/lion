"""End-to-end scenarios for the ``dash`` content-hash collector.

These tests run the installed ``lion`` executable inside the disposable
container against a real ``~/.shlib/dash`` and a real history. They pin the
opt-in contract: only ``LIONSTATE_``-prefixed symlinks that resolve to a regular
file are hashed, a content change is a pure value change, and a new or removed
link is structural.
"""

import hashlib
import json
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
import tomli_w

pytestmark = pytest.mark.e2e


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict), value
    return cast("dict[str, object]", value)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _history(data: Path) -> dict[str, bytes]:
    history = data / "lion" / "history"
    return {path.name: path.read_bytes() for path in sorted(history.glob("*.toml"))}


def _scan(runner: Callable[..., str], env: dict[str, str]) -> dict[str, object]:
    return _mapping(json.loads(runner("lion", "scan", "--json", env=env)))


def _dash(state: dict[str, object]) -> dict[str, object]:
    return _mapping(_mapping(state["collectors"])["dash"])


def _opt_in(home: Path, content: str) -> Path:
    """Create ``~/.shlib/dash`` with one monitored link and return its target."""
    dash = home / ".shlib" / "dash"
    dash.mkdir(parents=True)
    target = home / "config" / "settings.yml"
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    (dash / "LIONSTATE_settings.yml").symlink_to(target)
    return target


def _strip_section(path: Path, name: str) -> None:
    """Rewrite one stored entry without the named collector section."""
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    collectors = _mapping(document["collectors"])
    del collectors[name]
    path.write_text(tomli_w.dumps(document), encoding="utf-8")


def test_scan_without_dash_directory_is_ok_empty(runner: Callable[..., str], env: dict[str, str]) -> None:
    """A missing ``~/.shlib/dash`` is an ok, empty section in a real scan."""
    payload = _scan(runner, env)
    assert payload["event"] == "created"
    assert _dash(_mapping(payload["state"])) == {"status": "ok", "error": "", "files": []}


def test_added_dash_link_is_structural(runner: Callable[..., str], env: dict[str, str]) -> None:
    """A new monitored link appears as an added, structural list entry."""
    _scan(runner, env)
    _opt_in(Path(env["HOME"]), "alpha\n")

    status = _mapping(json.loads(runner("lion", "status", "--json", env=env)))

    assert status["changed"] is True
    assert status["structure_changed"] is True
    added = _mapping(_mapping(_mapping(status["differences"])["dash"])["added"])
    assert added == {"files[LIONSTATE_settings.yml]": {"name": "LIONSTATE_settings.yml", "hash": _digest("alpha\n")}}

    appended = _scan(runner, env)
    assert appended["event"] == "appended"
    assert _dash(_mapping(appended["state"]))["files"] == [
        {"name": "LIONSTATE_settings.yml", "hash": _digest("alpha\n")}
    ]


def test_content_change_is_a_value_change(runner: Callable[..., str], env: dict[str, str]) -> None:
    """A content change is a nonstructural change of the entry's hash."""
    target = _opt_in(Path(env["HOME"]), "alpha\n")
    assert _scan(runner, env)["event"] == "created"

    target.write_text("beta\n", encoding="utf-8")
    status = _mapping(json.loads(runner("lion", "status", "--json", env=env)))

    assert status["changed"] is True
    assert status["structure_changed"] is False
    changed = _mapping(_mapping(_mapping(status["differences"])["dash"])["changed"])
    assert changed == {"files[LIONSTATE_settings.yml].hash": {"old": _digest("alpha\n"), "new": _digest("beta\n")}}

    assert _scan(runner, env)["event"] == "appended"
    assert _scan(runner, env)["event"] == "confirmed"


def test_ignores_unprefixed_regular_directory_and_broken_links(runner: Callable[..., str], env: dict[str, str]) -> None:
    """Only prefixed links to regular files are monitored in a real scan."""
    home = Path(env["HOME"])
    target = _opt_in(home, "alpha\n")
    dash = home / ".shlib" / "dash"
    (dash / "ordinary.conf").symlink_to(target)
    (dash / "LIONSTATE_plain").write_text("plain", encoding="utf-8")
    folder = home / "folder"
    folder.mkdir()
    (dash / "LIONSTATE_dir").symlink_to(folder)
    (dash / "LIONSTATE_broken").symlink_to(home / "missing")

    payload = _scan(runner, env)

    assert _dash(_mapping(payload["state"]))["files"] == [
        {"name": "LIONSTATE_settings.yml", "hash": _digest("alpha\n")}
    ]


def test_old_snapshot_without_dash_is_additive(runner: Callable[..., str], env: dict[str, str]) -> None:
    """A stored snapshot without the dash section stays readable and is appended on rescan."""
    data = Path(env["XDG_DATA_HOME"])
    created = _scan(runner, env)
    _strip_section(Path(str(created["path"])), "dash")

    status = _mapping(json.loads(runner("lion", "status", "--json", env=env)))
    assert status["changed"] is True
    assert status["structure_changed"] is True
    added = _mapping(_mapping(_mapping(status["differences"])["dash"])["added"])
    assert set(added) == {"(collector)"}

    appended = _scan(runner, env)
    assert appended["event"] == "appended"
    assert appended["path"] != created["path"]
    confirmed = _scan(runner, env)
    assert confirmed["event"] == "confirmed"
    assert confirmed["path"] == appended["path"]
    assert len(_history(data)) == 2


def test_read_only_commands_leave_history_untouched(runner: Callable[..., str], env: dict[str, str]) -> None:
    """status, history, diff and doctor never change the dash history bytes."""
    data = Path(env["XDG_DATA_HOME"])
    _scan(runner, env)
    _opt_in(Path(env["HOME"]), "alpha\n")
    assert _scan(runner, env)["event"] == "appended"
    before = _history(data)

    runner("lion", "status", "--json", env=env)
    runner("lion", "history", "--json", env=env)
    runner("lion", "diff", "previous", "latest", "--json", env=env)
    runner("lion", "doctor", "--json", env=env)

    assert _history(data) == before
    assert len(before) == 2
