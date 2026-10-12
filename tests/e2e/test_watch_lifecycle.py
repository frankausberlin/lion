"""End-to-end watch scenarios with a real interactive zsh inside tmux.

Every test runs inside the disposable container (see ``conftest.py``), installs
the real shlib and watch integration into a temporary home, drives one or more
interactive shells and reads the results from the recording files — never from
the terminal text. The tmux screen is only used to detect prompts and to send
input.
"""

import json
import shutil
import time
from pathlib import Path
from typing import cast

import pytest
from e2eutil import CommandResult, Runner, doctor_findings, manifest
from watchutil import Zsh, strip_prompt

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


def _active(runner: Runner, env: dict[str, str]) -> dict[str, object]:
    payload = _json(runner.invoke("lion", "watch", "status", "--json", env=env, expected=0))
    assert payload["active"] is True, payload
    return _mapping(payload["recording"])


def _segments(recording: dict[str, object]) -> list[dict[str, object]]:
    raw = recording["segments"]
    assert isinstance(raw, list), raw
    return [_mapping(item) for item in cast("list[object]", raw)]


def _show(runner: Runner, env: dict[str, str], *args: str) -> dict[str, object]:
    result = runner.invoke("lion", "watch", "show", *args, "--json", env=env, expected=0)
    return _json(result)


def _events(payload: dict[str, object]) -> list[dict[str, object]]:
    raw = payload["events"]
    assert isinstance(raw, list), raw
    return [_mapping(item) for item in cast("list[object]", raw)]


def _command_texts(events: list[dict[str, object]]) -> list[str]:
    return [str(event["text"]) for event in events if event["kind"] in ("cmd", "comment")]


def _recording_dir(env: dict[str, str]) -> Path:
    return Path(env["XDG_DATA_HOME"]) / "lion" / "recordings"


