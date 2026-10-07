"""Deterministic tests for the host collector."""

import pytest

from lion.state import host
from lion.state.collector import CollectorStatus


def test_collect_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Collect host identity from patched platform functions."""
    monkeypatch.setattr(host.platform, "freedesktop_os_release", lambda: {"NAME": "Fixture Linux", "VERSION_ID": "1.0"})
    monkeypatch.setattr(host.platform, "node", lambda: "lion-test")
    monkeypatch.setattr(host.platform, "release", lambda: "6.0.0")
    monkeypatch.setattr(host.platform, "machine", lambda: "x86_64")

    result = host.COLLECTOR.collect()

    assert result.status == CollectorStatus.OK
    assert result.data == {
        "hostname": "lion-test",
        "distribution": "Fixture Linux",
        "distribution_version": "1.0",
        "kernel": "6.0.0",
        "architecture": "x86_64",
    }


def test_missing_os_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing release file yields unknown distribution details."""

    def unavailable() -> dict[str, str]:
        raise OSError("unavailable")

    monkeypatch.setattr(host.platform, "freedesktop_os_release", unavailable)
    monkeypatch.setattr(host.platform, "node", lambda: "")
    monkeypatch.setattr(host.platform, "release", lambda: "")
    monkeypatch.setattr(host.platform, "machine", lambda: "")

    result = host.COLLECTOR.collect()

    assert result.status == CollectorStatus.OK
    assert result.data["distribution"] == result.data["distribution_version"] == "Unknown"
    assert result.data["hostname"] == result.data["kernel"] == result.data["architecture"] == "Unknown"
