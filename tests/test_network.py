"""Deterministic tests for the network collector using a fake sysfs tree."""

from pathlib import Path

import pytest

from lion.state import network
from lion.state.collector import CollectorStatus


def _make_interface(
    root: Path,
    name: str,
    *,
    physical: bool,
    mac: str | None = None,
    type_id: str | None = None,
    driver: str | None = None,
    assign_type: str | None = "0",
) -> None:
    """Create one interface directory, optionally bound to a driver."""
    entry = root / name
    entry.mkdir()
    if mac is not None:
        (entry / "address").write_text(f"{mac}\n")
        if assign_type is not None:
            (entry / "addr_assign_type").write_text(f"{assign_type}\n")
    if type_id is not None:
        (entry / "type").write_text(f"{type_id}\n")
    if not physical:
        return
    device = entry / "device"
    device.mkdir()
    if driver is not None:
        target = root.parent / "drivers" / driver
        target.mkdir(parents=True, exist_ok=True)
        (device / "driver").symlink_to(target, target_is_directory=True)


def test_collects_only_physical_interfaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Virtual interfaces are excluded and physical ones are sorted by name."""
    net = tmp_path / "net"
    net.mkdir()
    _make_interface(net, "eth1", physical=True, mac="aa:bb:cc", type_id="1", driver="e1000e")
    _make_interface(net, "eth0", physical=True, mac="dd:ee:ff", type_id="1")
    _make_interface(net, "lo", physical=False, mac="00:00:00", type_id="772")
    _make_interface(net, "docker0", physical=False)
    monkeypatch.setattr(network, "SYSFS_NET", str(net))

    result = network.COLLECTOR.collect()

    assert result.status == CollectorStatus.OK
    assert result.error == ""
    assert result.data == {
        "interfaces": [
            {"name": "eth0", "mac": "dd:ee:ff", "type": 1, "driver": ""},
            {"name": "eth1", "mac": "aa:bb:cc", "type": 1, "driver": "e1000e"},
        ]
    }


def test_dynamic_addresses_are_not_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Random, stolen, software-set or unproven addresses never enter the state."""
    net = tmp_path / "net"
    net.mkdir()
    _make_interface(net, "eth0", physical=True, mac="aa:bb:cc", assign_type="0")
    _make_interface(net, "eth1", physical=True, mac="11:22:33", assign_type="1")
    _make_interface(net, "eth2", physical=True, mac="44:55:66", assign_type="2")
    _make_interface(net, "eth3", physical=True, mac="77:88:99", assign_type="3")
    _make_interface(net, "eth4", physical=True, mac="de:ad:be", assign_type=None)
    monkeypatch.setattr(network, "SYSFS_NET", str(net))

    result = network.COLLECTOR.collect()

    assert result.status == CollectorStatus.OK
    assert result.data == {
        "interfaces": [
            {"name": "eth0", "mac": "aa:bb:cc", "type": 0, "driver": ""},
            {"name": "eth1", "mac": "", "type": 0, "driver": ""},
            {"name": "eth2", "mac": "", "type": 0, "driver": ""},
            {"name": "eth3", "mac": "", "type": 0, "driver": ""},
            {"name": "eth4", "mac": "", "type": 0, "driver": ""},
        ]
    }


def test_missing_sysfs_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing ``/sys/class/net`` marks the collector unavailable."""
    monkeypatch.setattr(network, "SYSFS_NET", str(tmp_path / "absent"))

    result = network.COLLECTOR.collect()

    assert result.status == CollectorStatus.UNAVAILABLE
    assert result.error
    assert result.data == {"interfaces": []}


def test_unreadable_fields_use_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing ``address``/``type``/driver files fall back to empty/zero values."""
    net = tmp_path / "net"
    net.mkdir()
    entry = net / "eth0"
    entry.mkdir()
    (entry / "device").mkdir()
    monkeypatch.setattr(network, "SYSFS_NET", str(net))

    result = network.COLLECTOR.collect()

    assert result.data == {"interfaces": [{"name": "eth0", "mac": "", "type": 0, "driver": ""}]}


def test_invalid_type_value_is_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-numeric ``type`` file degrades to ``0`` instead of failing."""
    net = tmp_path / "net"
    net.mkdir()
    _make_interface(net, "eth0", physical=True, type_id="not-a-number")
    monkeypatch.setattr(network, "SYSFS_NET", str(net))

    assert network.COLLECTOR.collect().data == {"interfaces": [{"name": "eth0", "mac": "", "type": 0, "driver": ""}]}


def test_empty_sysfs_is_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An existing but empty sysfs directory yields no interfaces and stays ok."""
    net = tmp_path / "net"
    net.mkdir()
    monkeypatch.setattr(network, "SYSFS_NET", str(net))

    result = network.COLLECTOR.collect()

    assert result.status == CollectorStatus.OK
    assert result.data == {"interfaces": []}
