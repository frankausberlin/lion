"""Deterministic tests for the services collector."""

import pytest

from lion.state import services
from lion.state.collector import CollectorStatus

LIST_UNIT_FILES = ["systemctl", "list-unit-files", "--type=service", "--no-pager", "--no-legend"]

OUTPUT = """\
ssh.service          enabled
cron.service         disabled
invented.service     some-new-state
not-a-service        enabled
dbus.socket          enabled
"""


def _fake_run(monkeypatch: pytest.MonkeyPatch, output: str | None) -> list[list[str]]:
    """Patch ``run_tool``: record calls and return ``output`` (or fail)."""
    calls: list[list[str]] = []

    def fake(command: list[str], *, timeout: int, failures: list[str] | None = None) -> str | None:
        calls.append(list(command))
        if output is None:
            if failures is not None:
                failures.append("systemctl: executable not found")
            return None
        return output

    monkeypatch.setattr(services, "run_tool", fake)
    return calls


def test_parses_known_and_unknown_states(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only ``*.service`` lines are kept; unknown states normalize to ``unknown``."""
    calls = _fake_run(monkeypatch, OUTPUT)

    result = services.COLLECTOR.collect()

    assert result.status == CollectorStatus.OK
    assert result.error == ""
    assert result.data == {
        "units": [
            {"name": "cron.service", "state": "disabled"},
            {"name": "invented.service", "state": "unknown"},
            {"name": "ssh.service", "state": "enabled"},
        ]
    }
    assert calls == [LIST_UNIT_FILES]


def test_missing_systemctl_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing ``systemctl`` marks the collector unavailable with a reason."""
    _fake_run(monkeypatch, None)

    result = services.COLLECTOR.collect()

    assert result.status == CollectorStatus.UNAVAILABLE
    assert result.error == "systemctl: executable not found"
    assert result.data == {"units": []}


def test_empty_output_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """No unit files is a valid empty selection."""
    _fake_run(monkeypatch, "")

    result = services.COLLECTOR.collect()

    assert result.status == CollectorStatus.OK
    assert result.data == {"units": []}


def test_uses_fixed_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """The command is fixed code, not influenced by any external input."""
    calls = _fake_run(monkeypatch, "")
    services.COLLECTOR.collect()
    assert calls[0][0] == "systemctl"
