"""Optional Gitleaks stage tests with a fake binary."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

import lion.doctor.gitleaks as gitleaks_module
from lion.cli import app
from lion.doctor import collect_diagnosis
from lion.doctor.render import render_report
from lion.shlib.layout import ShlibLayout

runner = CliRunner()
TOKEN = "aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW"


def _fake_binary(directory: Path, *, report: str = "[]", exit_code: int = 0, sleep: bool = False) -> Path:
    """Write a fake ``gitleaks`` executable."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "gitleaks"
    body = ["#!/bin/sh", 'if [ "$1" = "version" ]; then', "  echo 8.18.0", "  exit 0", "fi"]
    if sleep:
        body.extend(["sleep 1", "exit 0"])
    else:
        body.extend([f"printf '%s' '{report}'", f"exit {exit_code}"])
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _fake_path(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    """Put the fake binary first in PATH."""
    monkeypatch.setenv("PATH", f"{directory}:{os.environ['PATH']}")


def _payload(*, rule: str = "generic-api-key", start: int = 9, end: int = 40) -> str:
    """Return a Gitleaks JSON report without a secret or match field."""
    return json.dumps(
        [
            {
                "RuleID": rule,
                "StartLine": 1,
                "StartColumn": start,
                "EndLine": 1,
                "EndColumn": end,
            }
        ]
    )


def test_deep_scan_maps_a_position_to_a_line(
    installed: ShlibLayout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid fake report becomes a metadata-only finding."""
    _fake_binary(tmp_path / "bin", report=_payload())
    _fake_path(monkeypatch, tmp_path / "bin")
    (installed.shlibs / "10-api.sh").write_text(f"value = {TOKEN}\n", encoding="utf-8")

    report = collect_diagnosis(installed, deep=True)
    finding = next(finding for finding in report.findings if finding.code == "shlibs.possible_secret")

    assert finding.line == 1
    assert "generic-api-key" in finding.summary
    assert "confidence 85%" in finding.summary
    assert TOKEN not in finding.summary
    assert TOKEN not in repr(report.findings)
    assert TOKEN not in "\n".join(render_report(report, color=False))


def test_missing_binary_is_info_and_does_not_abort(
    installed: ShlibLayout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing gitleaks is an info finding and stages 1-2 still run."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    (installed.shlibs / "10-api.sh").write_text(f"value = {TOKEN}\n", encoding="utf-8")

    report = collect_diagnosis(installed, deep=True)
    finding = next(finding for finding in report.findings if finding.code == "gitleaks.unavailable")

    assert finding.severity == "info"
    assert report.exit_code == 0


def test_invalid_json_is_a_warning(installed: ShlibLayout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid JSON becomes gitleaks.error without scanner output."""
    _fake_binary(tmp_path / "bin", report="not json")
    _fake_path(monkeypatch, tmp_path / "bin")
    (installed.shlibs / "10-api.sh").write_text("x\n", encoding="utf-8")

    report = collect_diagnosis(installed, deep=True)
    finding = next(finding for finding in report.findings if finding.code == "gitleaks.error")

    assert finding.severity == "warning"
    assert "not json" not in finding.summary


def test_non_zero_exit_is_a_warning(installed: ShlibLayout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing scanner is reported and the rest of doctor keeps running."""
    _fake_binary(tmp_path / "bin", exit_code=2)
    _fake_path(monkeypatch, tmp_path / "bin")
    (installed.shlibs / "10-api.sh").write_text("x\n", encoding="utf-8")

    report = collect_diagnosis(installed, deep=True)
    finding = next(finding for finding in report.findings if finding.code == "gitleaks.error")

    assert "status 2" in finding.summary
    assert report.exit_code == 1


def test_timeout_is_a_warning(installed: ShlibLayout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A scanner timeout becomes a warning instead of an exception."""
    _fake_binary(tmp_path / "bin", sleep=True)
    _fake_path(monkeypatch, tmp_path / "bin")
    monkeypatch.setattr(gitleaks_module, "SCAN_TIMEOUT_SECONDS", 0.1)
    (installed.shlibs / "10-api.sh").write_text("x\n", encoding="utf-8")

    report = collect_diagnosis(installed, deep=True)
    finding = next(finding for finding in report.findings if finding.code == "gitleaks.error")

    assert "timed out" in finding.summary


def test_old_version_is_unsupported(installed: ShlibLayout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An older gitleaks is skipped with a warning."""
    path = tmp_path / "bin" / "gitleaks"
    path.parent.mkdir()
    path.write_text("#!/bin/sh\necho 7.6.1\n", encoding="utf-8")
    path.chmod(0o755)
    _fake_path(monkeypatch, tmp_path / "bin")

    report = collect_diagnosis(installed, deep=True)

    assert "gitleaks.unsupported" in {finding.code for finding in report.findings}


def test_symlink_entry_gets_the_follow_option(
    installed: ShlibLayout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symlinked entry is passed with --follow-symlinks and kept as path."""
    args_file = tmp_path / "args"
    script = f"""#!/bin/sh
if [ "$1" = "version" ]; then
  echo 8.18.0
  exit 0
fi
printf '%s\\n' "$@" > '{args_file}'
printf '%s' '{_payload(start=1, end=5)}'
exit 0
"""
    binary = tmp_path / "bin" / "gitleaks"
    binary.parent.mkdir()
    binary.write_text(script, encoding="utf-8")
    binary.chmod(0o755)
    _fake_path(monkeypatch, tmp_path / "bin")
    target = tmp_path / "target"
    target.write_text("abcd\n", encoding="utf-8")
    entry = installed.shlibs / "10-api.sh"
    entry.symlink_to(target)

    report = collect_diagnosis(installed, deep=True)
    finding = next(finding for finding in report.findings if finding.code == "shlibs.possible_secret")

    assert "--follow-symlinks" in args_file.read_text(encoding="utf-8").splitlines()
    assert finding.path == entry


def test_fake_binary_is_not_called_without_deep(
    installed: ShlibLayout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default doctor run never starts Gitleaks."""
    marker = tmp_path / "called"
    script = f"""#!/bin/sh
if [ "$1" = "version" ]; then
  echo 8.18.0
  exit 0
fi
touch '{marker}'
printf '[]'
"""
    binary = tmp_path / "bin" / "gitleaks"
    binary.parent.mkdir()
    binary.write_text(script, encoding="utf-8")
    binary.chmod(0o755)
    _fake_path(monkeypatch, tmp_path / "bin")

    collect_diagnosis(installed)

    assert not marker.exists()


def test_deep_and_fix_are_compatible(installed: ShlibLayout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Combining --deep with --fix does not abort."""
    _fake_binary(tmp_path / "bin")
    _fake_path(monkeypatch, tmp_path / "bin")
    installed.redaction_key.unlink()

    result = runner.invoke(app, ["doctor", "--home", str(installed.home), "--deep", "--fix"])

    assert result.exit_code == 0
    assert installed.redaction_key.exists()


def test_empty_security_section_uses_best_effort_text(installed: ShlibLayout) -> None:
    """A clean security section no longer claims 'ok'."""
    report = collect_diagnosis(installed)
    rendered = "\n".join(render_report(report, color=False))

    assert "no suspected secrets found (best effort)" in rendered
