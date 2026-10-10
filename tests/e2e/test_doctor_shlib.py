"""End-to-end ``doctor`` checks against a real ``lion shlib`` installation.

The tests install the real shell library into a temporary home and then let
``doctor`` inspect the resulting on-disk state, so the ``shlib.*`` findings are
exercised without mocks.
"""

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from lion.program.shlib import START

pytestmark = pytest.mark.e2e


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict), value
    return cast("dict[str, object]", value)


def _findings(stdout: str) -> dict[str, dict[str, object]]:
    raw = _mapping(json.loads(stdout))["findings"]
    assert isinstance(raw, list), raw
    result: dict[str, dict[str, object]] = {}
    for item in cast("list[object]", raw):
        finding = _mapping(item)
        result[str(finding["name"])] = finding
    return result


def _status(stdout: str, name: str) -> str:
    status = _findings(stdout)[name]["status"]
    assert isinstance(status, str)
    return status


def test_consistent_installation_is_clean(runner: Callable[..., str], env: dict[str, str]) -> None:
    """A fresh installation reports ``shlib.installed ok`` and no shlib warnings."""
    runner("lion", "shlib", "install", env=env)

    stdout = runner("lion", "doctor", "--json", env=env)
    findings = _findings(stdout)
    assert _status(stdout, "shlib.installed") == "ok"
    assert "shlib.lock" not in findings
    assert "shlib.warnings" not in findings


def test_missing_reference_copy_warns(runner: Callable[..., str], env: dict[str, str]) -> None:
    """Deleting ``~/.zshrc.lock`` yields a ``shlib.lock warn`` about the missing copy."""
    home = Path(env["HOME"])
    runner("lion", "shlib", "install", env=env)
    (home / ".zshrc.lock").unlink()

    stdout = runner("lion", "doctor", "--json", env=env)
    assert _status(stdout, "shlib.lock") == "warn"
    assert "missing" in str(_findings(stdout)["shlib.lock"]["message"])


def test_drifted_rc_warns(runner: Callable[..., str], env: dict[str, str]) -> None:
    """Editing ``~/.zshrc`` after installation yields a ``shlib.lock warn``."""
    home = Path(env["HOME"])
    runner("lion", "shlib", "install", env=env)
    rc = home / ".zshrc"
    rc.write_text(rc.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")

    stdout = runner("lion", "doctor", "--json", env=env)
    assert _status(stdout, "shlib.lock") == "warn"
    assert "differs" in str(_findings(stdout)["shlib.lock"]["message"])


def test_missing_directory_warns(runner: Callable[..., str], env: dict[str, str]) -> None:
    """Removing ``~/.shlib/exports`` yields a ``shlib.warnings warn``."""
    home = Path(env["HOME"])
    runner("lion", "shlib", "install", env=env)
    shutil.rmtree(home / ".shlib" / "exports")

    stdout = runner("lion", "doctor", "--json", env=env)
    assert _status(stdout, "shlib.warnings") == "warn"


def test_dash_defects_warn(runner: Callable[..., str], env: dict[str, str]) -> None:
    """A regular file and a broken symlink in ``dash/`` are each a warning."""
    home = Path(env["HOME"])
    dash = home / ".shlib" / "dash"
    dash.mkdir(parents=True)
    (dash / "plain.conf").write_text("plain", encoding="utf-8")
    (dash / "broken.conf").symlink_to(home / "missing")

    stdout = runner("lion", "doctor", "--json", env=env)
    assert _status(stdout, "shlib.dash.plain.conf") == "warn"
    assert _status(stdout, "shlib.dash.broken.conf") == "warn"


def test_ambiguous_markers_are_error(runner: Callable[..., str], env: dict[str, str]) -> None:
    """Two start markers make the shlib status unreadable: error and exit 1."""
    home = Path(env["HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / ".zshrc").write_text(f"{START}\n{START}\n", encoding="utf-8")

    stdout = runner("lion", "doctor", "--json", env=env, expected=1)
    assert _status(stdout, "shlib.status") == "error"


def test_foreign_zdotdir(runner: Callable[..., str], env: dict[str, str], tmp_path: Path) -> None:
    """A foreign ZDOTDIR is skip without an install and error with one."""
    other = tmp_path / "other"
    foreign = {**env, "ZDOTDIR": str(other)}

    stdout = runner("lion", "doctor", "--json", env=foreign)
    assert _status(stdout, "shlib.home") == "skip"

    runner("lion", "shlib", "install", env=env)
    stdout = runner("lion", "doctor", "--json", env=foreign, expected=1)
    assert _status(stdout, "shlib.home") == "error"
