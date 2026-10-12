r"""Masking and encoding: the recording stores what ran, best-effort.

Runs inside the disposable container (see ``conftest.py``). The pane runs under
``C.UTF-8`` (a UTF-8 pane is required for tmux to pass non-ASCII input
through), so the recording must store the typed UTF-8 text exactly; the
byte-escaped C-locale form of ``${(q)}`` is covered by the unit tests. Masking
is best-effort, not a security boundary: the scenario pins the documented
variants (bare, single- and double-quoted values, ``Authorization:`` headers,
``LION_WATCH_MASK=0`` opt-out) and the 4096 character cap. All credentials are
synthetic.
"""

import json
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


def _events(payload: dict[str, object]) -> list[dict[str, object]]:
    raw = payload["events"]
    assert isinstance(raw, list), raw
    return [_mapping(item) for item in cast("list[object]", raw)]


def _command_texts(events: list[dict[str, object]]) -> list[str]:
    return [str(event["text"]) for event in events if event["kind"] in ("cmd", "comment")]


def test_masking_variants_utf8_and_cap(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """Quote variants, Authorization, opt-out, UTF-8 fidelity and the cap."""
    _install(runner, env)
    _ = runner("lion", "watch", "start", "--note", "masking", env=env)

    long_command = "echo " + "a" * 5000
    with Zsh(env, tmp_path / "zdot", locale="C.UTF-8") as shell:
        _ = shell.wait_prompt()
        _ = shell.run('export API_KEY="double-secret"')
        _ = shell.run("export API_KEY='single-secret'")
        _ = shell.run("export API_KEY=bare-secret")
        _ = shell.run('echo "Authorization: Bearer tok-123"')
        _ = shell.run("export LION_WATCH_MASK=0")
        _ = shell.run("export API_KEY=optout-secret")
        _ = shell.run("export LION_WATCH_MASK=1")
        _ = shell.run('export API_KEY="ümläut-secret-ß"')
        _ = shell.run('echo "grüße straße 日本語"')
        _ = shell.run(long_command)

    _ = runner("lion", "watch", "stop", env=env)
    events = _events(_json(runner.invoke("lion", "watch", "show", "latest", "--json", env=env, expected=0)))
    texts = _command_texts(events)

    assert texts.count("export API_KEY=***") == 4
    assert "export API_KEY=optout-secret" in texts
    for secret in ("double-secret", "single-secret", "bare-secret", "tok-123", "ümläut-secret-ß"):
        assert all(secret not in text for text in texts)
    authorization = next(text for text in texts if "Authorization:" in text)
    assert "Authorization: ***" in authorization
    assert 'echo "grüße straße 日本語"' in texts
    truncated = next(text for text in texts if text.startswith("echo aaa"))
    assert truncated != long_command
    assert len(truncated) <= 4096
