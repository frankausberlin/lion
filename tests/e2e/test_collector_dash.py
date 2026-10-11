"""End-to-end scenarios for the ``dash`` content-hash collector.

These tests run the installed ``lion`` executable inside the disposable
container against a real ``~/.shlib/dash`` and a real history. They pin the
opt-in contract: only ``LIONSTATE_``-prefixed symlinks that resolve to a regular
file are hashed, a content change is a pure value change, and a new or removed
link is structural. The unreadable and unlistable failure paths are exercised
with the real unprivileged ``lion-e2e`` user.
"""

import hashlib
import json
import os
import pwd
import stat
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
import tomli_w
from e2eutil import Runner, manifest

pytestmark = pytest.mark.e2e

_SECTIONS = {"host", "hardware", "network", "packages", "services", "containers", "tools", "dash"}
_DASH = Path(".shlib") / "dash"
_CANARY = "LION_E2E_DASH_CANARY_7c1f9a"


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


def _dash_names(state: dict[str, object]) -> list[str]:
    files = cast("list[object]", _dash(state)["files"])
    return [str(_mapping(entry)["name"]) for entry in files]


def _findings(stdout: str) -> dict[str, dict[str, object]]:
    raw = _mapping(json.loads(stdout))["findings"]
    assert isinstance(raw, list), raw
    return {str(_mapping(item)["name"]): _mapping(item) for item in cast("list[object]", raw)}


def _opt_in(home: Path, content: str) -> Path:
    """Create ``~/.shlib/dash`` with one monitored link and return its target."""
    dash = home / ".shlib" / "dash"
    dash.mkdir(parents=True)
    target = home / "config" / "settings.yml"
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    (dash / "LIONSTATE_settings.yml").symlink_to(target)
    return target


def _link(home: Path, name: str, content: str) -> Path:
    """Create ``~/.shlib/dash/<name>`` resolving to a fresh target; return the target."""
    dash = home / _DASH
    dash.mkdir(parents=True, exist_ok=True)
    target = home / "config" / f"{name}.data"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    link = dash / name
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(target)
    return target


def _chown_tree(root: Path) -> None:
    """Transfer ownership of ``root`` and everything below it to ``lion-e2e``."""
    account = pwd.getpwnam("lion-e2e")
    os.chown(root, account.pw_uid, account.pw_gid)
    for child in root.rglob("*"):
        os.chown(child, account.pw_uid, account.pw_gid, follow_symlinks=False)


def _traverse(root: Path) -> None:
    """Make every directory below ``root`` traversable for the unprivileged user."""
    for path in [root, *root.rglob("*")]:
        if stat.S_ISDIR(path.lstat().st_mode):
            path.chmod(0o755)


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


def test_removed_dash_link_is_structural(runner: Runner, env: dict[str, str]) -> None:
    """Removing one of two monitored links is a structural removal everywhere."""
    home = Path(env["HOME"])
    _link(home, "LIONSTATE_alpha", "alpha\n")
    _link(home, "LIONSTATE_beta", "beta\n")
    assert _scan(runner, env)["event"] == "created"
    data = Path(env["XDG_DATA_HOME"])
    before = _history(data)

    (home / _DASH / "LIONSTATE_beta").unlink()
    status = _mapping(json.loads(runner("lion", "status", "--json", env=env)))
    assert status["changed"] is True
    assert status["structure_changed"] is True
    assert status["differences"] == {
        "dash": {"removed": {"files[LIONSTATE_beta]": {"name": "LIONSTATE_beta", "hash": _digest("beta\n")}}}
    }

    appended = _scan(runner, env)
    assert appended["event"] == "appended"
    assert _dash(_mapping(appended["state"]))["files"] == [{"name": "LIONSTATE_alpha", "hash": _digest("alpha\n")}]
    after = _history(data)
    assert len(after) == 2
    assert all(after[name] == content for name, content in before.items())

    delta = _mapping(json.loads(runner("lion", "diff", "previous", "latest", "--json", env=env)))
    assert delta["structure_changed"] is True
    assert _mapping(delta["differences"])["dash"] == {
        "removed": {"files[LIONSTATE_beta]": {"name": "LIONSTATE_beta", "hash": _digest("beta\n")}}
    }

    assert _scan(runner, env)["event"] == "confirmed"


