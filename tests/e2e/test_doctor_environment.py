"""End-to-end ``doctor`` checks for the environment, storage and history paths.

These tests exercise the installed ``lion`` executable and a real filesystem in
the disposable container, complementing the mocked ``tests/test_doctor.py``.
The bare image deliberately lacks ``lspci``/``nvidia-smi``, so the hardware and
``lspci`` findings are deterministic.
"""

import json
import shutil
import stat
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from lion import __version__

pytestmark = pytest.mark.e2e

_CANARY = "LION_E2E_CANARY"


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict), value
    return cast("dict[str, object]", value)


def _findings(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    raw = payload["befunde"]
    assert isinstance(raw, list), raw
    result: dict[str, dict[str, object]] = {}
    for item in cast("list[object]", raw):
        finding = _mapping(item)
        result[str(finding["name"])] = finding
    return result


def _status(payload: dict[str, object], name: str) -> str:
    status = _findings(payload)[name]["status"]
    assert isinstance(status, str)
    return status


def _reco_scripts(data: Path) -> list[Path]:
    recos = data / "lion" / "recos"
    return sorted(recos.glob("*.sh")) if recos.is_dir() else []


def test_bare_profile_publishes_one_reco(runner: Callable[..., str], env: dict[str, str], tmp_path: Path) -> None:
    """The bare container yields a deterministic profile and exactly one reco."""
    assert shutil.which("lspci") is None
    assert shutil.which("nvidia-smi") is None

    payload = _mapping(json.loads(runner("lion", "doctor", "--json", env=env)))
    assert payload["status"] == "warn"

    for name in (
        "collectors.host",
        "collectors.packages",
        "collectors.tools",
        "tools.apt_mark",
        "storage.data_dir",
        "storage.history",
        "storage.recos",
    ):
        assert _status(payload, name) == "ok", name
    assert _status(payload, "collectors.hardware") == "warn"
    assert _status(payload, "tools.lspci") == "warn"
    assert _findings(payload)["tools.lspci"]["commands"] == ["sudo apt install pciutils"]
    for name in ("tools.nvidia_smi", "tools.rocm_smi", "tools.zsh", "history.entries", "shlib.installed"):
        assert _status(payload, name) == "skip", name

    scripts = _reco_scripts(tmp_path / "data")
    assert len(scripts) == 1
    assert stat.S_IMODE(scripts[0].stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "data" / "lion" / "recos").stat().st_mode) == 0o700
    content = scripts[0].read_text(encoding="utf-8")
    assert "#!/usr/bin/env bash" in content
    assert "sudo apt install pciutils" in content
    assert "führt dieses Skript niemals aus" in content
    assert f"# LION: {__version__}" in content
    assert "# Host: " in content


def test_json_is_pure_and_show_prints_text(runner: Callable[..., str], env: dict[str, str]) -> None:
    """``--json --show`` stays pure JSON; ``--show`` prints the recipe as text."""
    json_output = runner("lion", "doctor", "--json", "--show", env=env)
    assert isinstance(json.loads(json_output), dict)
    assert "#!/usr/bin/env bash" not in json_output

    text_output = runner("lion", "doctor", "--show", env=env)
    assert "#!/usr/bin/env bash" in text_output
    assert "VOR DEM AUSFÜHREN KOMPLETT LESEN" in text_output


def test_read_only_run_never_creates_history(runner: Callable[..., str], env: dict[str, str], tmp_path: Path) -> None:
    """A warn run publishes recos/ but never creates history/ or its lock."""
    runner("lion", "doctor", "--json", env=env)

    lion_data = tmp_path / "data" / "lion"
    assert (lion_data / "recos").is_dir()
    assert not (lion_data / "history").exists()
    assert not (lion_data / ".history.lock").exists()


def test_hostile_filename_stays_comment_only(runner: Callable[..., str], env: dict[str, str], tmp_path: Path) -> None:
    """A newline/command filename cannot inject an executable reco line."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake_lspci = bindir / "lspci"
    fake_lspci.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_lspci.chmod(0o755)
    env = {**env, "PATH": f"{bindir}:{env['PATH']}"}

    history = tmp_path / "data" / "lion" / "history"
    history.mkdir(parents=True)
    (history / f"evil\n$(touch {_CANARY})\n#x.toml").write_text("broken = [", encoding="utf-8")

    payload = _mapping(json.loads(runner("lion", "doctor", "--json", env=env, expected=1)))
    assert payload["status"] == "error"
    assert not Path(_CANARY).exists()
    assert not (tmp_path / _CANARY).exists()

    scripts = _reco_scripts(tmp_path / "data")
    assert len(scripts) == 1
    allowed = {"", "#!/usr/bin/env bash", "set -euo pipefail"}
    for line in scripts[0].read_text(encoding="utf-8").splitlines():
        assert line in allowed or line.startswith("#"), f"unexpected executable line: {line!r}"


def test_second_run_never_overwrites(runner: Callable[..., str], env: dict[str, str], tmp_path: Path) -> None:
    """Two runs produce two recos and leave the first byte-identical."""
    runner("lion", "doctor", "--json", env=env)
    first_run = _reco_scripts(tmp_path / "data")
    assert len(first_run) == 1
    first = first_run[0]
    before = first.read_bytes()

    runner("lion", "doctor", "--json", env=env)
    scripts = _reco_scripts(tmp_path / "data")
    assert len(scripts) == 2
    assert first.read_bytes() == before
    assert all(stat.S_IMODE(script.stat().st_mode) == 0o700 for script in scripts)


def test_broken_history_entry_is_error(runner: Callable[..., str], env: dict[str, str], tmp_path: Path) -> None:
    """A real scan plus one damaged entry: exit 1, valid entry still reported ok."""
    runner("lion", "scan", "--json", env=env)
    history = tmp_path / "data" / "lion" / "history"
    assert len(list(history.glob("*.toml"))) == 1
    (history / "broken.toml").write_text("broken = [", encoding="utf-8")

    payload = _mapping(json.loads(runner("lion", "doctor", "--json", env=env, expected=1)))
    assert _status(payload, "history.entries") == "ok"
    assert _status(payload, "history.broken.toml") == "error"
    assert "broken.toml" in str(_findings(payload)["history.broken.toml"]["message"])
    assert _reco_scripts(tmp_path / "data")


def test_legacy_scans_directory_warns(runner: Callable[..., str], env: dict[str, str], tmp_path: Path) -> None:
    """A leftover ``scans/`` directory is a warning, not an error."""
    (tmp_path / "data" / "lion" / "scans").mkdir(parents=True)

    payload = _mapping(json.loads(runner("lion", "doctor", "--json", env=env)))
    assert _status(payload, "history.scans") == "warn"


def test_unusable_data_path_is_error(runner: Callable[..., str], env: dict[str, str], tmp_path: Path) -> None:
    """A data directory path that is a regular file is a hard storage error."""
    data_file = tmp_path / "datafile"
    data_file.write_text("not a directory", encoding="utf-8")
    env = {**env, "XDG_DATA_HOME": str(data_file)}

    payload = _mapping(json.loads(runner("lion", "doctor", "--json", env=env, expected=1)))
    assert _status(payload, "storage.data_dir") == "error"
