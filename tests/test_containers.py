"""Deterministic tests for the containers collector with canned CLI output."""

import json
from collections.abc import Mapping
from typing import cast

import pytest

from lion.state import containers
from lion.state.collector import CollectorStatus

SEP = "\x1f"


def _inspect_line(
    name: str,
    image: str,
    *,
    restart: str | None = None,
    privileged: bool = False,
    cap_add: list[str] | None = None,
    cap_drop: list[str] | None = None,
    readonly: bool = False,
    security_opt: list[str] | None = None,
    exposed: dict[str, object] | None = None,
    bindings: object = None,
    networks: dict[str, object] | None = None,
    mounts: list[object] | None = None,
    compose_project: str | None = None,
    compose_service: str | None = None,
) -> str:
    """Render one whitelisted ``--format`` inspect record, like the CLI does."""
    fields: list[object] = [
        name,
        image,
        restart,
        privileged,
        cap_add or [],
        cap_drop or [],
        readonly,
        security_opt or [],
        exposed or {},
        bindings,
        networks or {},
        mounts or [],
        compose_project,
        compose_service,
    ]
    return SEP.join(json.dumps(field) for field in fields)


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    responses: Mapping[tuple[str, str], str | None],
    present: set[str],
) -> list[list[str]]:
    """Patch ``shutil.which`` and ``run_tool``; return the recorded commands."""
    calls: list[list[str]] = []

    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in present else None

    def run_tool(command: list[str], *, timeout: int, failures: list[str] | None = None) -> str | None:
        calls.append(list(command))
        key = (command[0], command[1] if len(command) > 1 else "")
        output = responses.get(key)
        if output is None and failures is not None:
            failures.append(f"{command[0]}: exit code 1")
        return output

    monkeypatch.setattr(containers.shutil, "which", which)
    monkeypatch.setattr(containers, "run_tool", run_tool)
    return calls


def _docker_responses() -> dict[tuple[str, str], str]:
    info = json.dumps(
        {
            "server_version": "24.0.7",
            "storage_driver": "overlay2",
            "security_options": ["name=seccomp", "name=rootless"],
        }
    )
    images = (
        f"nginx{SEP}1.25{SEP}sha256:aaa{SEP}187MB\n"
        f"nginx{SEP}latest{SEP}sha256:bbb{SEP}200MB\n"
        f"dangling{SEP}<none>{SEP}{SEP}0B\n"
    )
    volumes = f"data{SEP}local\n"
    networks = f"host{SEP}host{SEP}local\nbridge{SEP}bridge{SEP}local\n"
    ps = "worker\nweb\n"
    inspect = (
        _inspect_line(
            "/web",
            "nginx:latest",
            restart="unless-stopped",
            privileged=True,
            cap_add=["NET_ADMIN"],
            cap_drop=["MKNOD"],
            readonly=True,
            security_opt=["no-new-privileges"],
            exposed={"80/tcp": {}, "443/tcp": {}},
            bindings={"443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8443"}]},
            networks={"custom": {}, "bridge": {}},
            mounts=[{"Type": "bind", "Source": "/srv", "Destination": "/data", "RW": False, "Name": ""}],
            compose_project="proj",
            compose_service="web",
        )
        + "\n"
        + _inspect_line("/worker", "busybox")
        + "\n"
    )
    return {
        ("docker", "info"): info,
        ("docker", "images"): images,
        ("docker", "volume"): volumes,
        ("docker", "network"): networks,
        ("docker", "ps"): ps,
        ("docker", "inspect"): inspect,
    }