def test_content_change_and_revert_append_distinct_states(runner: Runner, env: dict[str, str]) -> None:
    """A change and its revert each append a state; only the hash differs."""
    target = _link(Path(env["HOME"]), "LIONSTATE_alpha", "alpha\n")
    first = _scan(runner, env)
    assert first["event"] == "created"

    target.write_text("beta\n", encoding="utf-8")
    changed = _mapping(json.loads(runner("lion", "status", "--json", env=env)))
    assert changed["changed"] is True
    assert changed["structure_changed"] is False
    second = _scan(runner, env)
    assert second["event"] == "appended"

    target.write_text("alpha\n", encoding="utf-8")
    reverted = _mapping(json.loads(runner("lion", "status", "--json", env=env)))
    assert reverted["changed"] is True
    assert reverted["structure_changed"] is False
    assert _mapping(_mapping(_mapping(reverted["differences"])["dash"])["changed"]) == {
        "files[LIONSTATE_alpha].hash": {"old": _digest("beta\n"), "new": _digest("alpha\n")}
    }
    third = _scan(runner, env)
    assert third["event"] == "appended"

    first_state = _mapping(first["state"])
    third_state = _mapping(third["state"])
    assert first_state["created_at"] != third_state["created_at"]
    assert _dash(first_state) == _dash(third_state)

    confirmed = _scan(runner, env)
    assert confirmed["event"] == "confirmed"
    assert confirmed["path"] == third["path"]


def test_retargeted_dash_link_changes_only_hash(runner: Runner, env: dict[str, str]) -> None:
    """Pointing a link at a different target is a nonstructural hash change."""
    home = Path(env["HOME"])
    original = _opt_in(home, "alpha\n")
    assert _scan(runner, env)["event"] == "created"

    replacement = home / "config" / "replacement.yml"
    replacement.write_text("gamma\n", encoding="utf-8")
    link = home / _DASH / "LIONSTATE_settings.yml"
    link.unlink()
    link.symlink_to(replacement)

    status = _mapping(json.loads(runner("lion", "status", "--json", env=env)))
    assert status["changed"] is True
    assert status["structure_changed"] is False
    assert status["differences"] == {
        "dash": {
            "changed": {"files[LIONSTATE_settings.yml].hash": {"old": _digest("alpha\n"), "new": _digest("gamma\n")}}
        }
    }

    assert _scan(runner, env)["event"] == "appended"
    delta = _mapping(json.loads(runner("lion", "diff", "previous", "latest", "--json", env=env)))
    assert delta["structure_changed"] is False
    changed = _mapping(_mapping(_mapping(delta["differences"])["dash"])["changed"])
    assert set(changed) == {"files[LIONSTATE_settings.yml].hash"}
    assert str(original) not in json.dumps(delta)
    assert str(replacement) not in json.dumps(delta)


def test_dash_path_that_is_a_regular_file(runner: Runner, env: dict[str, str]) -> None:
    """A regular file at ``~/.shlib/dash`` is unavailable but keeps all sections."""
    home = Path(env["HOME"])
    shlib = home / ".shlib"
    shlib.mkdir(parents=True)
    (shlib / "dash").write_text("not a directory\n", encoding="utf-8")

    payload = _scan(runner, env)
    state = _mapping(payload["state"])
    assert _dash(state) == {"status": "unavailable", "error": "dash path is not a directory", "files": []}
    assert set(_mapping(state["collectors"])) == _SECTIONS

    repeat = _scan(runner, env)
    assert repeat["event"] == "confirmed"
    assert repeat["path"] == payload["path"]


