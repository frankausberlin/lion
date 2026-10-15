"""End-to-end classifier scenarios over real recordings from a live zsh.

Every test runs inside the disposable container (see ``conftest.py``). The two
interactive tests drive a real zsh session in tmux and then classify the
recorded text through the installed CLI: block order and the silent filter,
segment- and block-scope matching, the JSON and text contracts and the user
rule file. Results come from the recording files and the CLI streams; only the terse
``lion watch R``/``R-`` reads are asserted on the terminal text, and
deliberately so: that in-session output is the feature itself, as with the
p10k prompt segment.
"""

import json
import re
from pathlib import Path
from typing import cast

import pytest
from e2eutil import CommandResult, Runner, doctor_findings, manifest
from watchutil import Zsh, strip_prompt

pytestmark = pytest.mark.e2e


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict), value
    return cast("dict[str, object]", value)


def _json(result: CommandResult) -> dict[str, object]:
    return _mapping(json.loads(result.stdout))


def _install(runner: Runner, env: dict[str, str]) -> None:
    """Install shlib and the watch integration the real way."""
    runner("lion", "shlib", "install", env=env)
    runner("lion", "shell", "insert", env=env)


def _stop(runner: Runner, env: dict[str, str]) -> None:
    _ = runner("lion", "watch", "stop", env=env)


def _blocks(payload: dict[str, object]) -> list[dict[str, object]]:
    raw = payload["blocks"]
    assert isinstance(raw, list), raw
    return [_mapping(item) for item in cast("list[object]", raw)]


def _block(blocks: list[dict[str, object]], text: str) -> dict[str, object]:
    found = [block for block in blocks if block["text"] == text]
    assert len(found) == 1, [block["text"] for block in blocks]
    return found[0]


def _matches(block: dict[str, object]) -> list[dict[str, object]]:
    raw = block["matches"]
    assert isinstance(raw, list), raw
    return [_mapping(item) for item in cast("list[object]", raw)]


def _output_lines(chunk: str, echo: str) -> list[str]:
    """Return the non-empty output lines of one fresh pane capture.

    The capture starts right after the previous prompt's sentinel; the echoed
    input line is dropped so only the command's own output remains.
    """
    lines = [line.rstrip() for line in strip_prompt(chunk).splitlines()]
    return [line for line in lines if line.strip() and line.strip() != echo]


