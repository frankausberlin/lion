"""Exit and signal paths: the recording must state what really happened.

Runs inside the disposable container (see ``conftest.py``). Each scenario drives
a real interactive zsh in tmux and reads the outcome from the recording files
only; the terminal is never an assertion source. The expectations are defined up
front:

- ``exit N`` marks the segment ended with exit status ``N``; the exit line has
  no result of its own, and a missing end is never success.
- SIGKILL leaves no exit event at all; the segment ends by liveness (``gone``).
- Ctrl-C leaves a result whose status is not zero; no success is claimed.
- A background job's result is the prompt return, not the job's completion; the
  job may still be running when its result is read.
- Pipelines and compound lines carry the shell status of the whole line.
"""

import json
import time
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


def _segments(payload: dict[str, object]) -> list[dict[str, object]]:
    recording = _mapping(payload["recording"])
    raw = recording["segments"]
    assert isinstance(raw, list), raw
    return [_mapping(item) for item in cast("list[object]", raw)]


def _show(runner: Runner, env: dict[str, str]) -> dict[str, object]:
    return _json(runner.invoke("lion", "watch", "show", "latest", "--json", env=env, expected=0))


def _events(payload: dict[str, object]) -> list[dict[str, object]]:
    raw = payload["events"]
    assert isinstance(raw, list), raw
    return [_mapping(item) for item in cast("list[object]", raw)]


def _wait_dead(shell: Zsh, timeout: float = 5.0) -> None:
    """Wait until the pane is marked dead; a live pane would fake the path."""
    deadline = time.monotonic() + timeout
    while shell.alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not shell.alive()


def _wait_file(path: Path, timeout: float = 10.0) -> None:
    """Wait until a command of the shell reached its marked point of execution."""
    deadline = time.monotonic() + timeout
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert path.exists(), f"the shell command never created {path}"


def test_exit_and_kill_paths(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """``exit N`` records its status; SIGKILL leaves no exit event."""
    _install(runner, env)
    _ = runner("lion", "watch", "start", "--note", "exit paths", env=env)

    exiting = Zsh(env, tmp_path / "zdot-exit")
    try:
        _ = exiting.wait_prompt()
        exiting_pid = exiting.pid
        exiting.send("exit 7")
        _wait_dead(exiting)
    finally:
        exiting.close()

    killed = Zsh(env, tmp_path / "zdot-kill")
    try:
        _ = killed.wait_prompt()
        killed_pid = killed.pid
        killed.send("kill -9 $$")
        _wait_dead(killed)
    finally:
        killed.close()

    payload = _show(runner, env)
    events = _events(payload)
    segments = {segment["pid"]: segment for segment in _segments(payload)}
    exit_segment = segments[exiting_pid]
    assert exit_segment["alive"] is False
    assert exit_segment["commands"] == 1
    # The shell died inside ``exit 7``: no prompt, so no result event.
    assert exit_segment["results"] == 0
    exit_events = [event for event in events if event["kind"] == "exit" and event["segment"] == exit_segment["name"]]
    assert [event["status"] for event in exit_events] == [7]

    kill_segment = segments[killed_pid]
    assert kill_segment["alive"] is False
    assert kill_segment["end_reason"] == "gone"
    assert kill_segment["commands"] == 1
    assert kill_segment["results"] == 0
    assert kill_segment["exits"] == 0
    assert not any(event["kind"] == "exit" and event["segment"] == kill_segment["name"] for event in events)

    _ = runner("lion", "watch", "stop", env=env)


def test_interrupt_background_and_status_paths(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """Ctrl-C, background jobs, pipelines and compound lines are truthful."""
    _install(runner, env)
    _ = runner("lion", "watch", "start", "--note", "status paths", env=env)

    home = Path(env["HOME"])
    background = '(sleep 2; : > "$HOME/job-done") &'
    with Zsh(env, tmp_path / "zdot") as shell:
        _ = shell.wait_prompt()
        _ = shell.run("false | true")
        _ = shell.run("echo compound-ran; false")

        # Ctrl-C after a file proves the command started; the interrupt must
        # leave a result that is not a success.
        shell.send(": > $HOME/interrupted && sleep 30")
        _wait_file(home / "interrupted")
        shell.interrupt()
        _ = shell.wait_prompt(timeout=10.0)

        # The background job keeps running after the prompt returned; its
        # result is the prompt return, not the job's completion.
        _ = shell.run(background)
        assert not (home / "job-done").exists(), "the job must still be running"
        _ = shell.run("wait")
        assert (home / "job-done").exists()

    _ = runner("lion", "watch", "stop", env=env)
    events = _events(_show(runner, env))
    commands = [str(event["text"]) for event in events if event["kind"] == "cmd"]
    assert commands == [
        "false | true",
        "echo compound-ran; false",
        ": > $HOME/interrupted && sleep 30",
        background,
        "wait",
    ]
    statuses = [event["status"] for event in events if event["kind"] == "result"]
    assert len(statuses) == 5
    assert statuses[0] == 0  # pipeline: the last element's status
    assert statuses[1] == 1  # compound line: the line's final status
    assert statuses[2] not in (0, None)  # Ctrl-C: never a success
    assert statuses[3] == 0  # background: the prompt return
    assert statuses[4] == 0  # wait: the job finished by then
