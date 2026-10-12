"""Real races: overlapping shells and simultaneous start/stop processes.

Runs inside the disposable container (see ``conftest.py``). Two scenarios:

- Two shells really overlap: while one runs a slow foreground command, the
  other completes a command and its result is already recorded; the slow
  result only appears when its own prompt returns.
- Two simultaneous ``watch start`` (and later ``watch stop``) processes race
  for the pointer. Exactly one must win; the loser has to fail loudly, and no
  half record or staging file may remain.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest
from e2eutil import CommandResult, Runner
from watchutil import Zsh

pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("tmux_server")]


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict), value
    return cast("dict[str, object]", value)


def _json(result: CommandResult) -> dict[str, object]:
    return _mapping(json.loads(result.stdout))


def _install(runner: Runner, env: dict[str, str]) -> None:
    """Install shlib and the watch integration the real way."""
    runner("lion", "shlib", "install", env=env)
    runner("lion", "shell", "insert", env=env)


def _show(runner: Runner, env: dict[str, str]) -> dict[str, object]:
    return _json(runner.invoke("lion", "watch", "show", "latest", "--json", env=env, expected=0))


def _events(payload: dict[str, object]) -> list[dict[str, object]]:
    raw = payload["events"]
    assert isinstance(raw, list), raw
    return [_mapping(item) for item in cast("list[object]", raw)]


def _recordings(env: dict[str, str]) -> Path:
    return Path(env["XDG_DATA_HOME"]) / "lion" / "recordings"


def test_commands_overlap_across_two_shells(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """A slow command and a fast one in another shell truly interleave."""
    _install(runner, env)
    _ = runner("lion", "watch", "start", env=env)

    with Zsh(env, tmp_path / "zdota") as first, Zsh(env, tmp_path / "zdotb") as second:
        _ = first.wait_prompt()
        _ = second.wait_prompt()
        # No wait for the first shell: its result can only appear when its own
        # prompt returns, while the second shell already finished.
        first.send("sleep 2")
        _ = second.run("echo b-fast")
        _ = first.wait_prompt(timeout=15.0)

    events = _events(_show(runner, env))
    commands = [event for event in events if event["kind"] == "cmd"]
    assert [str(event["text"]) for event in commands] == ["sleep 2", "echo b-fast"]
    slow, fast = commands
    assert slow["segment"] != fast["segment"]
    slow_result = next(event for event in events if event["kind"] == "result" and event["segment"] == slow["segment"])
    fast_result = next(event for event in events if event["kind"] == "result" and event["segment"] == fast["segment"])
    # The fast shell's result was recorded while the slow command was pending.
    assert datetime.fromisoformat(str(fast_result["time"])) < datetime.fromisoformat(str(slow_result["time"]))
    slow_duration = slow_result["duration_ms"]
    fast_duration = fast_result["duration_ms"]
    assert isinstance(slow_duration, float) and slow_duration >= 1000
    assert isinstance(fast_duration, float) and fast_duration < 500


def _race(runner: Runner, env: dict[str, str], command: str) -> tuple[CommandResult, CommandResult]:
    """Run two ``lion watch <command>`` processes at once, one has to lose."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(runner.invoke, "lion", "watch", command, env=env, expected=None) for _ in range(2)]
        results = [future.result() for future in futures]
    winners = [result for result in results if result.returncode == 0]
    losers = [result for result in results if result.returncode != 0]
    assert len(winners) == 1, [result.returncode for result in results]
    assert len(losers) == 1
    assert losers[0].stderr.strip()
    return winners[0], losers[0]


def test_simultaneous_start_and_stop_fail_loudly(runner: Runner, env: dict[str, str]) -> None:
    """Exactly one of two racing start/stop processes wins; nothing half stays."""
    _install(runner, env)
    recordings = _recordings(env)

    start_winner, start_loser = _race(runner, env, "start")
    assert "Recording started" in start_winner.stdout
    assert "already exists" in start_loser.stderr or "existing file" in start_loser.stderr
    dirs = [path for path in recordings.iterdir() if path.is_dir()]
    assert len(dirs) == 1
    assert (recordings / "active").exists()
    assert not list(recordings.rglob(".lion.*"))
    status = _json(runner.invoke("lion", "watch", "status", "--json", env=env, expected=0))
    assert status["active"] is True
    assert _mapping(status["recording"])["status"] == "open"

    stop_winner, stop_loser = _race(runner, env, "stop")
    assert "Recording stopped" in stop_winner.stdout
    assert "existing file" in stop_loser.stderr or "No active recording" in stop_loser.stderr
    assert not (recordings / "active").exists()
    assert len([path for path in recordings.iterdir() if path.is_dir()]) == 1
    assert not list(recordings.rglob(".lion.*"))
    payload = _show(runner, env)
    assert _mapping(payload["recording"])["status"] == "stopped"
    assert _json(runner.invoke("lion", "watch", "status", "--json", env=env, expected=0)) == {"active": False}
