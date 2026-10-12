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
    """The help text names the rule file and the group vocabulary."""
    result = _invoke("classify", "--help")
    assert result.exit_code == 0
    assert "classifier.json" in result.stdout
    assert "downloads" in result.stdout
    assert "checks" in result.stdout