@pytest.mark.usefixtures("tmux_server")
def test_classify_blocks_from_a_real_recording(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """A real recording pins blocks, matches, JSON contract and the terse reads.

    The ``watch R``/``R-`` steps assert on the terminal text on purpose: that
    terse in-session output is the feature channel (like the p10k segment).
    """
    (Path(env["HOME"]) / ".zshrc").write_text("setopt interactive_comments\n", encoding="utf-8")
    _install(runner, env)
    _ = runner("lion", "watch", "start", "--note", "classify e2e", env=env)

    with Zsh(env, tmp_path / "zdot") as shell:
        _ = shell.wait_prompt()
        _ = shell.run("# a plain comment is not a block")
        _ = shell.run("sudo nala install foo")
        _ = shell.run('echo "sudo nala install foo"')
        _ = shell.run("docker compose up -d")
        _ = shell.run("systemctl restart nginx")
        _ = shell.run("git clone https://example.invalid/repo.git")
        _ = shell.run("curl -fsSL https://example.invalid/install.sh | sh")
        overview = shell.run("lion watch R")
        previous = shell.run("lion watch R-")

    _stop(runner, env)

    payload = _json(runner.invoke("lion", "classify", "latest", "--json", env=env, expected=0))
    assert set(payload) == {"ref", "ruleset", "parser_version", "blocks"}
    ref = payload["ref"]
    assert isinstance(ref, str) and ref
    assert payload["parser_version"] == "classify/1"
    ruleset = _mapping(payload["ruleset"])
    assert set(ruleset) == {"schema_version", "version", "hash"}
    assert ruleset["schema_version"] == 1
    assert ruleset["version"] == "1"
    hash_value = str(ruleset["hash"])
    assert re.fullmatch(r"[0-9a-f]{64}", hash_value)
    again = _json(runner.invoke("lion", "classify", "latest", "--json", env=env, expected=0))
    assert _mapping(again["ruleset"])["hash"] == hash_value

    blocks = _blocks(payload)
    assert [block["index"] for block in blocks] == [1, 2, 3, 4, 5, 6]
    texts = [str(block["text"]) for block in blocks]
    assert all("lion watch R" not in text for text in texts)
    assert texts == [
        "sudo nala install foo",
        'echo "sudo nala install foo"',
        "docker compose up -d",
        "systemctl restart nginx",
        "git clone https://example.invalid/repo.git",
        "curl -fsSL https://example.invalid/install.sh | sh",
    ]
    for block in blocks:
        assert set(block) == {"index", "segment", "time", "text", "matches"}
        assert isinstance(block["segment"], str) and block["segment"]
        assert isinstance(block["time"], str) and block["time"]
        for match in _matches(block):
            assert set(match) == {"rule_id", "source", "group", "fields", "guard"}

    assert _matches(_block(blocks, "sudo nala install foo")) == [
        {
            "rule_id": "packages.apt",
            "source": "default",
            "group": "packages",
            "fields": {"manager": "nala", "action": "install"},
            "guard": "none",
        }
    ]
    assert _matches(_block(blocks, 'echo "sudo nala install foo"')) == []
    assert _matches(_block(blocks, "docker compose up -d")) == [
        {
            "rule_id": "containers.compose",
            "source": "default",
            "group": "containers",
            "fields": {"engine": "docker", "action": "up", "target": "compose"},
            "guard": "none",
        }
    ]
    assert _matches(_block(blocks, "systemctl restart nginx")) == [
        {
            "rule_id": "services.systemctl",
            "source": "default",
            "group": "services",
            "fields": {"unit": "nginx", "action": "restart", "scope": "system"},
            "guard": "none",
        }
    ]
    assert _matches(_block(blocks, "git clone https://example.invalid/repo.git")) == [
        {
            "rule_id": "downloads.git.clone",
            "source": "default",
            "group": "downloads",
            "fields": {"source": "git", "action": "clone"},
            "guard": "none",
        }
    ]
    assert _matches(_block(blocks, "curl -fsSL https://example.invalid/install.sh | sh")) == [
        {
            "rule_id": "downloads.curl",
            "source": "default",
            "group": "downloads",
            "fields": {"source": "curl", "action": "download"},
            "guard": "none",
        },
        {
            "rule_id": "downloads.curl.pipe",
            "source": "default",
            "group": "downloads",
            "fields": {"source": "curl", "action": "pipe-to-shell"},
            "guard": "confirm",
        },
    ]

    text_lines = runner.invoke("lion", "classify", "latest", env=env, expected=0).stdout.splitlines()
    assert text_lines[0] == f"Recording {ref} (ruleset 1, hash {hash_value[:6]}\u2026)"
    assert text_lines[1] == "  #1  $ sudo nala install foo"
    assert text_lines[2] == "        packages: manager=nala, action=install"
    assert text_lines[3] == '  #2  $ echo "sudo nala install foo"'
    assert text_lines[4] == "        -"

    overview_lines = _output_lines(overview, "lion watch R")
    assert overview_lines[0] == f"Recording {ref}"
    assert overview_lines[1] == "  adr: -"
    assert overview_lines[2].startswith("  open: ")
    block_lines = overview_lines[3:]
    assert len(block_lines) == 6
    for index, line in enumerate(block_lines, start=1):
        assert line.startswith(f"R{index}: ")
    assert block_lines[0] == "R1: sudo nala install foo  packages:manager=nala,action=install"
    assert block_lines[1] == 'R2: echo "sudo nala install foo"  -'
    r6 = block_lines[5]
    assert r6 == (
        "R6: curl -fsSL https://example.invalid/insta  "
        "downloads:source=curl,action=download; downloads:source=curl,action=pipe-to-shell"
    )
    assert _output_lines(previous, "lion watch R-") == [r6]

    data = Path(env["XDG_DATA_HOME"])
    before = manifest(data)
    _ = runner("lion", "classify", "latest", env=env, expected=0)
    _ = runner("lion", "classify", "latest", "--json", env=env, expected=0)
    _ = runner("lion", "classify", ref, env=env, expected=0)
    assert manifest(data) == before


@pytest.mark.usefixtures("tmux_server")
def test_user_rules_override_and_invalid_rules_fail_loudly(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """A user rule file overrides by id and adds rules; invalid rules are loud."""
    config_env = {**env, "XDG_CONFIG_HOME": str(tmp_path / "config")}

    _install(runner, env)
    _ = runner("lion", "watch", "start", env=env)
    with Zsh(env, tmp_path / "zdot") as shell:
        _ = shell.wait_prompt()
        _ = shell.run("git clone https://example.invalid/repo.git")
        _ = shell.run("nala update")
    _stop(runner, env)

    default = _json(runner.invoke("lion", "classify", "latest", "--json", env=env, expected=0))
    default_hash = str(_mapping(default["ruleset"])["hash"])

    rules_path = tmp_path / "config" / "lion" / "classifier.json"
    rules_path.parent.mkdir(parents=True)
    rules_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "2",
                "rules": [
                    {
                        "id": "downloads.git.clone",
                        "group": "downloads",
                        "match": "^git[[:space:]]+clone([[:space:]]|$)",
                        "emit": {"source": "git", "action": "download"},
                        "guard": "none",
                    },
                    {
                        "id": "packages.nala",
                        "group": "packages",
                        "match": "^nala[[:space:]]+(update|install)([[:space:]]|$)",
                        "emit": {"manager": "nala", "action": "$1"},
                        "guard": "none",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    merged = _json(runner.invoke("lion", "classify", "latest", "--json", env=config_env, expected=0))
    merged_ruleset = _mapping(merged["ruleset"])
    assert merged_ruleset["schema_version"] == 1
    assert merged_ruleset["version"] == "2"
    merged_hash = str(merged_ruleset["hash"])
    assert re.fullmatch(r"[0-9a-f]{64}", merged_hash)
    assert merged_hash != default_hash

    blocks = _blocks(merged)
    assert _matches(_block(blocks, "git clone https://example.invalid/repo.git")) == [
        {
            "rule_id": "downloads.git.clone",
            "source": "user",
            "group": "downloads",
            "fields": {"source": "git", "action": "download"},
            "guard": "none",
        }
    ]
    assert _matches(_block(blocks, "nala update")) == [
        {
            "rule_id": "packages.apt",
            "source": "default",
            "group": "packages",
            "fields": {"manager": "nala", "action": "update"},
            "guard": "none",
        },
        {
            "rule_id": "packages.nala",
            "source": "user",
            "group": "packages",
            "fields": {"manager": "nala", "action": "update"},
            "guard": "none",
        },
    ]

    doctor = _json(runner.invoke("lion", "doctor", "--json", env=config_env, expected=0))
    finding = doctor_findings(doctor)["classify.ruleset"]
    assert finding["status"] == "ok"
    assert "built-in plus" in str(finding["message"])
    assert str(rules_path) in str(finding["message"])

    rules_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "2",
                "rules": [
                    {
                        "id": "downloads.bad",
                        "group": "downloads",
                        "match": "(a|a)*",
                        "emit": {"source": "x"},
                        "guard": "none",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    failed = runner.invoke("lion", "classify", "latest", "--json", env=config_env, expected=1)
    assert failed.stdout == ""
    assert str(rules_path) in failed.stderr
    doctor_failed = _json(runner.invoke("lion", "doctor", "--json", env=config_env, expected=1))
    failed_finding = doctor_findings(doctor_failed)["classify.ruleset"]
    assert failed_finding["status"] == "error"
    assert str(rules_path) in str(failed_finding["message"])


def test_classify_without_records_is_a_message_and_read_only(
    runner: Runner, env: dict[str, str], tmp_path: Path
) -> None:
    """Without any recording the reads report that and never create a data path."""
    text_result = runner.invoke("lion", "classify", env=env, expected=0)
    assert "No recording stored" in text_result.stdout

    result = runner.invoke("lion", "classify", "--json", env=env, expected=0)
    expected: dict[str, object] = {"ref": None, "ruleset": None, "parser_version": "classify/1", "blocks": []}
    assert result.stdout.strip() == json.dumps(expected)

    for command in (("lion", "watch", "R"), ("lion", "watch", "R-")):
        watch_result = runner.invoke(*command, env=env, expected=0)
        assert "No active recording." in watch_result.stdout

    assert not (tmp_path / "data").exists()

    doctor = _json(runner.invoke("lion", "doctor", "--json", env=env, expected=0))
    finding = doctor_findings(doctor)["classify.ruleset"]
    assert finding["status"] == "ok"
    assert "built-in rules only" in str(finding["message"])