def test_lifecycle_capture_stop_show(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """Capture commands, comments, a failure and a secret; stop and show them."""
    _install(runner, env)
    start = runner("lion", "watch", "start", "--note", "e2e lifecycle", env=env)
    assert "Recording started" in start

    with Zsh(env, tmp_path / "zdot") as shell:
        _ = shell.wait_prompt()
        _ = shell.run("echo hi")
        _ = shell.run("false")
        _ = shell.run("setopt interactive_comments")
        _ = shell.run("# a comment line")
        _ = shell.run("export API_KEY=supersecret-e2e")
        _ = shell.paste_and_run("echo multi-one\necho multi-two")

        live = _active(runner, env)
        assert live["status"] == "open"
        assert live["note"] == "e2e lifecycle"
        segments = _segments(live)
        assert len(segments) == 1
        assert segments[0]["alive"] is True
        assert segments[0]["commands"] == 5
        assert segments[0]["comments"] == 1

    ended = _segments(_active(runner, env))
    assert ended[0]["alive"] is False
    assert ended[0]["end_reason"] == "gone"

    stop = runner("lion", "watch", "stop", env=env)
    assert "Recording stopped" in stop
    assert not (_recording_dir(env) / "active").exists()

    events = _events(_show(runner, env, "latest"))
    texts = _command_texts(events)
    assert "echo hi" in texts
    assert "# a comment line" in texts
    assert "echo multi-one\necho multi-two" in texts
    assert all("supersecret-e2e" not in text for text in texts)
    assert "export API_KEY=***" in texts
    results = [event for event in events if event["kind"] == "result"]
    assert any(event["status"] == 1 for event in results)
    assert all(isinstance(event["duration_ms"], float) for event in results)
    exits = [event for event in events if event["kind"] == "exit"]
    assert len(exits) == 1


def test_comment_inputs_are_not_doubled(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """A mixed pasted block is one command; comment-only input is one comment."""
    _install(runner, env)
    _ = runner("lion", "watch", "start", env=env)

    with Zsh(env, tmp_path / "zdot") as shell:
        _ = shell.wait_prompt()
        _ = shell.run("setopt interactive_comments")
        mixed = "# step info\necho mixed-ran"
        _ = shell.paste_and_run(mixed)
        _ = shell.paste_and_run("# alpha\n# beta")
        _ = shell.run("# standalone")

    events = _events(_show(runner, env, "latest"))
    commands = [event for event in events if event["kind"] == "cmd"]
    comments = [event for event in events if event["kind"] == "comment"]
    assert [event["text"] for event in commands].count(mixed) == 1
    assert all(mixed not in str(event["text"]) for event in comments)
    assert {str(event["text"]) for event in comments} == {"# alpha\n# beta", "# standalone"}


def test_exec_zsh_keeps_recording_readable(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """``exec zsh`` keeps the PID, starts a second segment and loses no data."""
    _install(runner, env)
    _ = runner("lion", "watch", "start", env=env)

    with Zsh(env, tmp_path / "zdot") as shell:
        _ = shell.wait_prompt()
        pid = shell.pid
        _ = shell.run("echo before-exec")
        shell.send("exec zsh\r")
        _ = shell.wait_prompt()
        _ = shell.run("echo after-exec")

        segments = _segments(_active(runner, env))
        assert len(segments) == 2
        assert {segment["pid"] for segment in segments} == {pid}
        # The takeover ended the first segment; only the newest one is alive.
        assert segments[0]["alive"] is False
        assert segments[0]["end_reason"] == "superseded"
        assert segments[1]["alive"] is True

    payload = _show(runner, env, "latest")
    texts = _command_texts(_events(payload))
    assert "echo before-exec" in texts
    assert "echo after-exec" in texts
    segments = _segments(_mapping(payload["recording"]))
    assert len(segments) == 2
    assert all(segment["alive"] is False for segment in segments)
    # The takeover date is stable: the older segment keeps 'superseded' after
    # the process ended; only the newest segment ends by liveness.
    assert segments[0]["end_reason"] == "superseded"
    assert segments[1]["end_reason"] == "gone"
    # The first segment has no exit event at all: a missing end is never success.
    events = _events(payload)
    first_segment = str(segments[0]["name"])
    assert not any(event["kind"] == "exit" and event["segment"] == first_segment for event in events)
    second_segment = str(segments[1]["name"])
    assert any(event["kind"] == "exit" and event["segment"] == second_segment for event in events)


def test_resourcing_keeps_segment_and_result(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """A repeated integration load keeps the segment and the pending result."""
    _install(runner, env)
    _ = runner("lion", "watch", "start", env=env)

    with Zsh(env, tmp_path / "zdot") as shell:
        _ = shell.wait_prompt()
        _ = shell.run("echo first")
        _ = shell.run('source "$HOME/.zshrc"')
        _ = shell.run("echo second")

        segments = _segments(_active(runner, env))
        assert len(segments) == 1
        # The source command itself gets its result: the reload kept the state.
        assert segments[0]["commands"] == 3
        assert segments[0]["results"] == 3

    payload = _show(runner, env, "latest")
    segments = _segments(_mapping(payload["recording"]))
    assert len(segments) == 1
    texts = _command_texts(_events(payload))
    assert "echo first" in texts
    assert 'source "$HOME/.zshrc"' in texts
    assert "echo second" in texts


def test_window_close_ends_segment(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """Closing the terminal window (SIGHUP) leaves a readable, ended segment."""
    _install(runner, env)
    _ = runner("lion", "watch", "start", env=env)

    shell = Zsh(env, tmp_path / "zdot")
    _ = shell.wait_prompt()
    _ = shell.run("echo hup-me")
    shell.close_window()
    deadline = time.time() + 5
    while shell.alive() and time.time() < deadline:
        time.sleep(0.05)
    assert not shell.alive()

    segments = _segments(_active(runner, env))
    assert segments[0]["alive"] is False
    payload = _show(runner, env, "latest")
    assert "echo hup-me" in _command_texts(_events(payload))
    # SIGHUP runs zshexit, so the exit hint is present even though the segment
    # is ended by liveness.
    assert any(event["kind"] == "exit" for event in _events(payload))
    shell.close()


def test_parallel_shells_keep_separate_segments(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """Two interleaved shells write separate segments without mixing."""
    _install(runner, env)
    _ = runner("lion", "watch", "start", env=env)

    with Zsh(env, tmp_path / "zdota") as first, Zsh(env, tmp_path / "zdotb") as second:
        _ = first.wait_prompt()
        _ = second.wait_prompt()
        _ = first.run("echo A1")
        _ = second.run("echo B1")
        _ = first.run("echo A2")
        _ = second.run("echo B2")

        segments = _segments(_active(runner, env))
        assert len(segments) == 2
        assert len({segment["pid"] for segment in segments}) == 2

        # Read while both shells are open: the only commands are the four
        # interleaved echos, in execution order (no harness 'exit' yet).
        payload = _show(runner, env, "latest")
        events = _events(payload)
        commands = [str(event["text"]) for event in events if event["kind"] == "cmd"]
        assert commands == ["echo A1", "echo B1", "echo A2", "echo B2"]
        order = [segment["name"] for segment in _segments(_mapping(payload["recording"]))]
        starts = [str(event["segment"]) for event in events if event["kind"] == "segment"]
        assert starts == order

    payload = _show(runner, env, "latest")
    events = _events(payload)
    by_segment: dict[str, set[str]] = {}
    for event in events:
        if event["kind"] == "cmd":
            by_segment.setdefault(str(event["segment"]), set()).add(str(event["text"]))
    assert len(by_segment) == 2
    groups = list(by_segment.values())
    a_group = next(group for group in groups if "echo A1" in group)
    b_group = next(group for group in groups if "echo B1" in group)
    assert a_group is not b_group
    assert {"echo A1", "echo A2"} <= a_group
    assert {"echo B1", "echo B2"} <= b_group
    assert "echo B1" not in a_group and "echo A1" not in b_group


def test_prompt_path_reads_pointer_and_loads_silently(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """Loading prints nothing; the p10k segment follows the pointer file."""
    _install(runner, env)
    _ = runner("lion", "watch", "start", env=env)

    shell = Zsh(env, tmp_path / "zdot")
    first = shell.wait_prompt()
    # Only the sentinel prompt may be on the freshly rendered screen: the load
    # itself must print nothing (and no instant-prompt warning).
    residue = strip_prompt(first).replace(" ", "").strip()
    assert residue == "", repr(first)
    assert "warning" not in first.lower()

    _ = shell.run('p10k() { print -r -- "P10K:$*" }')
    _ = shell.run("POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS=(time)")
    active_out = shell.run("prompt_lion_watch")
    assert "P10K:segment -f 196 -t" in active_out

    _ = runner("lion", "watch", "stop", env=env)
    idle_out = shell.run("prompt_lion_watch")
    assert "P10K:" not in idle_out
    payload = _json(runner.invoke("lion", "watch", "status", "--json", env=env, expected=0))
    assert payload == {"active": False}
    shell.close()


def test_failure_paths(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """The loud failure paths and the removal warning."""
    failed = runner.invoke("lion", "watch", "start", env=env, expected=1)
    assert "lion shell insert" in failed.stderr
    assert not _recording_dir(env).exists()

    failed = runner.invoke("lion", "watch", "stop", env=env, expected=1)
    assert "No active recording" in failed.stderr

    damaged = _recording_dir(env) / "2026-10-05T20-00-00.000000Z"
    damaged.mkdir(parents=True)
    (damaged / "meta.toml").write_text("broken = [")
    failed = runner.invoke("lion", "watch", "list", env=env, expected=1)
    assert "meta.toml" in failed.stderr
    shutil.rmtree(damaged)

    _install(runner, env)
    _ = runner("lion", "watch", "start", env=env)
    remove = runner.invoke("lion", "shell", "remove", env=env, expected=0)
    assert "removed" in remove.stdout
    assert "recording is active" in remove.stderr

    with Zsh(env, tmp_path / "zdot") as shell:
        _ = shell.wait_prompt()
        _ = shell.run("echo fresh-shell-is-silent")

    payload = _show(runner, env, "latest")
    assert "echo fresh-shell-is-silent" not in _command_texts(_events(payload))


def test_read_commands_write_nothing(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """status, list, show and lion status leave every file byte-identical."""
    _install(runner, env)
    _ = runner("lion", "scan", env=env)
    _ = runner("lion", "watch", "start", env=env)
    with Zsh(env, tmp_path / "zdot") as shell:
        _ = shell.wait_prompt()
        _ = shell.run("echo baseline")

    home = Path(env["HOME"])
    data = Path(env["XDG_DATA_HOME"])
    before_home = manifest(home)
    before_data = manifest(data)

    _ = runner("lion", "watch", "status", env=env)
    _ = runner("lion", "watch", "status", "--json", env=env)
    _ = runner("lion", "watch", "list", env=env)
    _ = runner("lion", "watch", "list", "--json", env=env)
    _ = runner("lion", "watch", "show", env=env)
    _ = runner("lion", "watch", "show", "latest", "--json", env=env)
    status = runner.invoke("lion", "status", env=env, expected=0)
    assert "Watch is recording since" in status.stdout

    assert manifest(home) == before_home
    assert manifest(data) == before_data


def test_doctor_reports_shell_and_watch(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """Doctor sees the integration and the active recording, read-only."""
    _install(runner, env)
    _ = runner("lion", "watch", "start", env=env)

    payload = _json(runner.invoke("lion", "doctor", "--json", env=env, expected=0))
    findings = doctor_findings(payload)
    assert findings["shell.inserted"]["status"] == "ok"
    assert findings["watch.active"]["status"] == "ok"
    assert findings["watch.records"]["status"] == "ok"

    _ = runner("lion", "shell", "remove", env=env)
    payload = _json(runner.invoke("lion", "doctor", "--json", env=env, expected=0))
    findings = doctor_findings(payload)
    assert findings["shell.inserted"]["status"] == "skip"
    # The integration warning is gone with the artifact; the record stays active.
    assert findings["watch.active"]["status"] == "ok"


def test_zshrc_block_path_without_shlib(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """Without shlib the integration lives as a marked block in ``~/.zshrc``."""
    home = Path(env["HOME"])
    original = "# user shell configuration\nalias ll='ls -l'\n"
    (home / ".zshrc").write_text(original)

    insert = runner.invoke("lion", "shell", "insert", env=env, expected=0)
    assert "Watch integration inserted" in insert.stdout
    payload = _json(runner.invoke("lion", "shell", "status", "--json", env=env, expected=0))
    assert payload["mode"] == "zshrc"
    assert payload["inserted"] is True
    assert payload["modified"] is False
    assert payload["warnings"] == []
    text = (home / ".zshrc").read_text()
    assert text.startswith(original)
    assert ">>>>>> LION watch" in text

    _ = runner("lion", "watch", "start", "--note", "zshrc block", env=env)
    with Zsh(env, tmp_path / "zdot") as shell:
        _ = shell.wait_prompt()
        _ = shell.run("echo block-path-command")
    _ = runner("lion", "watch", "stop", env=env)
    assert "echo block-path-command" in _command_texts(_events(_show(runner, env, "latest")))

    remove = runner.invoke("lion", "shell", "remove", env=env, expected=0)
    assert "Watch integration removed" in remove.stdout
    assert (home / ".zshrc").read_text() == original
    payload = _json(runner.invoke("lion", "shell", "status", "--json", env=env, expected=0))
    assert payload["inserted"] is False


def test_adoption_in_a_running_shell(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """A shell that was open before ``start`` joins with its next prompt.

    This pins the documented pointer lag (``docs/reference/recordings.md``):
    the hooks refresh the pointer in ``precmd``, so the command typed in the
    window between ``start`` and the next prompt is dropped and the following
    command is captured.
    """
    _install(runner, env)
    with Zsh(env, tmp_path / "zdot") as shell:
        _ = shell.wait_prompt()
        _ = shell.run("echo before-start")
        assert _json(runner.invoke("lion", "watch", "status", "--json", env=env, expected=0)) == {"active": False}

        _ = runner("lion", "watch", "start", "--note", "adoption", env=env)
        _ = shell.run(":")  # the join command inside the lag window is dropped
        _ = shell.run("echo after-start")

        segments = _segments(_active(runner, env))
        assert len(segments) == 1
        assert segments[0]["commands"] == 1

    _ = runner("lion", "watch", "stop", env=env)
    texts = _command_texts(_events(_show(runner, env, "latest")))
    assert "echo before-start" not in texts
    assert ":" not in texts
    assert "echo after-start" in texts


def test_record_switch_and_resume_across_shells(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """A new shell resumes an open record; a running shell switches records."""
    _install(runner, env)
    _ = runner("lion", "watch", "start", "--note", "first record", env=env)
    first_ref = str(_active(runner, env)["ref"])

    first = Zsh(env, tmp_path / "zdot-a")
    _ = first.wait_prompt()
    _ = first.run("echo in-first-record")
    first.close_window()
    deadline = time.monotonic() + 5
    while first.alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not first.alive()

    with Zsh(env, tmp_path / "zdot-b") as second:
        _ = second.wait_prompt()
        _ = second.run("echo resumed-in-first")
        # The switch applies at the next prompt (same pointer lag); the join
        # command is never attributed to the new record.
        _ = runner("lion", "watch", "stop", env=env)
        _ = runner("lion", "watch", "start", "--note", "second record", env=env)
        second_ref = str(_active(runner, env)["ref"])
        assert second_ref != first_ref
        _ = second.run(":")
        _ = second.run("echo switched-to-second")

    _ = runner("lion", "watch", "stop", env=env)
    first.close()

    first_payload = _show(runner, env, first_ref)
    first_texts = _command_texts(_events(first_payload))
    assert "echo in-first-record" in first_texts
    assert "echo resumed-in-first" in first_texts
    assert "echo switched-to-second" not in first_texts
    first_segments = _segments(_mapping(first_payload["recording"]))
    assert len(first_segments) == 2

    second_payload = _show(runner, env, second_ref)
    second_texts = _command_texts(_events(second_payload))
    assert "echo switched-to-second" in second_texts
    assert "echo resumed-in-first" not in second_texts
