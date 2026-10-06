"""Exercise the installed CLI against real, disposable Debian package state."""

import json
import os
import subprocess
import tomllib
from pathlib import Path
from typing import cast

import pytest

pytestmark = pytest.mark.e2e
PACKAGE = "lion-e2e-probe"
IDENTITY = f"{PACKAGE}:all"
VERSION = "1.0.0"


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict), value
    return cast("dict[str, object]", value)


def _run(args: list[str], env: dict[str, str], log: Path) -> str:
    with log.open("a", encoding="utf-8") as stream:
        stream.write(f"$ {args!r}\n")
        stream.flush()
        result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=60)
        stream.write(f"exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}\n")
    assert result.returncode == 0, f"{args!r}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return result.stdout


def _history(data: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted((data / "lion" / "history").glob("*.toml"))}


def test_package_lifecycle(tmp_path: Path) -> None:
    """Detect installation and removal while preserving every distinct state."""
    if os.environ.get("LION_E2E_CONTAINER") != "1" or not Path("/.dockerenv").exists() or os.geteuid() != 0:
        pytest.skip("Requires the disposable root container started by just test-e2e")

    data = tmp_path / "data"
    env = {**os.environ, "XDG_DATA_HOME": str(data), "LC_ALL": "C"}
    log = tmp_path / "commands.log"

    def lion(command: str) -> dict[str, object]:
        before = _history(data)
        output = _mapping(json.loads(_run(["lion", command, "--json"], env, log)))
        if command == "status":
            assert _history(data) == before, "status changed the history"
        return output

    def scan(event: str, count: int) -> dict[str, object]:
        before = _history(data)
        output = lion("scan")
        assert output["ereignis"] == event
        after = _history(data)
        assert len(after) == count
        if event == "appended":
            assert all(after[name] == content for name, content in before.items())
        path = Path(str(output["pfad"]))
        assert path.parent == data / "lion" / "history"
        snapshot = _mapping(output["zustand"])
        assert tomllib.loads(path.read_text(encoding="utf-8")) == snapshot
        return output

    def status(expected: dict[str, object]) -> None:
        output = lion("status")
        assert output["geaendert"] is bool(expected)
        assert output["unterschiede"] == expected

    # Build a package locally: no repository downloads, dependencies or maintainer scripts.
    root = tmp_path / "package"
    (root / "DEBIAN").mkdir(parents=True)
    (root / "DEBIAN" / "control").write_text(
        f"Package: {PACKAGE}\nVersion: {VERSION}\nArchitecture: all\n"
        "Maintainer: LION Tests <tests@example.invalid>\n"
        "Description: LION end-to-end fixture\n",
        encoding="utf-8",
    )
    payload = root / "usr" / "share" / PACKAGE
    payload.mkdir(parents=True)
    (payload / "probe.txt").write_text("LION E2E fixture\n", encoding="utf-8")
    deb = tmp_path / f"{PACKAGE}.deb"
    _run(["dpkg-deb", "--build", "--root-owner-group", str(root), str(deb)], env, log)

    initial = scan("created", 1)
    initial_state = _mapping(initial["zustand"])
    initial_collectors = _mapping(initial_state["collectors"])
    packages = _mapping(initial_collectors["packages"])
    assert packages["status"] == "ok"
    assert packages["error"] == ""
    assert IDENTITY not in _mapping(packages["installed"])
    manual = cast("list[str]", packages["manual"])
    assert PACKAGE not in manual
    installed_manual = sorted([*manual, PACKAGE])
    status({})

    confirmed = scan("confirmed", 1)
    confirmed_state = _mapping(confirmed["zustand"])
    assert confirmed["pfad"] == initial["pfad"]
    assert confirmed_state["erstscan"] == initial_state["erstscan"]
    assert str(confirmed_state["zuletzt_bestaetigt"]) > str(initial_state["zuletzt_bestaetigt"])
    assert confirmed_state["collectors"] == initial_collectors

    _run(["dpkg", "--install", str(deb)], env, log)
    status(
        {
            "packages": {
                "added": {f"installed.{IDENTITY}": VERSION},
                "changed": {"manual": {"old": manual, "new": installed_manual}},
            }
        }
    )
    installed = scan("appended", 2)
    installed_collectors = _mapping(_mapping(installed["zustand"])["collectors"])
    assert installed_collectors == {
        **initial_collectors,
        "packages": {
            **packages,
            "installed": {**_mapping(packages["installed"]), IDENTITY: VERSION},
            "manual": installed_manual,
        },
    }
    status({})

    _run(["dpkg", "--purge", PACKAGE], env, log)
    status(
        {
            "packages": {
                "removed": {f"installed.{IDENTITY}": VERSION},
                "changed": {"manual": {"old": installed_manual, "new": manual}},
            }
        }
    )
    removed = scan("appended", 3)
    assert removed["pfad"] not in (initial["pfad"], installed["pfad"])
    assert _mapping(removed["zustand"])["collectors"] == initial_collectors
    status({})
