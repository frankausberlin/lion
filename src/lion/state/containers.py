"""Collector for container configuration (Docker or Podman, config facts only).

Every command is fixed code and every record is filtered through whitelisted
``--format`` templates, so secrets in ``Config.Env`` or arbitrary labels never
reach stdout. ``docker inspect`` runs as a second phase over the container names
reported by ``docker ps`` and never dumps the whole object. Container ids, state,
health, IPs, timestamps and live stats are dropped, and every list is sorted for
deterministic output.
"""

import json
import re
import shutil
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import cast

from lion.state.collector import Collector, CollectorResult, CollectorStatus
from lion.state.tools import run_tool

TIMEOUT_SECONDS = 15

#: ASCII unit separator between template fields; ``json`` escapes it inside
#: strings, so it cannot appear in a whitelisted value and every record stays
#: exactly one line per object.
_SEP = "\x1f"

_INFO_TEMPLATE = (
    '{"server_version":{{json .ServerVersion}},"storage_driver":{{json .Driver}},'
    '"security_options":{{json .SecurityOptions}}}'
)
_PODMAN_INFO_TEMPLATE = (
    '{"server_version":{{json .Version.Version}},"storage_driver":{{json .Store.GraphDriverName}},'
    '"rootless":{{json .Host.Security.Rootless}}}'
)

_IMAGES_TEMPLATE = _SEP.join(("{{.Repository}}", "{{.Tag}}", "{{.Digest}}", "{{.Size}}"))
_VOLUMES_TEMPLATE = _SEP.join(("{{.Name}}", "{{.Driver}}"))
_NETWORKS_TEMPLATE = _SEP.join(("{{.Name}}", "{{.Driver}}", "{{.Scope}}"))

#: Whitelisted inspect fields as ``(parser name, template expression)``. The
#: template, its field count and the parser all derive from this single ordered
#: definition, so they cannot drift apart.
_INSPECT_FIELDS: tuple[tuple[str, str], ...] = (
    ("name", "{{json .Name}}"),
    ("image_ref", "{{json .Config.Image}}"),
    ("restart_policy", "{{json .HostConfig.RestartPolicy.Name}}"),
    ("privileged", "{{json .HostConfig.Privileged}}"),
    ("cap_add", "{{json .HostConfig.CapAdd}}"),
    ("cap_drop", "{{json .HostConfig.CapDrop}}"),
    ("readonly_rootfs", "{{json .HostConfig.ReadonlyRootfs}}"),
    ("security_opt", "{{json .HostConfig.SecurityOpt}}"),
    ("exposed_ports", "{{json .Config.ExposedPorts}}"),
    ("port_bindings", "{{json .HostConfig.PortBindings}}"),
    ("networks", "{{json .NetworkSettings.Networks}}"),
    ("mounts", "{{json .Mounts}}"),
    ("compose_project", '{{json (index .Config.Labels "com.docker.compose.project")}}'),
    ("compose_service", '{{json (index .Config.Labels "com.docker.compose.service")}}'),
)

_INSPECT_TEMPLATE = _SEP.join(expression for _, expression in _INSPECT_FIELDS)

_IMAGE_FIELDS = 4
_VOLUME_FIELDS = 2
_NETWORK_FIELDS = 3

_SIZE_PATTERN = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([kKmMgGtT]?)[bB]?\s*$")
_SIZE_UNITS = {"": 1, "k": 1000, "m": 1000**2, "g": 1000**3, "t": 1000**4}


@dataclass(frozen=True)
class PortState:
    """One exposed or published container port."""

    container_port: int
    protocol: str
    host_port: str


@dataclass(frozen=True)
class MountState:
    """One configured container mount."""

    type: str
    name: str
    source: str
    destination: str
    read_only: bool


@dataclass(frozen=True)
class ContainerState:
    """Configuration facts of one container (no runtime state)."""

    name: str
    image_ref: str
    restart_policy: str
    privileged: bool
    cap_add: list[str]
    cap_drop: list[str]
    readonly_rootfs: bool
    security_opt: list[str]
    ports: list[PortState]
    networks: list[str]
    mounts: list[MountState]
    compose_project: str
    compose_service: str


@dataclass(frozen=True)
class ImageState:
    """One local container image reference."""

    repository: str
    tag: str
    digest: str
    size_bytes: int


@dataclass(frozen=True)
class VolumeState:
    """One container volume."""

    name: str
    driver: str


@dataclass(frozen=True)
class ContainerNetworkState:
    """One container network."""

    name: str
    driver: str
    scope: str


@dataclass(frozen=True)
class ContainersState:
    """Selected container engine plus its configured images and objects."""

    runtime: str
    server_version: str
    storage_driver: str
    rootless: bool
    images: list[ImageState]
    volumes: list[VolumeState]
    networks: list[ContainerNetworkState]
    containers: list[ContainerState]


