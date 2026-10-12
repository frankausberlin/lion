"""The watch integration as the unprivileged ``lion-e2e`` container user.

Runs inside the disposable container (see ``conftest.py``). The interactive
shell lives in a tmux server owned by ``lion-e2e`` with its own socket
directory, so every file the CLI and the integration write belongs to that
user. The scenarios are deliberately compact: a full watch lifecycle with a
real non-root shell, and the denied overlay write path.
"""

import json
import os
import pwd
from pathlib import Path
from typing import cast

import pytest
from e2eutil import CommandResult, Runner, UserTmux
from watchutil import Zsh

pytestmark = pytest.mark.e2e


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict), value
    return cast("dict[str, object]", value)


def _json(result: CommandResult) -> dict[str, object]:
    return _mapping(json.loads(result.stdout))


def _events(payload: dict[str, object]) -> list[dict[str, object]]:
    raw = payload["events"]
    assert isinstance(raw, list), raw
    return [_mapping(item) for item in cast("list[object]", raw)]


def _command_texts(events: list[dict[str, object]]) -> list[str]:
    return [str(event["text"]) for event in events if event["kind"] in ("cmd", "comment")]


def _install(runner: Runner, env: dict[str, str]) -> None:
    """Install shlib and the watch integration the real way."""
    runner("lion", "shlib", "install", env=env)
    runner("lion", "shell", "insert", env=env)


def _user_owned_paths(root: Path) -> list[Path]:
    """Assert that everything below ``root`` belongs to lion-e2e."""
    account = pwd.getpwnam("lion-e2e")
    paths = [path for path in root.rglob("*")]
    assert paths
    assert all(path.stat().st_uid == account.pw_uid for path in paths)
    return paths


def test_watch_lifecycle_as_unprivileged_user(
    user_runner: Runner, user_tmux: UserTmux, env: dict[str, str], tmp_path: Path
) -> None:
    """A real non-root shell records into a recording space it owns."""
    _install(user_runner, env)
    start = user_runner("lion", "watch", "start", "--note", "user e2e", env=env)
    assert "Recording started" in start

    account = pwd.getpwnam("lion-e2e")
    zdot = tmp_path / "zdot"
    zdot.mkdir()
    os.chown(zdot, account.pw_uid, account.pw_gid)

    with Zsh(env, zdot, tmux_prefix=user_tmux.prefix, tmux_env=user_tmux.env) as shell:
        _ = shell.wait_prompt()
        _ = shell.run("echo user-command")
        live = _json(user_runner.invoke("lion", "watch", "status", "--json", env=env, expected=0))
        assert live["active"] is True

    payload = _json(user_runner.invoke("lion", "watch", "show", "latest", "--json", env=env, expected=0))
    assert "echo user-command" in _command_texts(_events(payload))
    _ = user_runner("lion", "watch", "stop", env=env)
    paths = _user_owned_paths(tmp_path / "data" / "lion" / "recordings")
    assert any(path.is_file() for path in paths)
    assert all(path.stat().st_mode & 0o077 == 0 for path in paths if path.is_dir())


def test_records_edit_denied_leaves_no_overlay_or_draft(
    user_runner: Runner, env: dict[str, str], tmp_path: Path
) -> None:
    """A user denied the overlay write fails loudly without a partial file."""
    _install(user_runner, env)
    _ = user_runner("lion", "watch", "start", env=env)
    _ = user_runner("lion", "watch", "stop", env=env)

    record = next((tmp_path / "data" / "lion" / "recordings").iterdir())
    record.chmod(0o500)
    try:
        result = user_runner.invoke("lion", "records", "edit", "latest", env={**env, "EDITOR": "true"}, expected=1)
        assert "Permission denied" in result.stderr
        listing = {path.name for path in record.iterdir()}
        assert "adr.toml" not in listing
        assert not any(name.startswith(".adr-edit-") for name in listing)
        read = user_runner.invoke("lion", "records", "adrs", "latest", env=env, expected=0)
        assert "No ADR" in read.stdout
    finally:
        record.chmod(0o700)