def test_unreadable_dash_targets_report_stable_error(user_runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """Two unreadable targets yield unavailable with a stable, leak-free error."""
    home = Path(env["HOME"])
    _link(home, "LIONSTATE_a_good", "alpha\n")
    bad_b = _link(home, "LIONSTATE_b_bad", "bravo\n")
    bad_c = _link(home, "LIONSTATE_c_bad", "charlie\n")
    _link(home, "LIONSTATE_d_good", "delta\n")
    _chown_tree(home)
    _traverse(home)
    bad_b.chmod(0o000)
    bad_c.chmod(0o000)

    result = user_runner.invoke("lion", "scan", "--json", env=env, expected=0)
    payload = _mapping(json.loads(result.stdout))
    assert payload["event"] == "created"
    dash = _dash(_mapping(payload["state"]))
    assert dash["status"] == "unavailable"
    assert dash["error"] == ("Unreadable dash entry: LIONSTATE_b_bad; Unreadable dash entry: LIONSTATE_c_bad")
    assert dash["files"] == [
        {"name": "LIONSTATE_a_good", "hash": _digest("alpha\n")},
        {"name": "LIONSTATE_d_good", "hash": _digest("delta\n")},
    ]
    assert str(bad_b) not in result.stdout
    assert str(bad_c) not in result.stdout

    assert "Warning: dash:" in result.stderr
    assert "LIONSTATE_b_bad" in result.stderr
    assert "LIONSTATE_c_bad" in result.stderr

    stored = tomllib.loads(Path(str(payload["path"])).read_text(encoding="utf-8"))
    assert stored == _mapping(payload["state"])


def test_recovery_from_unreadable_targets(user_runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """Restoring read access recovers the missing hashes structurally."""
    home = Path(env["HOME"])
    _link(home, "LIONSTATE_a_good", "alpha\n")
    bad_b = _link(home, "LIONSTATE_b_bad", "bravo\n")
    bad_c = _link(home, "LIONSTATE_c_bad", "charlie\n")
    _link(home, "LIONSTATE_d_good", "delta\n")
    _chown_tree(home)
    _traverse(home)
    bad_b.chmod(0o000)
    bad_c.chmod(0o000)

    created = _scan(user_runner, env)
    assert _dash(_mapping(created["state"]))["status"] == "unavailable"

    bad_b.chmod(0o644)
    bad_c.chmod(0o644)
    status = _mapping(json.loads(user_runner("lion", "status", "--json", env=env)))
    assert status["changed"] is True
    assert status["structure_changed"] is True
    dash_delta = _mapping(_mapping(status["differences"])["dash"])
    assert _mapping(dash_delta["changed"])["status"] == {"old": "unavailable", "new": "ok"}
    assert "error" in _mapping(dash_delta["changed"])
    assert set(_mapping(dash_delta["added"])) == {"files[LIONSTATE_b_bad]", "files[LIONSTATE_c_bad]"}

    appended = _scan(user_runner, env)
    assert appended["event"] == "appended"
    assert _dash(_mapping(appended["state"])) == {
        "status": "ok",
        "error": "",
        "files": [
            {"name": "LIONSTATE_a_good", "hash": _digest("alpha\n")},
            {"name": "LIONSTATE_b_bad", "hash": _digest("bravo\n")},
            {"name": "LIONSTATE_c_bad", "hash": _digest("charlie\n")},
            {"name": "LIONSTATE_d_good", "hash": _digest("delta\n")},
        ],
    }
    assert _scan(user_runner, env)["event"] == "confirmed"

    doctor = user_runner.invoke("lion", "doctor", "--json", env=env)
    assert _findings(doctor.stdout)["collectors.dash"]["status"] == "ok"


def test_unlistable_dash_directory_is_unavailable(user_runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """A traversable but unlistable directory fails cleanly and recovers fully."""
    home = Path(env["HOME"])
    _link(home, "LIONSTATE_one", "one\n")
    _link(home, "LIONSTATE_two", "two\n")
    _chown_tree(home)
    _traverse(home)
    dash_dir = home / _DASH
    dash_dir.chmod(0o100)
    try:
        result = user_runner.invoke("lion", "scan", "--json", env=env, expected=0)
        payload = _mapping(json.loads(result.stdout))
        state = _mapping(payload["state"])
        assert payload["event"] == "created"
        assert _dash(state) == {"status": "unavailable", "error": "Cannot list ~/.shlib/dash", "files": []}
        collectors = _mapping(state["collectors"])
        assert set(collectors) == _SECTIONS
        assert _mapping(collectors["host"])["status"] == "ok"

        repeat = _scan(user_runner, env)
        assert repeat["event"] == "confirmed"
        assert repeat["path"] == payload["path"]
    finally:
        dash_dir.chmod(0o755)

    recovered = _scan(user_runner, env)
    assert recovered["event"] == "appended"
    assert _dash_names(_mapping(recovered["state"])) == ["LIONSTATE_one", "LIONSTATE_two"]


def test_dash_outputs_never_leak_content_or_targets(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """Every output channel stores hashes only, never content or target paths."""
    home = Path(env["HOME"])
    monitored = _link(home, "LIONSTATE_secret", f"{_CANARY}\n")
    unmonitored = home / "config" / "plain.secret"
    unmonitored.write_text(f"{_CANARY}\n", encoding="utf-8")
    (home / _DASH / "ordinary.secret").symlink_to(unmonitored)

    first = runner.invoke("lion", "scan", "--json", env=env, expected=0)
    _link(home, "LIONSTATE_second", "second\n")
    second = runner.invoke("lion", "scan", "--json", env=env, expected=0)
    assert json.loads(second.stdout)["event"] == "appended"

    outputs = [
        first,
        second,
        runner.invoke("lion", "status", "--json", env=env),
        runner.invoke("lion", "history", "--json", env=env),
        runner.invoke("lion", "diff", "previous", "latest", "--json", env=env),
        runner.invoke("lion", "doctor", "--json", env=env),
    ]
    for result in outputs:
        assert _CANARY not in result.stdout
        assert _CANARY not in result.stderr

    dash = _dash(_mapping(json.loads(first.stdout)["state"]))
    assert set(dash) == {"status", "error", "files"}
    files = cast("list[dict[str, object]]", dash["files"])
    for entry in files:
        assert set(entry) == {"name", "hash"}
    assert files == [{"name": "LIONSTATE_secret", "hash": hashlib.sha256(f"{_CANARY}\n".encode()).hexdigest()}]
    assert str(monitored) not in json.dumps(dash)
    assert str(unmonitored) not in json.dumps(dash)

    data = Path(env["XDG_DATA_HOME"])
    for path in (data / "lion" / "history").glob("*.toml"):
        assert _CANARY not in path.read_text(encoding="utf-8")
    recos = data / "lion" / "recos"
    if recos.is_dir():
        for path in recos.glob("*.sh"):
            assert _CANARY not in path.read_text(encoding="utf-8")


def test_read_only_commands_leave_filesystem_unchanged(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """status, history, diff and doctor never rewrite files; doctor adds only a reco."""
    home = Path(env["HOME"])
    _link(home, "LIONSTATE_alpha", "alpha\n")
    assert _scan(runner, env)["event"] == "created"
    _link(home, "LIONSTATE_beta", "beta\n")
    assert _scan(runner, env)["event"] == "appended"

    data = Path(env["XDG_DATA_HOME"])
    home_before = manifest(home)
    data_before = manifest(data)

    runner.invoke("lion", "status", "--json", env=env, expected=0)
    runner.invoke("lion", "history", "--json", env=env, expected=0)
    runner.invoke("lion", "diff", "previous", "latest", "--json", env=env, expected=0)

    assert manifest(home) == home_before
    assert manifest(data) == data_before

    runner.invoke("lion", "doctor", "--json", env=env)
    assert manifest(home) == home_before
    after = manifest(data)
    assert all(after[key] == value for key, value in data_before.items())
    assert all(key == "lion/recos" or key.startswith("lion/recos/") for key in set(after) - set(data_before))


def test_scan_leaves_only_the_stored_entry_and_lock(runner: Runner, env: dict[str, str]) -> None:
    """A real scan stages atomically: no temporary files survive the publish."""
    data = Path(env["XDG_DATA_HOME"])
    created = _scan(runner, env)

    files = {path.relative_to(data).as_posix() for path in data.rglob("*") if path.is_file()}
    assert files == {"lion/.history.lock", f"lion/history/{Path(str(created['path'])).name}"}


def test_old_snapshot_without_dash_is_additive(runner: Callable[..., str], env: dict[str, str]) -> None:
    """A stored snapshot without the dash section stays readable and is appended on rescan."""
    data = Path(env["XDG_DATA_HOME"])
    created = _scan(runner, env)
    created_path = Path(str(created["path"]))
    _strip_section(created_path, "dash")
    stripped = created_path.read_bytes()

    status = _mapping(json.loads(runner("lion", "status", "--json", env=env)))
    assert status["changed"] is True
    assert status["structure_changed"] is True
    added = _mapping(_mapping(_mapping(status["differences"])["dash"])["added"])
    assert set(added) == {"(collector)"}
    assert created_path.read_bytes() == stripped

    runner("lion", "history", "--json", env=env)
    runner("lion", "diff", "previous", "latest", "--json", env=env, expected=1)
    assert created_path.read_bytes() == stripped

    appended = _scan(runner, env)
    assert appended["event"] == "appended"
    assert appended["path"] != created["path"]
    assert created_path.read_bytes() == stripped
    assert len(_history(data)) == 2

    confirmed = _scan(runner, env)
    assert confirmed["event"] == "confirmed"
    assert confirmed["path"] == appended["path"]
    stored = tomllib.loads(Path(str(appended["path"])).read_text(encoding="utf-8"))
    assert stored["schema_version"] == 2
    assert created_path.read_bytes() == stripped


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
