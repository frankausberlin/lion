"""End-to-end ADR annotation scenarios with a real interactive zsh inside tmux.

Every test runs inside the disposable container (see ``conftest.py``), installs
the real shlib and watch integration into a temporary home, drives a real shell
and reads the results from the recording files via ``lion records`` — never
from the terminal text. The scenarios assert that markers fold into exactly the
intended ADR, that a marker inside a here-document body stays text, and that
the read commands and the edit flow behave as documented.
"""

import json
from pathlib import Path
from typing import cast

import pytest
from e2eutil import CommandResult, Runner, doctor_findings, manifest
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


def _stop(runner: Runner, env: dict[str, str]) -> None:
    _ = runner("lion", "watch", "stop", env=env)


def test_adr_annotations_from_a_real_session(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """Typed markers and a mixed paste fold into the intended ADR."""
    _install(runner, env)
    _ = runner("lion", "watch", "start", "--note", "e2e adr", env=env)

    with Zsh(env, tmp_path / "zdot") as shell:
        _ = shell.wait_prompt()
        _ = shell.run("setopt interactive_comments")
        _ = shell.run("#+LION: adr.title E2E annotation title")
        _ = shell.run("#+LION: adr.component shell")
        _ = shell.run("echo anchored-command")
        mixed = "cat <<'EOF'\n#-LION: adr.title FAKE\nEOF\n#-LION: adr.decision Real decision from a mixed paste"
        _ = shell.paste_and_run(mixed)
        _ = shell.run("#.LION: adr.status accepted")
        _ = shell.run("#.LION: adr.uses does-not-exist")

    _stop(runner, env)

    detail = _mapping(_json(runner.invoke("lion", "records", "adrs", "latest", "--json", env=env, expected=0))["adr"])
    fields = _mapping(detail["fields"])
    assert detail["complete"] is True
    assert detail["edited"] is False
    assert fields["title"] == "E2E annotation title"
    assert fields["component"] == "shell"
    assert fields["decision"] == "Real decision from a mixed paste"
    assert fields["status"] == "accepted"
    assert detail["issues"] == []
    anchor = _mapping(detail["anchor"])
    assert anchor["kind"] == "next"
    assert anchor["text"] == "echo anchored-command"
    assert _mapping(cast("list[object]", detail["uses"])[0]) == {"ref": "does-not-exist", "resolved": None}
    assert "FAKE" not in json.dumps(detail)

    listing = _json(runner.invoke("lion", "records", "list", "--json", env=env, expected=0))["recordings"]
    entry = _mapping(cast("list[object]", listing)[0])
    brief = _mapping(entry["adr"])
    assert brief["title"] == "E2E annotation title"
    assert brief["status"] == "accepted"

    status = _json(runner.invoke("lion", "records", "status", "--json", env=env, expected=0))
    assert status["recordings"] == 1
    assert status["open"] == 0
    assert _mapping(status["adrs"])["total"] == 1


def test_records_read_commands_write_nothing(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """status, list and adrs leave the data directory byte-identical."""
    _install(runner, env)
    _ = runner("lion", "watch", "start", env=env)
    with Zsh(env, tmp_path / "zdot") as shell:
        _ = shell.wait_prompt()
        _ = shell.run("setopt interactive_comments")
        _ = shell.run("#-LION: adr.title Read-only proof")
    _stop(runner, env)

    data = Path(env["XDG_DATA_HOME"])
    before = manifest(data)

    _ = runner("lion", "records", "status", env=env)
    _ = runner("lion", "records", "status", "--json", env=env)
    _ = runner("lion", "records", "list", env=env)
    _ = runner("lion", "records", "list", "--json", env=env)
    _ = runner("lion", "records", "adrs", env=env)
    _ = runner("lion", "records", "adrs", "latest", "--json", env=env)

    assert manifest(data) == before


def test_records_edit_roundtrip_in_the_container(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """A scripted $EDITOR stores the overlay; records and doctor accept it."""
    _install(runner, env)
    _ = runner("lion", "watch", "start", env=env)
    with Zsh(env, tmp_path / "zdot") as shell:
        _ = shell.wait_prompt()
        _ = shell.run("setopt interactive_comments")
        _ = shell.run("#-LION: adr.title Overlay source")
        _ = shell.run("#-LION: adr.decision Keep this decision")
    _stop(runner, env)

    editor = Path(env["HOME"]) / "editor.py"
    editor.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "document = (\n"
        "    'schema_version = 1\\n\\n[fields]\\n'\n"
        '    \'title = "Edited in the container"\\ndecision = "Kept decision"\\n\'\n'
        ")\n"
        "pathlib.Path(sys.argv[1]).write_text(document)\n",
        encoding="utf-8",
    )
    editor.chmod(0o755)

    result = runner.invoke("lion", "records", "edit", "latest", env={**env, "EDITOR": str(editor)}, expected=0)
    assert "ADR overlay written" in result.stdout

    detail = _mapping(_json(runner.invoke("lion", "records", "adrs", "latest", "--json", env=env, expected=0))["adr"])
    assert detail["edited"] is True
    assert _mapping(detail["fields"])["title"] == "Edited in the container"

    records_dir = Path(env["XDG_DATA_HOME"]) / "lion" / "recordings"
    assert not list(records_dir.rglob(".adr-edit-*"))
    assert list(records_dir.rglob("adr.toml"))

    doctor = _json(runner.invoke("lion", "doctor", "--json", env=env, expected=0))
    findings = doctor_findings(doctor)
    assert not [name for name in findings if name.startswith("watch.adr")]
