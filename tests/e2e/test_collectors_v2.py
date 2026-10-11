"""End-to-end scenarios for the collector v2 set (network, services, containers).

These tests run the installed ``lion`` executable inside the disposable
container against real files and a real history. The bare image has no physical
network interface (``network.interfaces == []``) and no container engine
(``runtime == "none"``), so both collectors are deterministic; ``services``
mirrors whatever ``systemctl`` this base image provides. The scenarios also pin
the additive schema-2 contract: a snapshot without the v2 sections stays a
valid old snapshot and a rescan appends instead of silently adopting it.
"""

import json
import shutil
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
import tomli_w

pytestmark = pytest.mark.e2e

_V2_SECTIONS = ("network", "services", "containers")


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict), value
    return cast("dict[str, object]", value)


def _history(data: Path) -> dict[str, bytes]:
    history = data / "lion" / "history"
    return {path.name: path.read_bytes() for path in sorted(history.glob("*.toml"))}


def _scan(runner: Callable[..., str], env: dict[str, str]) -> dict[str, object]:
    return _mapping(json.loads(runner("lion", "scan", "--json", env=env)))


def _findings(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    raw = payload["findings"]
    assert isinstance(raw, list), raw
    result: dict[str, dict[str, object]] = {}
    for item in cast("list[object]", raw):
        finding = _mapping(item)
        result[str(finding["name"])] = finding
    return result


def _strip_v2_sections(path: Path) -> None:
    """Rewrite one stored entry into a valid old schema-2 snapshot."""
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    collectors = _mapping(document["collectors"])
    for name in _V2_SECTIONS:
        del collectors[name]
    path.write_text(tomli_w.dumps(document), encoding="utf-8")


def test_scan_persists_v2_sections(runner: Callable[..., str], env: dict[str, str]) -> None:
    """A real scan persists all eight sections with deterministic bare-profile data."""
    payload = _scan(runner, env)
    assert payload["event"] == "created"
    state = _mapping(payload["state"])
    assert state["schema_version"] == 2

    collectors = _mapping(state["collectors"])
    assert set(collectors) == {"host", "hardware", "network", "packages", "services", "containers", "tools", "dash"}

    network = _mapping(collectors["network"])
    assert network["status"] == "ok"
    assert network["interfaces"] == []

    containers = _mapping(collectors["containers"])
    assert containers["status"] == "ok"
    assert containers["runtime"] == "none"
    for key in ("images", "volumes", "networks", "containers"):
        assert containers[key] == [], key

    services = _mapping(collectors["services"])
    assert services["status"] in {"ok", "unavailable"}
    if services["status"] == "unavailable":
        assert services["error"] != ""

    path = Path(str(payload["path"]))
    assert path.parent == Path(env["XDG_DATA_HOME"]) / "lion" / "history"
    assert tomllib.loads(path.read_text(encoding="utf-8")) == state


def test_repeat_scan_confirms_without_churn(runner: Callable[..., str], env: dict[str, str]) -> None:
    """Two scans in a row confirm the same entry: no v2 collector has volatile fields."""
    first = _scan(runner, env)
    second = _scan(runner, env)
    assert second["event"] == "confirmed"
    assert second["path"] == first["path"]

    first_state = _mapping(first["state"])
    second_state = _mapping(second["state"])
    assert second_state["collectors"] == first_state["collectors"]
    assert second_state["created_at"] == first_state["created_at"]
    assert str(second_state["confirmed_at"]) > str(first_state["confirmed_at"])
    assert len(_history(Path(env["XDG_DATA_HOME"]))) == 1


def test_status_is_clean_for_v2_collectors(runner: Callable[..., str], env: dict[str, str]) -> None:
    """After a scan the v2 collectors compare identical and status stays read-only."""
    _scan(runner, env)
    data = Path(env["XDG_DATA_HOME"])
    before = _history(data)

    payload = _mapping(json.loads(runner("lion", "status", "--json", env=env)))
    assert payload["changed"] is False
    assert payload["differences"] == {}
    assert payload["structure_changed"] is False
    assert _history(data) == before


def test_old_snapshot_without_v2_sections_is_additive(runner: Callable[..., str], env: dict[str, str]) -> None:
    """A stored snapshot without the v2 sections stays readable and is appended on rescan."""
    data = Path(env["XDG_DATA_HOME"])
    created = _scan(runner, env)
    _strip_v2_sections(Path(str(created["path"])))

    status = _mapping(json.loads(runner("lion", "status", "--json", env=env)))
    assert status["changed"] is True
    assert status["structure_changed"] is True
    differences = _mapping(status["differences"])
    assert set(differences) == set(_V2_SECTIONS)
    for name in _V2_SECTIONS:
        added = _mapping(_mapping(differences[name])["added"])
        assert set(added) == {"(collector)"}, name
        section = _mapping(added["(collector)"])
        assert {"status", "error"} <= set(section), name

    appended = _scan(runner, env)
    assert appended["event"] == "appended"
    assert appended["path"] != created["path"]
    assert len(_history(data)) == 2

    confirmed = _scan(runner, env)
    assert confirmed["event"] == "confirmed"
    assert confirmed["path"] == appended["path"]
    assert len(_history(data)) == 2


def test_doctor_reports_v2_collectors_and_tools(runner: Callable[..., str], env: dict[str, str]) -> None:
    """Doctor findings for the v2 collectors and tools mirror the bare-profile reality."""
    payload = _mapping(json.loads(runner("lion", "doctor", "--json", env=env)))
    findings = _findings(payload)
    for name in ("collectors.network", "collectors.services", "collectors.containers"):
        assert name in findings, name
    assert findings["collectors.network"]["status"] == "ok"
    assert findings["collectors.containers"]["status"] == "ok"
    assert findings["collectors.containers"]["message"] == "Collector 'containers': ok."

    # Whether systemctl exists decides the services finding, so assert reality.
    if shutil.which("systemctl") is None:
        assert findings["collectors.services"]["status"] == "warn"
        assert findings["tools.systemctl"]["status"] == "warn"
    else:
        assert findings["tools.systemctl"]["status"] == "ok"
        assert findings["collectors.services"]["status"] in {"ok", "warn"}

    assert findings["tools.docker"]["status"] == "skip"
    assert findings["tools.podman"]["status"] == "skip"


def test_read_only_commands_leave_v2_history_untouched(runner: Callable[..., str], env: dict[str, str]) -> None:
    """status, history, diff and doctor never change the v2 history bytes."""
    data = Path(env["XDG_DATA_HOME"])
    created = _scan(runner, env)
    _strip_v2_sections(Path(str(created["path"])))
    appended = _scan(runner, env)
    assert appended["event"] == "appended"
    before = _history(data)

    runner("lion", "status", "--json", env=env)
    runner("lion", "history", "--json", env=env)
    runner("lion", "diff", "previous", "latest", "--json", env=env)
    runner("lion", "doctor", "--json", env=env)

    assert _history(data) == before
    assert len(before) == 2


def test_legacy_german_snapshot_fails_loud(runner: Callable[..., str], env: dict[str, str], tmp_path: Path) -> None:
    """A schema-1 entry in the history fails status loudly instead of being skipped."""
    history = Path(env["XDG_DATA_HOME"]) / "lion" / "history"
    history.mkdir(parents=True)
    (history / "legacy.toml").write_text(
        'schema_version = 1\n"erstscan" = "2026-10-05T20:00:00+00:00"\n'
        '"zuletzt_bestaetigt" = "2026-10-05T20:00:00+00:00"\n[collectors]\n',
        encoding="utf-8",
    )

    runner("lion", "status", "--json", env=env, expected=1)
    log = (tmp_path / "commands.log").read_text(encoding="utf-8")
    assert "legacy German snapshot keys" in log
    assert "erstscan" in log
    assert "legacy.toml" in log
