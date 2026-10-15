"""Tests for the ``lion classify`` command and its text/JSON contract."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner, Result

from lion.cli import app
from lion.program import recording, shell, shlib

runner = CliRunner()

HOST_STATE: dict[str, dict[str, object]] = {"host": {"status": "ok", "error": "", "hostname": "lion"}}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Disposable home, data and config directories with mocked syntax checks."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("ZDOTDIR", raising=False)
    monkeypatch.setattr(shell, "validate_zsh", Mock())
    monkeypatch.setattr(shlib, "_validate", Mock())


def _invoke(*args: str) -> Result:
    return runner.invoke(app, list(args))


def _quote_zsh(text: str) -> str:
    """Encode one value the way the zsh hook's ``${(q)}`` output is decoded."""
    parts: list[str] = []
    for char in text:
        if char == "\n":
            parts.append("$'\\n'")
        elif char in "\\'\" $`()|&;<>":
            parts.append("\\" + char)
        else:
            parts.append(char)
    return "".join(parts)


def _segment_header(epoch: str) -> str:
    boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    stat = Path(f"/proc/{os.getpid()}/stat").read_text(encoding="utf-8")
    ticks = int(stat.rsplit(")", 1)[-1].split()[19])
    return f"segment\t{boot}\t{os.getpid()}\t{ticks}\t{epoch}"