def test_full_docker_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config facts are collected, sorted and parsed from whitelisted fields."""
    calls = _patch(monkeypatch, _docker_responses(), {"docker"})

    result = containers.COLLECTOR.collect()

    assert result.status == CollectorStatus.OK
    assert result.error == ""
    data = result.data
    assert data["runtime"] == "docker"
    assert data["server_version"] == "24.0.7"
    assert data["storage_driver"] == "overlay2"
    assert data["rootless"] is True
    assert data["images"] == [
        {"repository": "dangling", "tag": "<none>", "digest": "", "size_bytes": 0},
        {"repository": "nginx", "tag": "1.25", "digest": "sha256:aaa", "size_bytes": 187_000_000},
        {"repository": "nginx", "tag": "latest", "digest": "sha256:bbb", "size_bytes": 200_000_000},
    ]
    assert data["volumes"] == [{"name": "data", "driver": "local"}]
    assert data["networks"] == [
        {"name": "bridge", "driver": "bridge", "scope": "local"},
        {"name": "host", "driver": "host", "scope": "local"},
    ]
    assert data["containers"] == [
        {
            "name": "web",
            "image_ref": "nginx:latest",
            "restart_policy": "unless-stopped",
            "privileged": True,
            "cap_add": ["NET_ADMIN"],
            "cap_drop": ["MKNOD"],
            "readonly_rootfs": True,
            "security_opt": ["no-new-privileges"],
            "ports": [
                {"container_port": 80, "protocol": "tcp", "host_port": ""},
                {"container_port": 443, "protocol": "tcp", "host_port": "8443"},
            ],
            "networks": ["bridge", "custom"],
            "mounts": [
                {
                    "type": "bind",
                    "name": "",
                    "source": "/srv",
                    "destination": "/data",
                    "read_only": True,
                }
            ],
            "compose_project": "proj",
            "compose_service": "web",
        },
        {
            "name": "worker",
            "image_ref": "busybox",
            "restart_policy": "",
            "privileged": False,
            "cap_add": [],
            "cap_drop": [],
            "readonly_rootfs": False,
            "security_opt": [],
            "ports": [],
            "networks": [],
            "mounts": [],
            "compose_project": "",
            "compose_service": "",
        },
    ]
    inspect_calls = [command for command in calls if command[1] == "inspect"]
    assert len(inspect_calls) == 1
    assert inspect_calls[0][-2:] == ["worker", "web"]


def test_inspect_never_dumps_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every command uses ``--format`` and no template exposes environment or ids."""
    calls = _patch(monkeypatch, _docker_responses(), {"docker"})

    containers.COLLECTOR.collect()

    assert calls
    for command in calls:
        assert "--format" in command
        assert all("Env" not in part for part in command)


def test_podman_fallback_after_broken_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """A present but unusable docker falls back to podman without a failure."""
    info = json.dumps({"server_version": "4.9", "storage_driver": "overlay", "rootless": True})
    responses = {
        ("podman", "info"): info,
        ("podman", "images"): "",
        ("podman", "volume"): "",
        ("podman", "network"): "",
        ("podman", "ps"): "",
    }
    _patch(monkeypatch, responses, {"docker", "podman"})

    result = containers.COLLECTOR.collect()

    assert result.status == CollectorStatus.OK
    assert result.data["runtime"] == "podman"
    assert result.data["server_version"] == "4.9"
    assert result.data["rootless"] is True


def test_no_engine_is_data_not_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """No container binary yields ``ok`` with ``runtime=none`` and empty lists."""
    _patch(monkeypatch, {}, set())

    result = containers.COLLECTOR.collect()

    assert result.status == CollectorStatus.OK
    assert result.error == ""
    assert result.data == {
        "runtime": "none",
        "server_version": "",
        "storage_driver": "",
        "rootless": False,
        "images": [],
        "volumes": [],
        "networks": [],
        "containers": [],
    }


def test_broken_engine_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A present engine whose ``info`` fails marks the collector unavailable."""
    _patch(monkeypatch, {}, {"docker"})

    result = containers.COLLECTOR.collect()

    assert result.status == CollectorStatus.UNAVAILABLE
    assert result.error == "docker: exit code 1"
    assert result.data["runtime"] == "none"


def test_partial_failure_keeps_other_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing sub-command marks the run unavailable but keeps the rest."""
    responses = _docker_responses()
    del responses[("docker", "images")]
    _patch(monkeypatch, responses, {"docker"})

    result = containers.COLLECTOR.collect()

    assert result.status == CollectorStatus.UNAVAILABLE
    assert result.data["images"] == []
    assert result.data["volumes"] == [{"name": "data", "driver": "local"}]
    containers_data = cast("list[object]", result.data["containers"])
    assert len(containers_data) == 2
