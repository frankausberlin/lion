"""Deterministic tests for the packages collector."""

import subprocess
from pathlib import Path

import pytest

from repolion.state import packages, tools
from repolion.state.collector import CollectorStatus

DPKG_FIXTURE = """\
Package: bash
Status: install ok installed
Version: 5.2.21-2
Description: The GNU Bourne Again SHell
 continued description

Package: removed-package
Status: deinstall ok config-files
Version: 1.0

Package: libfoo
Status: install ok installed
Version: 1.0-1

Package: missing-version
Status: install ok installed
"""


class _Completed:
    """Minimal stand-in for :class:`subprocess.CompletedProcess`."""

    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


@pytest.fixture(autouse=True)
def _patch_dpkg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    status = tmp_path / "status"
    status.write_text(DPKG_FIXTURE)
    monkeypatch.setattr(packages, "DPKG_STATUS", status)


def _apt(monkeypatch: pytest.MonkeyPatch, selections: dict[str, str]) -> None:
    def fake_run(command: list[str], **kwargs: object) -> _Completed:
        return _Completed(0, selections.get(command[-1], ""))

    monkeypatch.setattr(tools.subprocess, "run", fake_run)


def test_collect_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse installed packages and sorted apt-mark selections."""
    _apt(monkeypatch, {"showmanual": "bash\nzsh\n", "showauto": "libfoo\nlibfoo\n", "showhold": "bash\n"})

    result = packages.COLLECTOR.collect()

    assert result.status == CollectorStatus.OK
    assert result.data == {
        "installed": {"bash": "5.2.21-2", "libfoo": "1.0-1"},
        "manual": ["bash", "zsh"],
        "auto": ["libfoo"],
        "held": ["bash"],
    }


def test_missing_dpkg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A missing Dpkg database makes the collector unavailable."""
    monkeypatch.setattr(packages, "DPKG_STATUS", tmp_path / "absent")
    _apt(monkeypatch, {})

    result = packages.COLLECTOR.collect()

    assert result.status == CollectorStatus.UNAVAILABLE
    assert result.error
    assert result.data == {"installed": {}, "manual": [], "auto": [], "held": []}


def test_missing_apt_mark(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing apt-mark makes the collector unavailable."""

    def fake_run(command: list[str], **kwargs: object) -> _Completed:
        raise OSError("missing")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)

    result = packages.COLLECTOR.collect()

    assert result.status == CollectorStatus.UNAVAILABLE
    assert result.error


def test_failing_apt_mark(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-zero apt-mark exit makes the collector unavailable."""

    def fake_run(command: list[str], **kwargs: object) -> _Completed:
        return _Completed(2, "")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)

    assert packages.COLLECTOR.collect().status == CollectorStatus.UNAVAILABLE


def test_apt_mark_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A timed-out apt-mark makes the collector unavailable."""

    def fake_run(command: list[str], **kwargs: object) -> _Completed:
        raise subprocess.TimeoutExpired(["apt-mark"], 1)

    monkeypatch.setattr(tools.subprocess, "run", fake_run)

    assert packages.COLLECTOR.collect().status == CollectorStatus.UNAVAILABLE


def test_empty_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty apt-mark output is a valid empty selection."""
    _apt(monkeypatch, {})
    result = packages.COLLECTOR.collect()
    assert result.status == CollectorStatus.OK
    assert result.data["manual"] == result.data["auto"] == result.data["held"] == []


@pytest.mark.parametrize("selection", ["install", "hold", "deinstall"])
def test_installed_selection_preserves_version(monkeypatch: pytest.MonkeyPatch, selection: str) -> None:
    """Selection changes must not remove a still-installed package."""
    packages.DPKG_STATUS.write_text(f"Package: bash\nStatus: {selection} ok installed\nVersion: 5.2\n")
    _apt(monkeypatch, {"showhold": "bash\n"} if selection == "hold" else {})
    result = packages.COLLECTOR.collect()
    assert result.data["installed"] == {"bash": "5.2"}


def test_multiarch_versions_remain_distinct(monkeypatch: pytest.MonkeyPatch) -> None:
    """Co-installed architectures retain independent, stable identities."""
    blocks = [
        f"Package: libfoo\nStatus: install ok installed\nArchitecture: {arch}\nVersion: 1.0\n"
        for arch in ["amd64", "i386"]
    ]
    _apt(monkeypatch, {})
    for ordered in [blocks, list(reversed(blocks))]:
        packages.DPKG_STATUS.write_text("\n".join(ordered))
        assert packages.COLLECTOR.collect().data["installed"] == {"libfoo:amd64": "1.0", "libfoo:i386": "1.0"}
    packages.DPKG_STATUS.write_text(blocks[0])
    assert packages.COLLECTOR.collect().data["installed"] == {"libfoo:amd64": "1.0"}


@pytest.mark.parametrize("indent", [" ", "\t"])
def test_description_continuations_do_not_override_fields(monkeypatch: pytest.MonkeyPatch, indent: str) -> None:
    """Field-like description text must not change package identity or state."""
    packages.DPKG_STATUS.write_text(
        "Package: example\nStatus: install ok installed\nArchitecture: amd64\nVersion: 1.0\n"
        "Description: example package\n"
        f"{indent}Package: wrong\n{indent}Version: 9.9\n{indent}Architecture: i386\n"
        f"{indent}Status: deinstall ok config-files\n"
    )
    _apt(monkeypatch, {})
    assert packages.COLLECTOR.collect().data["installed"] == {"example:amd64": "1.0"}
