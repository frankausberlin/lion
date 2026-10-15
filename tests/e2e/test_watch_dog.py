"""End-to-end guard scenarios for ``lion watch dog`` (ADR 0014/0020).

Scenario one drives a real interactive zsh inside tmux: arming with the first
Enter, executing with the second, editing the buffer to re-arm, and a
non-critical command passing straight through; then ``off`` and a new shell
where the same command runs immediately. Success is asserted on the prompt
returning and on the filesystem (marker directories), not on the transient
``zle -M`` message, which only synchronizes the steps.

Scenario two runs without a shell: the status JSON contract, the doctor
finding for on/fresh, stale and off, an ``on`` refresh after a rule change,
and an ``lstat`` manifest proving that the read commands write nothing.
"""

import json
from pathlib import Path
from typing import cast

import pytest
from e2eutil import Runner, doctor_findings, manifest
from watchutil import Zsh

pytestmark = pytest.mark.e2e


def _install(runner: Runner, env: dict[str, str]) -> None:
    """Install the real shell library and the watch integration."""
    runner("lion", "shlib", "install", env=env)
    runner("lion", "shell", "insert", env=env)


def _artifact(env: dict[str, str]) -> Path:
    return Path(env["XDG_DATA_HOME"]) / "lion" / "dog" / "rules.zsh"


def _status(runner: Runner, env: dict[str, str]) -> dict[str, object]:
    payload = json.loads(runner("lion", "watch", "dog", "status", "--json", env=env))
    assert isinstance(payload, dict)
    return cast("dict[str, object]", payload)


def _doctor(runner: Runner, env: dict[str, str]) -> dict[str, dict[str, object]]:
    payload = json.loads(runner("lion", "doctor", "--json", env=env))
    assert isinstance(payload, dict)
    return doctor_findings(cast("dict[str, object]", payload))


@pytest.mark.usefixtures("tmux_server")
def test_guard_arms_confirms_edits_and_disarms(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """The interactive guard: first Enter arms, second runs, edit re-arms, off frees."""
    (Path(env["HOME"]) / ".zshrc").write_text("setopt interactive_comments\n", encoding="utf-8")
    _install(runner, env)
    started = runner("lion", "watch", "dog", "on", env=env)
    assert "Guard rules written" in started
    assert "(7 confirm rules)" in started
    artifact = _artifact(env)
    assert artifact.is_file()

    target = tmp_path / "doomed"
    target.mkdir()
    edited = tmp_path / "edited"
    edited.mkdir()
    after_off = tmp_path / "after-off"
    after_off.mkdir()

    with Zsh(env, tmp_path / "zdot") as shell:
        _ = shell.wait_prompt()

        # (a) The first Enter arms and executes nothing; the second executes.
        shell.type_text(f"rm -rf {target}")
        shell.send("")  # Enter alone
        assert "LION dog: shell.rm.recursive" in shell.wait_for("LION dog: shell.rm.recursive", timeout=8.0)
        assert target.exists(), "the armed command must not run on the first Enter"
        shell.send("")
        _ = shell.wait_prompt()
        assert not target.exists(), "the second Enter must run the confirmed command"

        # (c) Editing the buffer cancels the armed state and re-arms.
        shell.type_text(f"rm -rf {edited}")
        shell.send("")
        assert "LION dog: shell.rm.recursive" in shell.wait_for("LION dog: shell.rm.recursive", timeout=8.0)
        shell.type_text(" ")  # edit: the buffer no longer equals the armed text
        shell.send("")
        shell.clear_line()
        output = shell.run("echo armed-sync")  # forces a fresh prompt as a sync point
        assert "armed-sync" in output
        assert edited.exists(), "the edited buffer must not run on its next Enter"

        shell.type_text(f"rm -rf {edited}")
        shell.send("")
        assert "LION dog: shell.rm.recursive" in shell.wait_for("LION dog: shell.rm.recursive", timeout=8.0)
        shell.send("")
        _ = shell.wait_prompt()
        assert not edited.exists(), "the regular second Enter still confirms"

        # (b) Non-critical input runs immediately and shows no guard message.
        output = shell.run("echo non-critical-ran")
        assert "non-critical-ran" in output
        assert "LION dog:" not in output

    # (d) After 'off' a new shell loses the guard completely.
    stopped = runner("lion", "watch", "dog", "off", env=env)
    assert "Guard disabled" in stopped
    assert not artifact.exists()
    with Zsh(env, tmp_path / "zdot-off") as shell:
        _ = shell.wait_prompt()
        output = shell.run(f"rm -rf {after_off}")
        assert "LION dog:" not in output
        assert not after_off.exists(), "without the artifact the command runs on the first Enter"


def test_guard_status_doctor_staleness_and_read_only(runner: Runner, env: dict[str, str]) -> None:
    """The command surface: status contract, doctor findings, refresh, read-only reads."""
    _install(runner, env)
    _ = runner("lion", "watch", "dog", "on", env=env)

    status = _status(runner, env)
    assert set(status) == {
        "enabled",
        "config_path",
        "config_present",
        "rules_hash",
        "confirm_rules",
        "artifact_path",
        "artifact_present",
        "artifact_hash",
        "artifact_stale",
        "integration_mode",
        "integration_inserted",
        "warnings",
    }
    assert status["enabled"] is True
    assert status["confirm_rules"] == 7
    assert status["artifact_present"] is True
    assert status["artifact_hash"] == status["rules_hash"]
    assert status["artifact_stale"] is False
    assert status["integration_inserted"] is True
    assert status["warnings"] == []

    findings = _doctor(runner, env)
    assert findings["watch.dog"]["status"] == "ok"
    assert findings["watch.dog"]["message"] == "Guard armed (7 confirm rules)."

    # A user rule changes the merged hash; the artifact is stale until 'on' again.
    user_rules = Path(env["HOME"]) / ".config" / "lion" / "classifier.json"
    user_rules.parent.mkdir(parents=True, exist_ok=True)
    user_rules.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "rules": [
                    {
                        "id": "packages.custom",
                        "group": "packages",
                        "match": "^custompm[[:space:]]+install",
                        "emit": {"manager": "custompm", "action": "install"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    stale = _status(runner, env)
    assert stale["artifact_stale"] is True
    assert any("stale" in str(warning) for warning in cast("list[object]", stale["warnings"]))
    stale_findings = _doctor(runner, env)
    assert stale_findings["watch.dog"]["status"] == "warn"
    assert stale_findings["classify.ruleset"]["status"] == "ok"

    refreshed = runner("lion", "watch", "dog", "on", env=env)
    assert "Guard refreshed" in refreshed
    fresh = _status(runner, env)
    assert fresh["artifact_stale"] is False
    assert fresh["artifact_hash"] == fresh["rules_hash"]

    # Read-only: bare and --json status leave the data directory byte-identical.
    data = Path(env["XDG_DATA_HOME"])
    before = manifest(data)
    _ = runner("lion", "watch", "dog", env=env)
    _ = runner("lion", "watch", "dog", "status", env=env)
    _ = _status(runner, env)
    assert manifest(data) == before