def _decode(raw: str) -> object:
    """Decode one ``json`` template field, or ``None`` when it is not JSON."""
    text = raw.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _load_object(raw: str) -> dict[str, object]:
    """Decode one command's single-line JSON object, or return ``{}``."""
    payload = _decode(raw.splitlines()[0] if raw.strip() else "")
    return cast("dict[str, object]", payload) if isinstance(payload, dict) else {}


def _str(value: object) -> str:
    """Return a string field, or ``""`` for a missing/non-string value."""
    return value if isinstance(value, str) else ""


def _str_list(value: object) -> list[str]:
    """Return a list of strings for a template field, or ``[]``."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in cast("list[object]", value)]


def _bool(value: object, *, default: bool = False) -> bool:
    """Return a boolean field, or ``default`` for a missing/non-boolean value."""
    return value if isinstance(value, bool) else default


def _parse_size(raw: str) -> int:
    """Parse a decimal byte size such as ``187MB`` into bytes, or ``0``."""
    match = _SIZE_PATTERN.match(raw)
    if match is None:
        return 0
    return round(float(match.group(1)) * _SIZE_UNITS[match.group(2).lower()])


def _parse_ports(exposed: object, bindings: object) -> list[PortState]:
    """Build the configured port list from ``ExposedPorts`` and ``PortBindings``.

    Configuration, not runtime: a port is listed when it is exposed or
    published, and ``host_port`` is empty for an exposed-only port. Host IPs are
    dropped.
    """
    exposed_map: Mapping[str, object] = cast("Mapping[str, object]", exposed) if isinstance(exposed, dict) else {}
    binding_map: Mapping[str, object] = cast("Mapping[str, object]", bindings) if isinstance(bindings, dict) else {}
    keys = {str(key) for key in exposed_map} | {str(key) for key in binding_map}
    ports: list[PortState] = []
    for key in sorted(keys):
        port_text, _, protocol = key.partition("/")
        container_port = int(port_text) if port_text.isdigit() else 0
        raw_bindings = binding_map.get(key)
        host_ports: list[str] = []
        if isinstance(raw_bindings, list):
            for binding in cast("list[object]", raw_bindings):
                if not isinstance(binding, dict):
                    continue
                host_port = _str(cast("Mapping[str, object]", binding).get("HostPort"))
                host_ports.append(host_port)
        for host_port in host_ports or [""]:
            ports.append(PortState(container_port=container_port, protocol=protocol, host_port=host_port))
    return sorted(ports, key=lambda port: (port.container_port, port.protocol, port.host_port))


def _parse_mounts(value: object) -> list[MountState]:
    """Build the sorted mount list; ``read_only`` is the inverse of ``RW``."""
    if not isinstance(value, list):
        return []
    mounts: list[MountState] = []
    for item in cast("list[object]", value):
        if not isinstance(item, dict):
            continue
        entry = cast("Mapping[str, object]", item)
        mounts.append(
            MountState(
                type=_str(entry.get("Type")),
                name=_str(entry.get("Name")),
                source=_str(entry.get("Source")),
                destination=_str(entry.get("Destination")),
                read_only=not _bool(entry.get("RW"), default=True),
            )
        )
    return sorted(mounts, key=lambda mount: (mount.destination, mount.source))


def _parse_networks(value: object) -> list[str]:
    """Return the sorted network names a container is attached to."""
    if not isinstance(value, dict):
        return []
    return sorted(str(name) for name in cast("Mapping[str, object]", value))


def _parse_inspect(output: str) -> list[ContainerState]:
    """Parse whitelisted ``inspect`` records into sorted containers."""
    containers: list[ContainerState] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split(_SEP)
        if len(parts) != len(_INSPECT_FIELDS):
            continue
        fields = {name: _decode(part) for (name, _), part in zip(_INSPECT_FIELDS, parts, strict=True)}
        raw_name = _str(fields["name"])
        if not raw_name:
            continue
        containers.append(
            ContainerState(
                name=raw_name.removeprefix("/"),
                image_ref=_str(fields["image_ref"]),
                restart_policy=_str(fields["restart_policy"]),
                privileged=_bool(fields["privileged"]),
                cap_add=_str_list(fields["cap_add"]),
                cap_drop=_str_list(fields["cap_drop"]),
                readonly_rootfs=_bool(fields["readonly_rootfs"]),
                security_opt=_str_list(fields["security_opt"]),
                ports=_parse_ports(fields["exposed_ports"], fields["port_bindings"]),
                networks=_parse_networks(fields["networks"]),
                mounts=_parse_mounts(fields["mounts"]),
                compose_project=_str(fields["compose_project"]),
                compose_service=_str(fields["compose_service"]),
            )
        )
    return sorted(containers, key=lambda container: container.name)


def _select_engine(failures: list[str]) -> tuple[str, dict[str, object]] | None:
    """Choose the first usable engine once, or ``None`` when none is usable.

    A binary that is not on ``PATH`` is skipped without a failure, so "no engine
    installed" stays ``ok``. A present binary whose ``info`` fails is retried as
    the next engine; its failure is only reported when no engine works, so a
    stale docker never masks a working podman.
    """
    attempts: list[str] = []
    engines = (
        ("docker", _INFO_TEMPLATE),
        ("podman", _PODMAN_INFO_TEMPLATE),
    )
    for runtime, template in engines:
        if shutil.which(runtime) is None:
            continue
        output = run_tool([runtime, "info", "--format", template], timeout=TIMEOUT_SECONDS, failures=attempts)
        if output is None:
            continue
        payload = _load_object(output)
        if runtime == "docker":
            options = _str_list(payload.get("security_options"))
            rootless = any("rootless" in option.lower() for option in options)
        else:
            rootless = _bool(payload.get("rootless"))
        return (
            runtime,
            {
                "server_version": _str(payload.get("server_version")),
                "storage_driver": _str(payload.get("storage_driver")),
                "rootless": rootless,
            },
        )
    failures.extend(attempts)
    return None


def _collect_images(runtime: str, failures: list[str]) -> list[ImageState]:
    output = run_tool(
        [runtime, "images", "--digests", "--no-trunc", "--format", _IMAGES_TEMPLATE],
        timeout=TIMEOUT_SECONDS,
        failures=failures,
    )
    if output is None:
        return []
    images: list[ImageState] = []
    for line in output.splitlines():
        parts = line.split(_SEP)
        if len(parts) != _IMAGE_FIELDS:
            continue
        repository, tag, digest, size = (part.strip() for part in parts)
        images.append(ImageState(repository=repository, tag=tag, digest=digest, size_bytes=_parse_size(size)))
    return sorted(images, key=lambda image: (image.repository, image.tag, image.digest))


def _collect_volumes(runtime: str, failures: list[str]) -> list[VolumeState]:
    output = run_tool(
        [runtime, "volume", "ls", "--format", _VOLUMES_TEMPLATE],
        timeout=TIMEOUT_SECONDS,
        failures=failures,
    )
    if output is None:
        return []
    volumes: list[VolumeState] = []
    for line in output.splitlines():
        parts = line.split(_SEP)
        if len(parts) != _VOLUME_FIELDS:
            continue
        name, driver = (part.strip() for part in parts)
        if name:
            volumes.append(VolumeState(name=name, driver=driver))
    return sorted(volumes, key=lambda volume: volume.name)


def _collect_networks(runtime: str, failures: list[str]) -> list[ContainerNetworkState]:
    output = run_tool(
        [runtime, "network", "ls", "--format", _NETWORKS_TEMPLATE],
        timeout=TIMEOUT_SECONDS,
        failures=failures,
    )
    if output is None:
        return []
    networks: list[ContainerNetworkState] = []
    for line in output.splitlines():
        parts = line.split(_SEP)
        if len(parts) != _NETWORK_FIELDS:
            continue
        name, driver, scope = (part.strip() for part in parts)
        if name:
            networks.append(ContainerNetworkState(name=name, driver=driver, scope=scope))
    return sorted(networks, key=lambda network: network.name)


def _collect_containers(runtime: str, failures: list[str]) -> list[ContainerState]:
    """Two-phase inspect: list names, then inspect those names as argv."""
    output = run_tool([runtime, "ps", "-a", "--format", "{{.Names}}"], timeout=TIMEOUT_SECONDS, failures=failures)
    if output is None:
        return []
    names = [line.strip() for line in output.splitlines() if line.strip()]
    if not names:
        return []
    inspect = run_tool(
        [runtime, "inspect", "--format", _INSPECT_TEMPLATE, "--", *names],
        timeout=TIMEOUT_SECONDS,
        failures=failures,
    )
    if inspect is None:
        return []
    return _parse_inspect(inspect)


def _collect() -> CollectorResult:
    """Collect container configuration without aborting the capture."""
    failures: list[str] = []
    engine = _select_engine(failures)
    if engine is not None:
        runtime, info = engine
        images = _collect_images(runtime, failures)
        volumes = _collect_volumes(runtime, failures)
        networks = _collect_networks(runtime, failures)
        containers = _collect_containers(runtime, failures)
    else:
        runtime = "none"
        info = {"server_version": "", "storage_driver": "", "rootless": False}
        images, volumes, networks, containers = [], [], [], []
    state = ContainersState(
        runtime=runtime,
        server_version=_str(info.get("server_version")),
        storage_driver=_str(info.get("storage_driver")),
        rootless=_bool(info.get("rootless")),
        images=images,
        volumes=volumes,
        networks=networks,
        containers=containers,
    )
    return CollectorResult(
        status=CollectorStatus.UNAVAILABLE if failures else CollectorStatus.OK,
        data=asdict(state),
        error="; ".join(dict.fromkeys(failures)),
    )


COLLECTOR = Collector(name="containers", collect=_collect)
