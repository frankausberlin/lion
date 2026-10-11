"""End-to-end write boundaries and history-read failures.

These tests run the installed ``lion`` executable with real permissions and a
real history. They pin the read-only contract of a fresh environment, the
failure behaviour of an unwritable or unreadable history, and the rule that no
command may hide a damaged or unreadable entry.
"""

import json
import stat
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from e2eutil import Runner, dash_link, manifest

pytestmark = pytest.mark.e2e


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict), value
    return cast("dict[str, object]", value)


def _scan(runner: Callable[..., str], env: dict[str, str]) -> dict[str, object]:
    return _mapping(json.loads(runner("lion", "scan", "--json", env=env)))


def _entries(history: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(history.glob("*.toml"))}


def _two_states(runner: Callable[..., str], env: dict[str, str]) -> Path:
    """Create two real snapshots via a dash change and return the history dir."""
    home = Path(env["HOME"])
    assert _scan(runner, env)["event"] == "created"
    dash_link(home, "LIONSTATE_probe", "probe\n")
    assert _scan(runner, env)["event"] == "appended"
    history = Path(env["XDG_DATA_HOME"]) / "lion" / "history"
    assert len(list(history.glob("*.toml"))) == 2
    return history


def test_fresh_home_read_only_commands_write_nothing(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """A fresh environment: every bare/read-only command stays a pure read."""
    home = Path(env["HOME"])
    before_home = manifest(home)

    bare = runner.invoke("lion", env=env, expected=0)
    status = runner.invoke("lion", "status", env=env, expected=0)
    status_json = runner.invoke("lion", "status", "--json", env=env, expected=0)
    history = runner.invoke("lion", "history", env=env, expected=0)
    history_json = runner.invoke("lion", "history", "--json", env=env, expected=0)
    runner.invoke("lion", "shlib", env=env, expected=0)
    runner.invoke("lion", "shlib", "status", env=env, expected=0)

    assert "No state stored" in bare.stdout
    assert "No state stored" in status.stdout
    assert json.loads(status_json.stdout) is None
    assert "No state stored" in history.stdout
    assert json.loads(history_json.stdout) == {"entries": []}

    assert not (tmp_path / "data").exists()
    assert manifest(home) == before_home

    failed = runner.invoke("lion", "diff", "previous", "latest", env=env, expected=1)
    assert failed.stderr.startswith("Error:")
    assert json.loads(failed.stdout or "null") is None
    assert not (tmp_path / "data").exists()
    assert manifest(home) == before_home


def test_unwritable_history_fails_without_corruption(user_runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """A denied history write fails loudly and leaves the stored bytes intact."""
    data = Path(env["XDG_DATA_HOME"])
    first = _scan(user_runner, env)
    assert first["event"] == "created"
    history = data / "lion" / "history"
    assert (data / "lion" / ".history.lock").exists()
    before = _entries(history)

    history.chmod(0o555)
    try:
        result = user_runner.invoke("lion", "scan", "--json", env=env, expected=1)
        assert result.stderr.startswith("Error:")
        assert "history" in result.stderr
        assert _entries(history) == before
        assert not list(history.glob("*.tmp"))
    finally:
        history.chmod(0o755)

    second = _scan(user_runner, env)
    assert second["event"] == "confirmed"
    assert second["path"] == first["path"]


def test_unreadable_latest_entry_fails_every_command(user_runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """An unreadable newest entry makes every command fail with its path."""
    history = _two_states(user_runner, env)
    entries = sorted(history.glob("*.toml"))
    newest = entries[-1]
    before = _entries(history)
    before_manifest = manifest(history)
    original_mode = stat.S_IMODE(newest.stat().st_mode)

    newest.chmod(0o000)
    try:
        for args in (
            ("lion", "status", "--json"),
            ("lion", "history", "--json"),
            ("lion", "diff", "previous", "latest", "--json"),
            ("lion", "scan", "--json"),
        ):
            result = user_runner.invoke(*args, env=env, expected=1)
            assert result.stderr.startswith("Error:")
            assert newest.name in result.stderr
    finally:
        newest.chmod(original_mode)

    assert _entries(history) == before
    assert manifest(history) == before_manifest


def test_unreadable_older_entry_fails_a_scan(user_runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """A scan validates every entry, so an unreadable older one still fails it."""
    history = _two_states(user_runner, env)
    entries = sorted(history.glob("*.toml"))
    older = entries[0]
    before = _entries(history)
    original_mode = stat.S_IMODE(older.stat().st_mode)

    older.chmod(0o000)
    try:
        result = user_runner.invoke("lion", "scan", "--json", env=env, expected=1)
        assert result.stderr.startswith("Error:")
        assert older.name in result.stderr
    finally:
        older.chmod(original_mode)

    assert _entries(history) == before


def test_syntactically_broken_older_entry_fails_everywhere(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """A damaged older entry is never skipped, not even by ``history --limit 1``."""
    history = _two_states(runner, env)
    entries = sorted(history.glob("*.toml"))
    older = entries[0]
    older.write_text("broken = [\n", encoding="utf-8")
    damaged = manifest(history)

    for args in (
        ("lion", "status", "--json"),
        ("lion", "history", "--json"),
        ("lion", "history", "--limit", "1", "--json"),
        ("lion", "diff", "previous", "latest", "--json"),
        ("lion", "scan", "--json"),
    ):
        result = runner.invoke(*args, env=env, expected=1)
        assert result.stderr.startswith("Error:")
        assert older.name in result.stderr

    assert manifest(history) == damaged
