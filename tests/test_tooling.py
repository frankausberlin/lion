"""Deterministic tests for the external-tool availability collector."""

import pytest

from lion.state import tooling
from lion.state.collector import CollectorStatus


def _fake_which(monkeypatch: pytest.MonkeyPatch, present: set[str]) -> None:
    def which(command: str) -> str | None:
        return f"/usr/bin/{command}" if command in present else None

    monkeypatch.setattr(tooling.shutil, "which", which)


def test_reports_availability_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every known tool is reported as a boolean keyed by its normalized name."""
    _fake_which(monkeypatch, {"lspci", "zsh"})
    result = tooling.COLLECTOR.collect()
    assert result.status == CollectorStatus.OK
    assert result.error == ""
    assert result.data == {
        "available": {
            "lspci": True,
            "nvidia_smi": False,
            "rocm_smi": False,
            "apt_mark": False,
            "zsh": True,
        }
    }


def test_missing_tools_are_data_not_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing tool keeps the collector ``ok`` because measuring succeeded."""
    _fake_which(monkeypatch, set())
    result = tooling.COLLECTOR.collect()
    assert result.status == CollectorStatus.OK
    assert result.error == ""
    assert result.data == {
        "available": {
            "lspci": False,
            "nvidia_smi": False,
            "rocm_smi": False,
            "apt_mark": False,
            "zsh": False,
        }
    }


def test_all_tools_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """When every tool exists the collector reports them all available."""
    _fake_which(monkeypatch, {"lspci", "nvidia-smi", "rocm-smi", "apt-mark", "zsh"})
    available = tooling.COLLECTOR.collect().data["available"]
    assert available == {
        "lspci": True,
        "nvidia_smi": True,
        "rocm_smi": True,
        "apt_mark": True,
        "zsh": True,
    }
