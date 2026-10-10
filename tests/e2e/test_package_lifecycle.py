"""Exercise the installed CLI against real, disposable Debian package state."""

import json
import os
import tomllib
from collections.abc import Callable
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


def _history(data: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted((data / "lion" / "history").glob("*.toml"))}


def test_package_lifecycle(tmp_path: Path, runner: Callable[..., str], env: dict[str, str]) -> None:
    """Detect installation and removal while preserving every distinct state."""
    if os.geteuid() != 0:
        pytest.fail("Package lifecycle requires root inside the disposable container")

    data = Path(env["XDG_DATA_HOME"])

    def run(args: list[str]) -> str:
        return runner(*args, env=env)

    def lion(command: str) -> dict[str, object]:
        before = _history(data)
        output = _mapping(json.loads(run(["lion", command, "--json"])))
        if command == "status":
            assert _history(data) == before, "status changed the history"
        return output

    def scan(event: str, count: int) -> dict[str, object]:
        before = _history(data)
        output = lion("scan")
        assert output["event"] == event
        after = _history(data)
        assert len(after) == count
        if event == "appended":
            assert all(after[name] == content for name, content in before.items())
        path = Path(str(output["path"]))
        assert path.parent == data / "lion" / "history"
        snapshot = _mapping(output["state"])
        assert tomllib.loads(path.read_text(encoding="utf-8")) == snapshot
        return output

    def status(expected: dict[str, object], structural: bool = False) -> None:
        output = lion("status")
        assert output["changed"] is bool(expected)
        assert output["differences"] == expected
        assert output["structure_changed"] is structural

    # Build a package locally: no repository downloads, dependencies or maintainer scripts.
    root = tmp_path / "package"
    (root / "DEBIAN").mkdir(parents=True)
    payload = root / "usr" / "share" / PACKAGE
    payload.mkdir(parents=True)
    (payload / "probe.txt").write_text("LION E2E fixture\n", encoding="utf-8")

    def build(version: str) -> Path:
        (root / "DEBIAN" / "control").write_text(
            f"Package: {PACKAGE}\nVersion: {version}\nArchitecture: all\n"
            "Maintainer: LION Tests <tests@example.invalid>\n"
            "Description: LION end-to-end fixture\n",
            encoding="utf-8",
        )
        deb = tmp_path / f"{PACKAGE}-{version}.deb"
        run(["dpkg-deb", "--build", "--root-owner-group", str(root), str(deb)])
        return deb

    deb = build(VERSION)
    upgrade = build("2.0.0")

    initial = scan("created", 1)
    initial_state = _mapping(initial["state"])
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
    confirmed_state = _mapping(confirmed["state"])
    assert confirmed["path"] == initial["path"]
    assert confirmed_state["created_at"] == initial_state["created_at"]
    # Timestamps are fixed-width UTC ISO-8601, so string order is chronological order.
    assert str(confirmed_state["confirmed_at"]) > str(initial_state["confirmed_at"])
    assert confirmed_state["collectors"] == initial_collectors

    run(["dpkg", "--install", str(deb)])
    status(
        {
            "packages": {
                "added": {
                    f"installed.{IDENTITY}": VERSION,
                    f"manual[{PACKAGE}]": PACKAGE,
                },
            }
        },
        structural=True,
    )
    installed = scan("appended", 2)
    installed_collectors = _mapping(_mapping(installed["state"])["collectors"])
    assert installed_collectors == {
        **initial_collectors,
        "packages": {
            **packages,
            "installed": {**_mapping(packages["installed"]), IDENTITY: VERSION},
            "manual": installed_manual,
        },
    }
    status({})

    run(["dpkg", "--install", str(upgrade)])
    upgrade_changes: dict[str, object] = {
        "packages": {"changed": {f"installed.{IDENTITY}": {"old": VERSION, "new": "2.0.0"}}}
    }
    status(upgrade_changes)
    upgraded = scan("appended", 3)
    assert _mapping(_mapping(upgraded["state"])["collectors"]) == {
        **installed_collectors,
        "packages": {
            **_mapping(installed_collectors["packages"]),
            "installed": {**_mapping(packages["installed"]), IDENTITY: "2.0.0"},
        },
    }
    status({})

    run(["dpkg", "--purge", PACKAGE])
    status(
        {
            "packages": {
                "removed": {
                    f"installed.{IDENTITY}": "2.0.0",
                    f"manual[{PACKAGE}]": PACKAGE,
                },
            }
        },
        structural=True,
    )
    removed = scan("appended", 4)
    assert removed["path"] not in (initial["path"], installed["path"], upgraded["path"])
    assert _mapping(removed["state"])["collectors"] == initial_collectors
    status({})

    # --- lion history and lion diff over the four real persisted states ---

    def diff(*references: str) -> dict[str, object]:
        return _mapping(json.loads(run(["lion", "diff", *references, "--json"])))

    refs = [Path(str(state["path"])).stem for state in (initial, installed, upgraded, removed)]
    before_read = _history(data)

    listing = _mapping(json.loads(run(["lion", "history", "--json"])))
    entries = cast("list[dict[str, object]]", listing["entries"])
    assert [entry["index"] for entry in entries] == [1, 2, 3, 4]
    assert [entry["ref"] for entry in entries] == refs
    assert entries[-1]["latest"] is True
    human = run(["lion", "history"])
    assert all(ref in human for ref in refs)

    install_delta = diff("1", "2")
    assert install_delta["changed"] is True
    assert install_delta["structure_changed"] is True
    assert _mapping(install_delta["from"])["ref"] == refs[0]
    assert _mapping(install_delta["to"])["ref"] == refs[1]
    assert _mapping(_mapping(install_delta["differences"])["packages"])["added"] == {
        f"installed.{IDENTITY}": VERSION,
        f"manual[{PACKAGE}]": PACKAGE,
    }

    upgrade_delta = diff("2", "3")
    assert upgrade_delta["differences"] == upgrade_changes
    assert upgrade_delta["structure_changed"] is False

    purge_delta = diff("previous", "latest")
    assert purge_delta["structure_changed"] is True
    assert _mapping(_mapping(purge_delta["differences"])["packages"])["removed"] == {
        f"installed.{IDENTITY}": "2.0.0",
        f"manual[{PACKAGE}]": PACKAGE,
    }

    # The purge restored the initial collectors: the first and last states are equal,
    # whether referenced by index or by their compact reference.
    assert diff("1", "4")["changed"] is False
    assert diff(refs[0], refs[3])["changed"] is False
    assert _history(data) == before_read, "history or diff changed the history"