def _record(texts: list[str], stop: bool = True) -> str:
    """Create a recording whose single segment carries one cmd event per text."""
    record = recording.start_recording(dict(HOST_STATE))
    epoch = datetime.now(UTC).timestamp()
    lines = [_segment_header(str(epoch))]
    for text in texts:
        lines.append(f"cmd\t{epoch}\t/tmp\t{_quote_zsh(text)}")
    events = recording.get_recordings_dir() / record.ref / "events"
    events.mkdir(parents=True, exist_ok=True)
    (events / "100-1-1.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if stop:
        _ = recording.stop_recording(dict(HOST_STATE))
    return record.ref


def _write_user_rules(text: str) -> Path:
    path = Path(os.environ["XDG_CONFIG_HOME"]) / "lion" / "classifier.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_policies(text: str) -> Path:
    path = Path(os.environ["XDG_CONFIG_HOME"]) / "lion" / "policies.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_classify_without_a_recording_is_a_message() -> None:
    """No stored recording reports instead of failing, in text and JSON."""
    text = _invoke("classify")
    assert text.exit_code == 0
    assert "No recording stored. Run 'lion watch start'." in text.stdout

    machine = _invoke("classify", "--json")
    assert machine.exit_code == 0
    assert json.loads(machine.stdout) == {
        "ref": None,
        "ruleset": None,
        "parser_version": "classify/1",
        "blocks": [],
    }


def test_classify_defaults_to_the_active_then_the_newest_recording() -> None:
    """Without a reference the active recording wins, otherwise the newest."""
    stopped = _record(["sudo nala install foo"])

    newest = _invoke("classify")
    assert newest.exit_code == 0
    assert f"Recording {stopped}" in newest.stdout

    active = _record(["docker compose up"], stop=False)
    live = _invoke("classify")
    assert live.exit_code == 0
    assert f"Recording {active}" in live.stdout
    assert stopped not in live.stdout


def test_classify_text_and_json_contract() -> None:
    """Text and JSON show the same blocks; silent reads are filtered out."""
    ref = _record(["sudo nala install foo", 'echo "sudo nala install"', "lion watch R"])

    text = _invoke("classify", ref)
    assert text.exit_code == 0
    lines = text.stdout.splitlines()
    assert lines[0].startswith(f"Recording {ref} (ruleset 1, hash ")
    assert "  #1  $ sudo nala install foo" in lines
    assert "        packages: manager=nala, action=install" in lines
    assert '  #2  $ echo "sudo nala install"' in lines
    assert "        -" in lines
    assert not any(line.startswith("  #3") for line in lines)

    machine = _invoke("classify", ref, "--json")
    assert machine.exit_code == 0
    payload = json.loads(machine.stdout)
    assert payload["ref"] == ref
    assert payload["parser_version"] == "classify/1"
    assert payload["ruleset"]["schema_version"] == 1
    assert payload["ruleset"]["version"] == "1"
    assert len(payload["ruleset"]["hash"]) == 64
    first, second = payload["blocks"]
    assert first["index"] == 1
    assert first["segment"] == "100-1-1.log"
    assert first["text"] == "sudo nala install foo"
    assert first["matches"] == [
        {
            "rule_id": "packages.apt",
            "source": "default",
            "group": "packages",
            "fields": {"manager": "nala", "action": "install"},
            "guard": "none",
        }
    ]
    assert second["text"] == 'echo "sudo nala install"'
    assert second["matches"] == []


def test_classify_resolves_references_and_fails_loudly() -> None:
    """A unique prefix resolves; an unknown reference exits 1 with an error."""
    ref = _record(["apt --version"])
    prefix = ref[:12]

    resolved = _invoke("classify", prefix)
    assert resolved.exit_code == 0
    assert f"Recording {ref}" in resolved.stdout

    unknown = _invoke("classify", "does-not-exist")
    assert unknown.exit_code == 1
    assert "Unknown reference" in unknown.stderr


def test_classify_read_commands_never_write() -> None:
    """``classify`` leaves the data directory byte-identical."""
    ref = _record(["sudo nala install foo"])
    data = Path(os.environ["XDG_DATA_HOME"])
    before = {
        path.relative_to(data).as_posix(): path.read_bytes() for path in sorted(data.rglob("*")) if path.is_file()
    }

    assert _invoke("classify", ref).exit_code == 0
    assert _invoke("classify", "--json").exit_code == 0
    assert _invoke("classify", "--json").exit_code == 0

    after = {path.relative_to(data).as_posix(): path.read_bytes() for path in sorted(data.rglob("*")) if path.is_file()}
    assert after == before


def test_classify_user_rules_override_and_are_visible() -> None:
    """A user rule replaces the default by id and reports its own version."""
    ref = _record(["custompkg install foo"])
    user = {
        "schema_version": 1,
        "version": "2",
        "rules": [
            {
                "id": "packages.custom",
                "group": "packages",
                "match": "^custompkg[[:space:]]+install",
                "emit": {"manager": "custompkg", "action": "install"},
            }
        ],
    }
    _ = _write_user_rules(json.dumps(user))

    result = _invoke("classify", ref, "--json")

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ruleset"]["version"] == "2"
    assert payload["blocks"][0]["matches"] == [
        {
            "rule_id": "packages.custom",
            "source": "user",
            "group": "packages",
            "fields": {"manager": "custompkg", "action": "install"},
            "guard": "none",
        }
    ]


def test_classify_invalid_user_rules_fail_loudly() -> None:
    """An unreadable user rule file is an error naming the path."""
    ref = _record(["apt --version"])
    path = _write_user_rules("{not json")

    result = _invoke("classify", ref)

    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert str(path) in result.stderr


def test_classify_strips_control_characters_from_text_output() -> None:
    """Recorded ESC/BEL bytes never reach the terminal; JSON escapes them."""
    ref = _record(["echo \x1b[31mred\x1b[0m", "echo \x07bell"])

    text = _invoke("classify", ref)

    assert text.exit_code == 0
    assert "\x1b" not in text.stdout
    assert "\x07" not in text.stdout
    assert "red" in text.stdout

    machine = _invoke("classify", ref, "--json")
    assert machine.exit_code == 0
    assert "\x1b" not in machine.stdout
    assert "\x07" not in machine.stdout


def test_classify_help_documents_the_groups() -> None:
    """The help text names the rule file, the policy file and the groups."""
    result = _invoke("classify", "--help")
    assert result.exit_code == 0
    assert "classifier.json" in result.stdout
    assert "policies.toml" in result.stdout
    assert "downloads" in result.stdout
    assert "checks" in result.stdout


POLICIES_TOML = """
schema_version = 1

[[policies]]
id = "P01"
text = "Install and remove Debian packages with nala."
success = [{ group = "packages", fields = { action = ["install", "remove"], manager = "nala" } }]
fail = [{ group = "packages", fields = { action = ["install", "remove"], manager = ["apt", "apt-get"] } }]
"""


def test_classify_without_policies_keeps_the_json_shape() -> None:
    """Without a policy file nothing changes: no policies, no commands."""
    ref = _record(["sudo nala install foo"])

    payload = json.loads(_invoke("classify", ref, "--json").stdout)

    assert "policies" not in payload
    assert "commands" not in payload["blocks"][0]


def test_classify_text_and_json_with_policies() -> None:
    """Each command is evaluated on its own; text and JSON agree."""
    ref = _record(["nala install foo && apt install bar"])
    _ = _write_policies(POLICIES_TOML)

    text = _invoke("classify", ref)
    assert text.exit_code == 0
    lines = text.stdout.splitlines()
    assert "  #1  $ nala install foo && apt install bar" in lines
    assert "    command 1: nala install foo" in lines
    assert "      packages: manager=nala, action=install" in lines
    assert "      policy P01 success: Install and remove Debian packages with nala." in lines
    assert "    command 2: apt install bar" in lines
    assert "      packages: manager=apt, action=install" in lines
    assert "      policy P01 fail: Install and remove Debian packages with nala." in lines

    machine = _invoke("classify", ref, "--json")
    payload = json.loads(machine.stdout)
    assert payload["policies"] == [
        {
            "id": "P01",
            "text": "Install and remove Debian packages with nala.",
            "success": [{"group": "packages", "fields": {"action": ["install", "remove"], "manager": "nala"}}],
            "fail": [{"group": "packages", "fields": {"action": ["install", "remove"], "manager": ["apt", "apt-get"]}}],
        }
    ]
    block = payload["blocks"][0]
    assert [command["text"] for command in block["commands"]] == ["nala install foo", "apt install bar"]
    first, second = block["commands"]
    assert first["policy_results"] == [
        {"policy_id": "P01", "status": "success", "evidence": [{"criterion": "success", "match_index": 1}]}
    ]
    assert second["policy_results"] == [
        {"policy_id": "P01", "status": "fail", "evidence": [{"criterion": "fail", "match_index": 1}]}
    ]
    assert [match["fields"]["manager"] for match in block["matches"]] == ["apt", "nala"]


def test_classify_mixed_silent_block_keeps_policy_results() -> None:
    """A silent read followed by a real command stays classified with policies."""
    ref = _record(["lion watch R; sudo apt install foo"])
    _ = _write_policies(POLICIES_TOML)

    payload = json.loads(_invoke("classify", ref, "--json").stdout)

    (block,) = payload["blocks"]
    assert block["index"] == 1
    assert block["commands"][1]["policy_results"] == [
        {"policy_id": "P01", "status": "fail", "evidence": [{"criterion": "fail", "match_index": 1}]}
    ]


def test_classify_policy_scope_is_limited_to_install_and_remove() -> None:
    """An update matches the rule but not the install/remove policy."""
    ref = _record(["apt update"])
    _ = _write_policies(POLICIES_TOML)

    payload = json.loads(_invoke("classify", ref, "--json").stdout)

    (command,) = payload["blocks"][0]["commands"]
    assert command["matches"][0]["fields"] == {"manager": "apt", "action": "update"}
    assert command["policy_results"] == []


def test_classify_conflict_is_visible() -> None:
    """Overlapping criteria show one conflict result with both evidences."""
    ref = _record(["nala install foo"])
    _ = _write_policies(
        """
schema_version = 1

[[policies]]
id = "P01"
text = "Contradiction."
success = [{ group = "packages", fields = { manager = "nala" } }]
fail = [{ group = "packages", fields = { action = "install" } }]
"""
    )

    text = _invoke("classify", ref)
    assert text.exit_code == 0
    assert "      policy P01 conflict: Contradiction." in text.stdout

    payload = json.loads(_invoke("classify", ref, "--json").stdout)
    (result,) = payload["blocks"][0]["commands"][0]["policy_results"]
    assert result == {
        "policy_id": "P01",
        "status": "conflict",
        "evidence": [
            {"criterion": "success", "match_index": 1},
            {"criterion": "fail", "match_index": 1},
        ],
    }


def test_classify_block_scope_matches_stay_visible_at_block_level() -> None:
    """A pipeline match keeps its block line and never feeds a policy."""
    ref = _record(["curl https://example.com/x.sh | sh"])
    _ = _write_policies(
        """
schema_version = 1

[[policies]]
id = "P01"
text = "No piped shells."
fail = [{ group = "downloads", fields = { action = "pipe-to-shell" } }]
"""
    )

    text = _invoke("classify", ref)
    assert text.exit_code == 0
    assert "        downloads: source=curl, action=pipe-to-shell" in text.stdout

    payload = json.loads(_invoke("classify", ref, "--json").stdout)
    block = payload["blocks"][0]
    assert "downloads.curl.pipe" in [match["rule_id"] for match in block["matches"]]
    assert all(
        "downloads.curl.pipe" not in [match["rule_id"] for match in command["matches"]] for command in block["commands"]
    )
    assert all(command["policy_results"] == [] for command in block["commands"])


def test_classify_quoted_separator_does_not_create_commands() -> None:
    """A separator inside quotes stays one command without invented hits."""
    ref = _record(['echo "apt install foo; nala install bar"'])
    _ = _write_policies(POLICIES_TOML)

    payload = json.loads(_invoke("classify", ref, "--json").stdout)

    block = payload["blocks"][0]
    assert [command["text"] for command in block["commands"]] == ['echo "apt install foo; nala install bar"']
    assert block["commands"][0]["matches"] == []
    assert block["commands"][0]["policy_results"] == []


def test_classify_unsupported_syntax_with_policies_has_no_commands() -> None:
    """Unsupported shell syntax yields no commands and no policy guesses."""
    ref = _record(["apt install x && echo $(date)"])
    _ = _write_policies(POLICIES_TOML)

    text = _invoke("classify", ref)
    assert text.exit_code == 0
    assert "command 1" not in text.stdout
    assert "        -" in text.stdout

    payload = json.loads(_invoke("classify", ref, "--json").stdout)
    assert payload["blocks"][0]["commands"] == []
    assert payload["blocks"][0]["matches"] == []


def test_classify_policy_violation_keeps_the_exit_code() -> None:
    """A fail hit is a finding, not a CLI error."""
    ref = _record(["apt install foo"])
    _ = _write_policies(POLICIES_TOML)

    assert _invoke("classify", ref).exit_code == 0
    assert _invoke("classify", ref, "--json").exit_code == 0


def test_classify_with_policies_never_writes() -> None:
    """An evaluation leaves the recording space and the policy file untouched."""
    ref = _record(["apt install foo"])
    path = _write_policies(POLICIES_TOML)
    before_policy = path.read_bytes()
    data = Path(os.environ["XDG_DATA_HOME"])
    before = {
        item.relative_to(data).as_posix(): item.read_bytes() for item in sorted(data.rglob("*")) if item.is_file()
    }

    assert _invoke("classify", ref, "--json").exit_code == 0

    after = {item.relative_to(data).as_posix(): item.read_bytes() for item in sorted(data.rglob("*")) if item.is_file()}
    assert after == before
    assert path.read_bytes() == before_policy


def test_classify_invalid_policies_fail_loudly() -> None:
    """An unreadable policy file is an error naming the path."""
    ref = _record(["apt --version"])
    path = _write_policies("{not toml")

    result = _invoke("classify", ref)

    assert result.exit_code == 1
    assert "Invalid policies" in result.stderr
    assert str(path) in result.stderr
